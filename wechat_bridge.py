"""微信 iLink Bot 桥接进程：把微信消息转发到 yuki，再把回复写回。

架构：
    手机微信 → yuki ClawBot 账号 → ilinkai.weixin.qq.com 长轮询
                                           ↓
                              本脚本（weixin-ilink SDK）
                                           ↓
                           POST http://127.0.0.1:3616/api/wechat_chat
                                           ↓
                                   yuki Agent + 工具
                                           ↓
                                       回复文本
                                           ↓
                       bot.send_text(user_id, 去 markdown + 分段)
                                           ↓
                                   手机微信收到回复

特性：
- 复用 master 对话上下文（跨端共享记忆 - 决策 A）
- 灵活启动: launcher.py 内置（YUKI_WECHAT_AUTOSTART=1）或独立 wechat_bridge.bat（决策 C）
- 回复去 markdown（微信不渲染），保段落（决策 B）
- 长回复自动分段（每段 ≤ 500 字，间隔 0.8 秒，避免限流）
- 可选 ACL 白名单（环境变量 ``YUKI_WECHAT_ALLOW`` 逗号分隔 user_id 前缀）
- 凭证缓存 ``.wechat_creds.json``，重启免扫码

环境变量:
    YUKI_API_BASE   yuki server 地址（默认 http://127.0.0.1:3616）
    YUKI_WECHAT_ALLOW  逗号分隔的允许 user_id 前缀；空 = 允许所有
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

# weixin-ilink SDK
try:
    from weixin_ilink import WeixinBot
    from weixin_ilink.markdown import filter_markdown
except ImportError:
    print("[ERROR] weixin-ilink 未安装。运行: pip install weixin-ilink", file=sys.stderr)
    sys.exit(1)

import requests

# ── 配置 ────────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
CREDS_PATH = HERE / ".wechat_creds.json"
YUKI_API_BASE = os.environ.get("YUKI_API_BASE", "http://127.0.0.1:3616").rstrip("/")
ALLOWED_PREFIXES = tuple(
    p.strip() for p in (os.environ.get("YUKI_WECHAT_ALLOW") or "").split(",") if p.strip()
)

# 单条微信消息最大字符数（超过分段发，留点 buffer 避免限流）
CHUNK_SIZE = 500
# 段间间隔（秒），太频繁微信会限流
CHUNK_DELAY = 0.8
# yuki API 单次请求超时（一次复杂工具调可能跑几十秒）
YUKI_TIMEOUT = 180


# ── 工具函数 ────────────────────────────────────────────────────────────────

def _is_allowed(user_id: str) -> bool:
    """ACL: ALLOWED_PREFIXES 空时全放，否则只允许 user_id 以任一前缀开头。"""
    if not ALLOWED_PREFIXES:
        return True
    return any(user_id.startswith(p) for p in ALLOWED_PREFIXES)


def _to_plain_text(md: str) -> str:
    """微信不渲染 markdown。用 SDK 自带 filter_markdown 去标记。

    保留段落 / 换行 / 中文标点 / URL；去掉 ** / * / # / ``` 等标记字符。
    失败时退回原文（不阻塞回复）。
    """
    try:
        return filter_markdown(md or "").strip()
    except Exception:
        return (md or "").strip()


def _chunk(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """按字符切片，优先在段落 / 句子边界切。

    简化版：先按 \\n\\n 切（段落），单段超 size 时按 \\n / 标点继续切，
    最后兜底 hard-split。
    """
    if not text:
        return []
    if len(text) <= size:
        return [text]

    out: list[str] = []
    buf = ""

    def flush():
        nonlocal buf
        if buf.strip():
            out.append(buf.rstrip())
        buf = ""

    for para in text.split("\n\n"):
        if not para.strip():
            continue
        # 当前 buf + 这段还能塞下 → 拼一起
        candidate = (buf + "\n\n" + para) if buf else para
        if len(candidate) <= size:
            buf = candidate
            continue
        # 单段就超 size → flush 当前 buf，本段再细切
        flush()
        if len(para) <= size:
            buf = para
        else:
            # 按句号 / 换行硬切
            cur = ""
            for token in _resplit_long(para):
                if len(cur) + len(token) <= size:
                    cur += token
                else:
                    if cur:
                        out.append(cur.rstrip())
                    cur = token
            if cur.strip():
                out.append(cur.rstrip())
    flush()
    return out


def _resplit_long(s: str) -> list[str]:
    """长段落按"句末标点 / 换行"切成 token 列表（保留分隔符）。"""
    import re as _re
    # 在中英文句末 / 换行后切；保留分隔符
    parts = _re.split(r"([。！？!?\n])", s)
    out: list[str] = []
    buf = ""
    for p in parts:
        buf += p
        if p in ("。", "！", "？", "!", "?", "\n"):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _post_yuki(user_id: str, text: str) -> Optional[str]:
    """同步 POST yuki 的 /api/wechat_chat，返回 reply 文本或 None。"""
    url = f"{YUKI_API_BASE}/api/wechat_chat"
    try:
        r = requests.post(
            url,
            json={"user_id": user_id, "text": text},
            timeout=YUKI_TIMEOUT,
        )
    except requests.exceptions.ConnectionError:
        return "❌ yuki 服务器没启动（启动 launcher.py 或 server.py 后再试）"
    except requests.exceptions.Timeout:
        return "⚠️ yuki 处理超时（任务可能太重，稍后重试或拆分问题）"
    except Exception as e:
        return f"⚠️ 调用失败: {type(e).__name__}: {e}"

    if r.status_code >= 400:
        body = r.text[:200]
        return f"⚠️ yuki 返回 {r.status_code}: {body}"

    try:
        data = r.json()
    except Exception:
        return f"⚠️ yuki 响应非 JSON: {r.text[:200]}"

    reply = (data.get("reply") or "").strip()
    if not reply:
        return "(yuki 没说话)"
    if data.get("truncated"):
        reply += '\n\n[提示] 这轮工具调用撞了 60 次上限，可能没回答完；在桌面端按「继续」按钮接着跑。'
    return reply


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 55)
    print("  yuki × 微信 iLink Bot 桥接")
    print("=" * 55)
    print(f"  yuki API: {YUKI_API_BASE}")
    if ALLOWED_PREFIXES:
        print(f"  ACL 白名单前缀: {ALLOWED_PREFIXES}")
    else:
        print("  ACL: 允许所有微信用户（如要限制 → set YUKI_WECHAT_ALLOW=<前缀>）")
    print(f"  凭证: {CREDS_PATH}")
    print()

    if CREDS_PATH.exists():
        print("[i] 复用本地凭证")
    else:
        print("[i] 首次启动，扫码登录…")
        print("    手机微信 → 我 → 设置 → 插件 → 微信 ClawBot")
    print()

    try:
        bot = WeixinBot.from_login(save_to=str(CREDS_PATH))
    except Exception as e:
        print(f"[ERROR] 登录失败: {type(e).__name__}: {e}")
        return 1

    print(f"[OK] bot running — account={bot.account_id}")
    print("    现在用手机微信对 yuki bot 说话即可")
    print()

    @bot.on_text
    def handle_text(msg):
        try:
            user = getattr(msg, "from_user", None) or getattr(msg, "sender", "?")
            text = (msg.text or "").strip()
            if not text:
                return

            if not _is_allowed(user):
                print(f"[ACL] 拒绝 {user!r}: {text[:30]!r}")
                msg.reply_text("（你不在 yuki 的白名单内）")
                return

            print(f"[<<] {user[:12]}.. : {text[:60]!r}")

            # 让对方看到正在处理（typing 状态）
            try:
                bot.send_typing(user)
            except Exception:
                pass

            reply = _post_yuki(user, text)
            if not reply:
                msg.reply_text("(yuki 没返回内容)")
                return

            plain = _to_plain_text(reply)
            chunks = _chunk(plain)
            print(f"[>>] {user[:12]}.. : {len(plain)} chars / {len(chunks)} chunk(s)")

            for i, c in enumerate(chunks):
                try:
                    bot.send_text(user, c)
                except Exception as e:
                    print(f"[!!] send_text 第 {i+1}/{len(chunks)} 段失败: {e}")
                    break
                if i < len(chunks) - 1:
                    time.sleep(CHUNK_DELAY)
        except Exception as e:
            print(f"[ERROR] handle_text: {type(e).__name__}: {e}")
            try:
                msg.reply_text(f"❌ 桥接异常: {type(e).__name__}: {str(e)[:120]}")
            except Exception:
                pass

    @bot.on_image
    def handle_image(msg):
        # 图片支持留待后续：可调 vision_describe 让 yuki 看图
        try:
            user = getattr(msg, "from_user", None) or "?"
            msg.reply_text(
                "📷 收到图片，但桥接器还没接上视觉模型路径。"
                "暂时只能用文字交流，或者在桌面端发图片让 yuki 看。"
            )
            print(f"[img] {user[:12]}.. -> 已告知暂不支持")
        except Exception:
            pass

    @bot.on_voice
    def handle_voice(msg):
        try:
            msg.reply_text("🎙️ 暂不支持语音，请用文字。")
        except Exception:
            pass

    @bot.on_file
    def handle_file(msg):
        try:
            msg.reply_text("📎 暂不支持文件，桌面端把文件传给 yuki 更顺手。")
        except Exception:
            pass

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n[i] 退出中…")
    finally:
        try:
            bot.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
