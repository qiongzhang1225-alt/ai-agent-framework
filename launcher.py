"""有希 桌面应用入口。

启动流程：
1. 单实例锁（同时只一个进程）
2. 选可用端口（默认 3616，被占往后找）
3. 后台线程启动 uvicorn server
4. 探活等 server 就绪
5. 创建 pywebview 窗口 + 系统托盘
6. 关窗 → 隐藏到托盘；托盘"退出"才真退出

跨平台：Windows / Mac / Linux。
- Windows：WebView2（Win10 1809+ 自带）
- Mac：WKWebView（系统自带）
- Linux：WebKitGTK（需 `apt install python3-webview-gtk` 或同等）

PyInstaller 打包后：
- 数据目录（.sandbox / .memory / skills / prompts）落在 exe 旁
- 代码资源（templates / static / assets）从 sys._MEIPASS 临时目录读
- yuki 用 self_edit 改的 prompts 在 exe 旁持久（首次启动从 bundle 解压）
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

# ── 资源路径解析（打包 / 源码兼容）────────────────────────────────────────
IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    # PyInstaller 打包：BUNDLE_DIR 是 sys._MEIPASS 临时目录（只读，重启会变）
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS"))
    # APP_DIR 是 exe 旁的固定目录（可写，持久化数据放这）
    APP_DIR = Path(sys.executable).resolve().parent
else:
    # 源码运行：两个一样
    BUNDLE_DIR = Path(__file__).resolve().parent
    APP_DIR = BUNDLE_DIR

# 让 import 能找到 server / agent / tools
sys.path.insert(0, str(BUNDLE_DIR))

# ── Windows GUI 模式 subprocess 不弹黑窗 ─────────────────────────────────
# PyInstaller --noconsole + GUI 进程启动 CLI 子进程（git/pip/playwright/...）时
# Windows 会给每个子进程**短暂**创建一个控制台窗口闪一下。
# tools/self_edit.py 每次操作前调 git commit safety checkpoint → 闪一下；
# tools/execute.py 跑 Python 子进程 → 闪一下；
# tools/shell.py / task_manager.py 跑 git/pip/playwright/etc → 闪一下。
# 解决：monkey-patch subprocess.Popen 默认带 CREATE_NO_WINDOW (0x08000000)。
# 仅 frozen+win32 启用，源码模式不受影响。
if sys.platform == "win32" and IS_FROZEN:
    import subprocess as _subprocess
    _CREATE_NO_WINDOW = 0x08000000
    _orig_popen = _subprocess.Popen

    class _SilentPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            flags = kwargs.get("creationflags", 0)
            if not (flags & _CREATE_NO_WINDOW):
                kwargs["creationflags"] = flags | _CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    _subprocess.Popen = _SilentPopen

# ── stdout / stderr 兜底（PyInstaller --noconsole 模式必需）─────────────
# 打包后 Windows GUI 程序没有控制台 → sys.stdout 和 sys.stderr 是 None
# uvicorn 的 ColourizedFormatter 调 sys.stderr.isatty() → None.isatty() 崩
#
# 策略：重定向到 APP_DIR/.yuki-launcher.log
# - 既解决了 isatty 崩溃问题
# - 也让用户能事后查看 server 抛的 traceback（之前 devnull 全丢了）
LOG_PATH = APP_DIR / ".yuki-launcher.log"
if sys.stdout is None or sys.stderr is None:
    try:
        _log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)  # 行缓冲
        if sys.stdout is None:
            sys.stdout = _log
        if sys.stderr is None:
            sys.stderr = _log
    except OSError:
        # 写日志失败 → 退化用 devnull
        _devnull = open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = _devnull
        if sys.stderr is None:
            sys.stderr = _devnull


# ── 数据目录种子（首次启动从 bundle 解压 prompts 到 APP_DIR）─────────────
def _seed_user_writable_dirs() -> None:
    """打包模式首次启动时，把可写资源从 bundle 复制到 APP_DIR。

    可写资源 = yuki 可能调 self_edit 改的 + 用户配置 = prompts/ + assets/
    不可写资源 = templates / static / 代码 = 留在 sys._MEIPASS 即可
    """
    if not IS_FROZEN:
        return  # 源码模式：路径本来就是 APP_DIR，不用解压

    for sub in ("prompts", "assets"):
        src = BUNDLE_DIR / sub
        dst = APP_DIR / sub
        if src.exists() and not dst.exists():
            try:
                shutil.copytree(src, dst)
            except Exception as e:
                print(f"[seed] 解压 {sub}/ 失败：{e}（不阻塞）")


# ── 单实例锁 ─────────────────────────────────────────────────────────────
LOCK_FILE = APP_DIR / ".yuki.lock"


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        kernel.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def acquire_lock() -> bool:
    """文件锁。返回 True=成功；False=已有实例。"""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            if _pid_alive(pid):
                return False
        except (ValueError, OSError):
            pass
        # 残留死锁，可以覆盖
    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def release_lock() -> None:
    try:
        if LOCK_FILE.exists():
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip() or "0")
            if pid == os.getpid():
                LOCK_FILE.unlink()
    except Exception:
        pass


def _show_already_running_dialog() -> None:
    """已运行时的提示弹窗（不依赖 pywebview）。"""
    msg = "有希 已经在运行了。\n请检查任务栏或系统托盘。"
    if sys.platform == "win32":
        try:
            import ctypes
            # MB_OK | MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(0, msg, "有希", 0x40)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)


# ── 端口选择 ─────────────────────────────────────────────────────────────
def find_free_port(start: int = 3616, max_tries: int = 20) -> int:
    """从 start 开始找一个本地可绑定的端口。"""
    for p in range(start, start + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", p))
                return p
        except OSError:
            continue
    raise RuntimeError(f"找不到可用端口（{start} ~ {start + max_tries - 1}）")


# ── 后台启动 uvicorn ─────────────────────────────────────────────────────
def start_server(port: int):
    """后台线程跑 uvicorn，主线程探活等就绪。"""
    import uvicorn

    # log_config=None：完全跳过 uvicorn 的 logging 配置
    # 原因：uvicorn 默认 LOGGING_CONFIG 用 ColourizedFormatter，依赖
    # sys.stderr.isatty()，PyInstaller --noconsole 下崩。
    # 自定义 log_config 又因为 uvicorn 内部对 'access' / 'default' handler
    # 名字有硬编码期待（KeyError: 'access'），不好兼容。
    # log_config=None 让 uvicorn 不动 logging 系统，最稳。
    config = uvicorn.Config(
        "server:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,
        use_colors=False,
    )
    server = uvicorn.Server(config)

    def _run():
        try:
            server.run()
        except Exception as e:
            print(f"[server] 异常退出：{e}", file=sys.stderr)

    threading.Thread(target=_run, daemon=True, name="uvicorn").start()

    # 探活
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return server
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server 在端口 {port} 启动超时（15s）")


# ── 系统托盘 ─────────────────────────────────────────────────────────────
_tray_icon = None
_window_ref = None


def setup_tray(icon_path: Path, on_show, on_quit) -> None:
    """启动系统托盘，子线程运行。"""
    global _tray_icon
    try:
        import pystray
        from PIL import Image
    except ImportError as e:
        print(f"[tray] 托盘库未安装，跳过：{e}", file=sys.stderr)
        return

    try:
        image = Image.open(icon_path)
    except Exception as e:
        print(f"[tray] 图标加载失败：{e}", file=sys.stderr)
        # 用一个默认 16x16 灰色方块兜底
        image = Image.new("RGB", (64, 64), color=(80, 80, 100))

    def _show(icon, item):
        try:
            on_show()
        except Exception as e:
            print(f"[tray] show 异常：{e}", file=sys.stderr)

    def _quit(icon, item):
        try:
            on_quit()
        except Exception as e:
            print(f"[tray] quit 异常：{e}", file=sys.stderr)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("显示窗口", _show, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _quit),
    )
    _tray_icon = pystray.Icon("yuki", image, "有希 · 信息统合思念体", menu)

    # 子线程跑托盘事件循环（pystray.run() 阻塞）
    threading.Thread(target=_tray_icon.run, daemon=True, name="tray").start()


def stop_tray() -> None:
    if _tray_icon is not None:
        try:
            _tray_icon.stop()
        except Exception:
            pass


# ── Splash 加载画面 ──────────────────────────────────────────────────────
def _build_splash_html(splash_image: Path | None) -> str:
    """生成内联 splash HTML（含 base64 嵌入图，零依赖）。

    用户可在 ``assets/splash.png`` / ``assets/splash.gif`` 放自定义画面，
    没有则用 ``icon.png`` 兜底。
    """
    import base64

    img_tag = ""
    if splash_image and splash_image.exists():
        try:
            data = splash_image.read_bytes()
            suffix = splash_image.suffix.lower()
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(suffix, "image/png")
            b64 = base64.b64encode(data).decode("ascii")
            img_tag = f'<img class="cover" src="data:{mime};base64,{b64}" alt="" />'
        except Exception:
            pass

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>有希 · 加载中</title>
<style>
  html, body {{
    margin: 0; padding: 0; height: 100%;
    background: radial-gradient(circle at 50% 40%, #1a1428 0%, #08080f 80%);
    color: #c4b8d8;
    font-family: 'Microsoft YaHei', -apple-system, 'PingFang SC', sans-serif;
    overflow: hidden;
    -webkit-user-select: none;
    user-select: none;
  }}
  body {{
    display: flex; align-items: center; justify-content: center;
  }}
  .splash {{ text-align: center; }}
  .avatar-wrap {{
    width: 180px; height: 180px; margin: 0 auto;
    position: relative;
    border-radius: 50%;
    overflow: hidden;
    box-shadow: 0 0 60px rgba(139, 118, 189, 0.5),
                inset 0 0 0 2px rgba(255,255,255,0.06);
    animation: pulse 2.4s ease-in-out infinite;
  }}
  .cover {{
    width: 100%; height: 100%;
    object-fit: cover;
    display: block;
  }}
  @keyframes pulse {{
    0%, 100% {{ transform: scale(1); }}
    50% {{ transform: scale(1.04); }}
  }}
  .ring {{
    position: absolute; inset: -8px;
    border-radius: 50%;
    border: 2px solid transparent;
    border-top-color: rgba(139, 118, 189, 0.8);
    border-right-color: rgba(139, 118, 189, 0.3);
    animation: spin 1.6s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .title {{
    font-size: 22px; margin: 32px 0 8px;
    letter-spacing: 0.15em;
    color: #e8e0f0;
  }}
  .subtitle {{
    font-size: 13px;
    color: #8676BD;
    opacity: 0.8;
  }}
  .subtitle .dot {{
    display: inline-block;
    animation: blink 1.4s infinite;
  }}
  .subtitle .dot:nth-child(2) {{ animation-delay: 0.2s; }}
  .subtitle .dot:nth-child(3) {{ animation-delay: 0.4s; }}
  @keyframes blink {{
    0%, 60%, 100% {{ opacity: 0.2; }}
    30% {{ opacity: 1; }}
  }}
  /* 雪花点缀（不依赖 css 文件，直接写） */
  .snow {{
    position: fixed; inset: 0; pointer-events: none;
    background-image:
      radial-gradient(1.5px 1.5px at 10% 20%, rgba(255,255,255,0.4), transparent),
      radial-gradient(1px 1px at 80% 30%, rgba(255,255,255,0.3), transparent),
      radial-gradient(1.5px 1.5px at 30% 70%, rgba(255,255,255,0.35), transparent),
      radial-gradient(1px 1px at 60% 80%, rgba(255,255,255,0.3), transparent),
      radial-gradient(2px 2px at 90% 60%, rgba(255,255,255,0.25), transparent);
    background-size: 100% 100%;
    animation: snowfall 8s linear infinite;
  }}
  @keyframes snowfall {{
    from {{ transform: translateY(-20px); opacity: 0.4; }}
    to {{ transform: translateY(20px); opacity: 0.4; }}
  }}
</style>
</head>
<body>
<div class="snow"></div>
<div class="splash">
  <div class="avatar-wrap">
    <div class="ring"></div>
    {img_tag}
  </div>
  <div class="title">信息统合思念体</div>
  <div class="subtitle">
    正在加载<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span>
  </div>
</div>
</body>
</html>"""


def _resolve_splash_image() -> Path | None:
    """找用户自定义 splash 图片；否则用 icon。"""
    candidates = ("splash.gif", "splash.png", "splash.jpg", "icon.png")
    for name in candidates:
        for base in (APP_DIR, BUNDLE_DIR):
            p = base / "assets" / name
            if p.exists():
                return p
    return None


SPLASH_MIN_DISPLAY_SECONDS = 0.3  # HTML splash 最少显示时间
# 之前 1.8s 是 onefile 模式下的人造等待（真实加载已在 bootloader 期完成）
# onedir 模式 + 延后 import 后，真实加载期间就在显示动画，不需要人造等待
# 0.3s 仅保留一个让"加载完成→主界面"过渡不突兀的瞬间


def _warm_up_in_background(port: int, on_ready) -> None:
    """后台启动预热：
    1. 等 server 探活通过
    2. 预热 chromadb embedding 模型（首次 list_memories 触发加载 bge-base-zh）
    3. 保证 HTML splash 至少显示 SPLASH_MIN_DISPLAY_SECONDS 秒
       （exe 模式下 import 已在 bootloader 解压期完成，预热瞬间结束，
        splash 来不及看，所以要补足显示时长）
    4. 完成后调 on_ready() —— 切换 splash 到主 URL
    """
    import urllib.request

    def _run():
        splash_start = time.time()

        # 等 server 真正响应 /api/health
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=0.5
                ) as r:
                    if r.status == 200:
                        break
            except Exception:
                pass
            time.sleep(0.2)

        # 预热 chromadb（最大延迟来源）
        try:
            from memory import list_memories
            list_memories(limit=1)
        except Exception as e:
            print(f"[warmup] memory 预热失败（不阻塞）: {e}", file=sys.stderr)

        # 保证 splash 至少显示一段时间，让用户能看见
        elapsed = time.time() - splash_start
        if elapsed < SPLASH_MIN_DISPLAY_SECONDS:
            time.sleep(SPLASH_MIN_DISPLAY_SECONDS - elapsed)

        # 切窗口
        try:
            on_ready()
        except Exception as e:
            print(f"[warmup] 切换主 URL 失败: {e}", file=sys.stderr)

    threading.Thread(target=_run, daemon=True, name="warmup").start()


# ── 主流程 ───────────────────────────────────────────────────────────────
def main() -> int:
    global _window_ref

    # 1. 单实例
    if not acquire_lock():
        _show_already_running_dialog()
        return 0

    try:
        # 2. 首次启动种子化 + cwd 设到 APP_DIR
        _seed_user_writable_dirs()
        os.chdir(APP_DIR)

        # 2.5. PyInstaller bootloader splash 检测（关闭时机：pywebview shown 后）
        try:
            import pyi_splash  # type: ignore  # PyInstaller 注入
            _pyi_splash_available = True
        except ImportError:
            _pyi_splash_available = False

        # 3. AppUserModelID（Windows 任务栏图标分组）
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "com.yuki.desktop"
                )
            except Exception:
                pass

        # 4. 解析图标路径
        import webview

        icon_path = None
        for sub in ("icon.ico", "icon.png"):
            for base in (APP_DIR, BUNDLE_DIR):
                p = base / "assets" / sub
                if p.exists():
                    icon_path = p
                    break
            if icon_path:
                break

        # 5. ★ 关键改造 ★
        # 先创建窗口显示 splash，后台再做重型加载（uvicorn / chromadb）。
        # 这样 HTML 动画 splash 覆盖**真实加载期间**，用户看到的是"加载中→主界面"的
        # 自然过渡，而不是"bootloader 静态图 5-10s 之后一瞬切主界面"。
        #
        # onedir 模式跳过了 _MEIPASS 解压（省 5-10s），launcher.main 跑得很早，
        # 此时大库 (torch / transformers / chromadb) 还没 import；
        # 创建 webview 窗口只依赖 webview 自身（轻量），splash 立刻显示；
        # 后台线程才触发 start_server → uvicorn 内部 import server →
        # server.py 顶部 import 才开始真正加载大库。
        splash_html = _build_splash_html(_resolve_splash_image())

        window = webview.create_window(
            title="有希 · 信息统合思念体",
            html=splash_html,
            width=1400,
            height=900,
            min_size=(1000, 700),
            background_color="#08080f",
            text_select=True,    # 允许在 webview 里选中复制文本（默认 False = 禁止选中）
        )
        _window_ref = window

        # 6. 后台线程做重型加载
        # 包含: 选端口 → 启 uvicorn (触发 server 模块 import → 大库加载) →
        #       探活到 /api/health OK → 预热 chromadb → 切到主 URL
        # 整个流程跟 pywebview splash 显示并行，用户看到的就是"动画期 = 真实加载期"
        def _heavy_init():
            try:
                port = find_free_port(3616)
                url = f"http://127.0.0.1:{port}"
                start_server(port)

                # 预热 chromadb + 切主 URL
                def _switch_to_main():
                    try:
                        window.load_url(url)
                    except Exception as e:
                        print(f"[switch] load_url 失败: {e}", file=sys.stderr)
                _warm_up_in_background(port, on_ready=_switch_to_main)
            except Exception as e:
                print(f"[heavy_init] 失败: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
        threading.Thread(target=_heavy_init, daemon=True, name="heavy_init").start()

        # 6. 关窗钩子：隐藏到托盘而非退出
        _hide_to_tray = {"v": True}  # mutable 闭包

        def _on_closing():
            if _hide_to_tray["v"]:
                try:
                    window.hide()
                except Exception:
                    pass
                return False  # 阻止关闭
            return True

        window.events.closing += _on_closing

        # 7. 托盘
        def _tray_show():
            try:
                window.show()
                window.restore()
            except Exception:
                pass

        def _tray_quit():
            _hide_to_tray["v"] = False  # 允许窗口真关闭
            try:
                window.destroy()
            except Exception:
                pass

        setup_tray(icon_path, on_show=_tray_show, on_quit=_tray_quit)

        # 8. 窗口创建后关闭 PyInstaller bootloader splash
        # （现在 pywebview 接管 splash，bootloader 那个不需要了）
        def _close_pyi_splash():
            if _pyi_splash_available:
                try:
                    import pyi_splash  # type: ignore
                    pyi_splash.close()
                except Exception:
                    pass
        window.events.shown += _close_pyi_splash

        # 9. 进入 pywebview 主循环（阻塞主线程）
        # icon 参数让窗口标题栏 + Windows 任务栏显示自定义图标（pywebview 6 起支持）
        start_kwargs = {"debug": False}
        if icon_path is not None:
            start_kwargs["icon"] = str(icon_path)
        webview.start(**start_kwargs)

    finally:
        stop_tray()
        release_lock()

    return 0


if __name__ == "__main__":
    sys.exit(main())
