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

import re
import requests

# weixin-ilink 的 bot.run() 内部会调 signal.signal() 注册 SIGINT 处理器
# 优雅停止。但 Python 的 signal.signal 只能在主线程跑，否则抛
# ValueError: signal only works in main thread of the main interpreter。
# yuki.exe 自启动桥接时桥接跑在 daemon 线程里 → 炸。
# 包一层守卫: 主线程调正常，非主线程静默 no-op（反正非主线程也接不到信号）。
# 必须放在 from weixin_ilink import ... 之前。
import signal as _signal
import threading as _threading
_orig_signal_signal = _signal.signal
def _signal_main_only(signum, handler):
    if _threading.current_thread() is _threading.main_thread():
        return _orig_signal_signal(signum, handler)
    return None
_signal.signal = _signal_main_only

# weixin-ilink SDK
try:
    from weixin_ilink import WeixinBot
except ImportError:
    print("[ERROR] weixin-ilink 未安装。运行: pip install weixin-ilink", file=sys.stderr)
    sys.exit(1)

# ── 配置 ────────────────────────────────────────────────────────────────────

# 注意 frozen 模式: __file__ 在 _internal/ 里，不是 yuki.exe 旁边。
# 真正的数据目录（凭证 / 日志）应该跟着 yuki.exe，不能跟着模块文件，否则:
# 1) 凭证存在但桥接找不到 → 误以为没扫码 → 卡在扫码路径
# 2) --noconsole 时 print 到无 → 用户完全看不到出错原因
if getattr(sys, "frozen", False):
    # 跟 paths.py 的 _resolve_data_root 同思路：sys.executable 旁
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent
CREDS_PATH = HERE / ".wechat_creds.json"
LOG_PATH = HERE / ".wechat_bridge.log"


def _setup_frozen_logging() -> None:
    """frozen + --noconsole 时把 stdout/stderr 重定向到日志文件。

    yuki.exe 跑桥接线程时没控制台，print 写到无（或抛 ValueError if sys.stdout is None）。
    重定向到 .wechat_bridge.log，主人想看就 tail 这个文件。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)  # line-buffered
        sys.stdout = f
        sys.stderr = f
        # 标记本次启动
        import datetime as _dt
        f.write(f"\n=== {_dt.datetime.now().isoformat()} bridge thread started ===\n")
        f.flush()
    except Exception:
        pass  # 日志开不了也别让桥接挂


def _safe_api_base() -> str:
    """读 YUKI_API_BASE，做防御性校验。

    历史上有过 .bat 在 GBK 中文 cmd 里 echo 一行示例 `set YUKI_API_BASE=http://其他地址`
    被错误解码成真 `set` 命令的事故，env 里塞了带中文 / 空格的烂 URL。
    校验失败时回退到默认 127.0.0.1:3616 并打印警告，不让烂 URL 把每次请求都打挂。
    """
    raw = (os.environ.get("YUKI_API_BASE") or "").strip().rstrip("/")
    if not raw:
        return "http://127.0.0.1:3616"
    # 基本结构 + ASCII-only 校验（中文域名 / 空格肯定是 bug）
    if not raw.startswith(("http://", "https://")):
        print(f"[WARN] YUKI_API_BASE 不是 http(s):// 开头: {raw!r}，回退默认", file=sys.stderr)
        return "http://127.0.0.1:3616"
    try:
        raw.encode("ascii")
    except UnicodeEncodeError:
        print(f"[WARN] YUKI_API_BASE 含非 ASCII 字符: {raw!r}，回退默认", file=sys.stderr)
        return "http://127.0.0.1:3616"
    if " " in raw:
        print(f"[WARN] YUKI_API_BASE 含空格: {raw!r}，回退默认", file=sys.stderr)
        return "http://127.0.0.1:3616"
    return raw


YUKI_API_BASE = _safe_api_base()
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
    """微信不渲染 markdown，转成 WeChat-friendly 纯文本。

    SDK 自带的 filter_markdown 几乎是 no-op（只去单星号斜体），其他标记
    （**加粗** / # 标题 / `code` / 代码块 / - 列表 / [链接]）一律保留 ——
    手机端看到一堆 ** ## - ``` 符号体验差。这里自己做完整转换。

    规则:
    - 三反引号代码块 → 只留内容（去 fence + 语言标签）
    - 行内 `code` → code
    - **加粗** / __加粗__ → 加粗
    - *斜体* / _斜体_ → 斜体（避免误删 **）
    - # 标题 → 标题（去 # 号）
    - - / * / + 列表 → • 列表（• 在微信渲染干净）
    - [text](url) → text（url）— 保留可点击 / 可复制
    - 折叠 ≥3 个连续换行
    """
    if not md:
        return ""
    s = md
    s = re.sub(r"```(?:[a-zA-Z0-9_+-]*)\n?", "", s)
    s = s.replace("```", "")
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", s)
    s = re.sub(r"__([^_\n]+?)__", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", s)
    s = re.sub(r"(?<![_a-zA-Z0-9])_([^_\n]+?)_(?![_a-zA-Z0-9])", r"\1", s)
    s = re.sub(r"^[#]{1,6}\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", s, flags=re.MULTILINE)
    s = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", r"\1（\2）", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


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
    _setup_frozen_logging()
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

    # 凭证持久化策略:
    # - SDK 的 from_login(save_to=...) 只写文件，**自己从不读** —— 每次都会重扫码
    #   分配新 bot 账号，手机端旧会话框就成"僵尸"（bot ID 变了）
    # - 修复: 文件存在 → WeixinBot(credentials_file=...) 直接恢复，不走扫码
    #         恢复失败（token 过期等）→ fallback 到 from_login 重新扫
    bot = None
    if CREDS_PATH.exists():
        print("[i] 用本地凭证恢复 bot…")
        try:
            bot = WeixinBot(credentials_file=str(CREDS_PATH))
            # bot.info 是属性 dict 不是方法，没办法在这一步真验证 token 是否过期。
            # 信任文件: 若 token 实际失效，后续 bot.run() 第一次轮询会报错，
            # 用户看到错误再删 .wechat_creds.json 重跑即可。
        except Exception as e:
            print(f"[i] 凭证文件无法解析（{type(e).__name__}: {str(e)[:80]}），重新扫码…")
            bot = None

    if bot is None:
        print("[i] 扫码登录中…")
        print("    手机微信 → 我 → 设置 → 插件 → 微信 ClawBot")
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
