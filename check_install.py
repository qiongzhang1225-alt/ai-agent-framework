#!/usr/bin/env python3
"""完整性检测：扫描有希安装是否齐全可用。

用法：
    python check_install.py           # 走系统 Python
    .venv/bin/python check_install.py # 走 venv（推荐）

输出每项检查 ✓/✗/⚠ + 失败时的修复建议。
返回 exit code 0 = 全部通过，非 0 = 有 critical 失败。
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

# ── 配色 ──
class C:
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"
    END = "\033[0m"

# Windows 终端默认不支持 ANSI（除非 Win10 1809+ 开了 VT processing）
if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        # 关掉颜色，避免乱码
        for attr in dir(C):
            if not attr.startswith("_") and isinstance(getattr(C, attr), str):
                setattr(C, attr, "")


# ── 结果统计 ──
class Results:
    def __init__(self):
        self.passed: list[str] = []
        self.warned: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []  # (描述, 修复建议)

    def ok(self, msg: str):
        self.passed.append(msg)
        print(f"  {C.GREEN}✓{C.END} {msg}")

    def warn(self, msg: str, fix: str = ""):
        self.warned.append((msg, fix))
        print(f"  {C.YELLOW}⚠{C.END} {msg}")
        if fix:
            print(f"    {C.GRAY}→ {fix}{C.END}")

    def fail(self, msg: str, fix: str = ""):
        self.failed.append((msg, fix))
        print(f"  {C.RED}✗{C.END} {msg}")
        if fix:
            print(f"    {C.GRAY}→ {fix}{C.END}")


R = Results()
HERE = Path(__file__).resolve().parent


def header(title: str):
    print(f"\n{C.BOLD}━━ {title} ━━{C.END}")


# ── 1. Python 版本 ──
def check_python():
    header("Python 版本")
    v = sys.version_info
    label = f"Python {v.major}.{v.minor}.{v.micro}  ({sys.executable})"
    if v < (3, 10):
        R.fail(label + " < 3.10", "升级 Python: https://www.python.org/downloads/")
    else:
        R.ok(label)


# ── 2. .venv ──
def check_venv():
    header("虚拟环境 (.venv)")
    venv_dir = HERE / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not venv_dir.exists():
        R.warn(
            ".venv 不存在",
            "建虚拟环境: python -m venv .venv",
        )
        return False
    if not venv_py.exists():
        R.fail(
            f"{venv_py} 不存在（.venv 残缺）",
            "重建: rm -rf .venv && python -m venv .venv",
        )
        return False
    R.ok(f".venv 已建立 ({venv_py})")
    # 当前是否在用 venv？
    if sys.executable.lower() != str(venv_py).lower():
        R.warn(
            f"当前 Python 不是 venv 的 ({sys.executable})",
            f"切换: {venv_py} check_install.py",
        )
    return True


# ── 3. 依赖 ──
REQUIRED_PACKAGES = [
    ("httpx", "httpx"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("chromadb", "chromadb"),
    ("sentence_transformers", "sentence-transformers"),
    ("dotenv", "python-dotenv"),
    ("pandas", "pandas"),
    ("openpyxl", "openpyxl"),
    ("webview", "pywebview"),       # pywebview 包导入名是 webview
    ("pystray", "pystray"),
]


def check_packages():
    header("Python 包依赖")
    missing = []
    for mod_name, pkg_name in REQUIRED_PACKAGES:
        try:
            __import__(mod_name)
            R.ok(f"{pkg_name}")
        except ImportError:
            R.fail(f"{pkg_name} 未装", f"装一下: pip install {pkg_name}")
            missing.append(pkg_name)
    if missing and (HERE / "requirements.txt").exists():
        print(f"  {C.GRAY}→ 一次装齐: pip install -r requirements.txt{C.END}")


# ── 4. .env ──
def check_env():
    header(".env 配置")
    env_file = HERE / ".env"
    if not env_file.exists():
        R.fail(
            ".env 不存在",
            "复制模板: cp .env.example .env  然后填 DEEPSEEK_API_KEY",
        )
        return
    text = env_file.read_text(encoding="utf-8", errors="replace")
    # 检测占位符
    if "your_key_here" in text or "Your true key here" in text:
        R.fail(
            ".env 仍含占位符（DEEPSEEK_API_KEY 未填）",
            "编辑 .env，把 your_key_here 换成真实 key",
        )
    elif "DEEPSEEK_API_KEY=" not in text:
        R.fail(".env 缺 DEEPSEEK_API_KEY", "加一行 DEEPSEEK_API_KEY=sk-xxx")
    else:
        # 取 key 长度检查
        for line in text.splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if len(key) < 20:
                    R.warn(f".env DEEPSEEK_API_KEY 看起来太短 ({len(key)} 字符)")
                else:
                    R.ok(f"DEEPSEEK_API_KEY 已配 ({len(key)} 字符)")
                break


# ── 5. Embedding 模型 ──
EMBEDDING_FILES = {
    "config.json": (500, 5000),
    "config_sentence_transformers.json": (50, 1000),
    "modules.json": (50, 1000),
    "pytorch_model.bin": (200_000_000, 500_000_000),  # ~390MB
    "sentence_bert_config.json": (10, 500),
    "special_tokens_map.json": (50, 1000),
    "tokenizer.json": (50_000, 500_000),
    "tokenizer_config.json": (50, 5000),
    "vocab.txt": (50_000, 500_000),
    "1_Pooling/config.json": (50, 1000),
}


def check_embedding_model():
    header("Embedding 模型 (bge-base-zh-v1.5)")
    model_dir = HERE / "models" / "bge-base-zh-v1.5"
    if not model_dir.exists():
        R.fail(
            f"模型目录不存在: {model_dir}",
            "按 models/README.md 下载（约 390MB）",
        )
        return

    missing = []
    too_small = []
    for fname, (min_size, max_size) in EMBEDDING_FILES.items():
        fpath = model_dir / fname
        if not fpath.exists():
            missing.append(fname)
        else:
            size = fpath.stat().st_size
            if size < min_size:
                too_small.append((fname, size, min_size))

    if missing:
        R.fail(
            f"缺 {len(missing)} 个文件",
            "缺失: " + ", ".join(missing[:5]) + (f"... 等 {len(missing)} 个" if len(missing) > 5 else ""),
        )
    elif too_small:
        R.fail(
            f"{len(too_small)} 个文件大小异常（下载可能中断）",
            "重新下载: " + ", ".join(f"{n} ({s} bytes, 应 ≥ {ms})" for n, s, ms in too_small[:3]),
        )
    else:
        total = sum((model_dir / f).stat().st_size for f in EMBEDDING_FILES)
        R.ok(f"模型完整 ({len(EMBEDDING_FILES)} 个文件，共 {total / 1024 / 1024:.1f} MB)")


# ── 6. 代码文件 ──
CRITICAL_FILES = [
    "agent.py", "server.py", "memory.py", "paths.py",
    "launcher.py", "requirements.txt",
    "ai_agent/__init__.py", "ai_agent/loop.py", "ai_agent/llm.py",
    "tools/__init__.py", "tools/execute.py", "tools/files.py",
    "tools/memory_tools.py", "tools/dialog.py",
    "prompts/yuki.md", "prompts/system.md",
    "templates/index.html", "static/style.css",
]


def check_code_files():
    header("代码文件完整性")
    missing = [f for f in CRITICAL_FILES if not (HERE / f).exists()]
    if missing:
        for f in missing:
            R.fail(f"缺 {f}", "")
        R.fail(
            f"共缺 {len(missing)} 个关键文件",
            "可能 git clone 不完整 / 误删。重新拉: git pull / git checkout HEAD -- .",
        )
    else:
        R.ok(f"{len(CRITICAL_FILES)} 个关键文件都在")


# ── 7. 端口 ──
def check_port():
    header("端口可用性")
    for port in (3616, 3617, 3618):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                R.ok(f"端口 {port} 可用")
                return
        except OSError:
            R.warn(f"端口 {port} 被占")
    R.fail(
        "3616-3618 全占",
        "关闭占用端口的程序，或编辑 launcher.py 改 find_free_port 起始值",
    )


# ── 主流程 ──
def main():
    print(f"\n{C.BOLD}有希 · 安装完整性检测{C.END}")
    print(f"{C.GRAY}项目目录: {HERE}{C.END}")

    venv_ok = True
    check_python()
    venv_ok = check_venv()
    check_packages()
    check_env()
    check_embedding_model()
    check_code_files()
    check_port()

    # ── 总结 ──
    print()
    print(C.BOLD + "═" * 60 + C.END)
    p, w, f = len(R.passed), len(R.warned), len(R.failed)
    print(
        f"  {C.GREEN}✓ {p} 通过{C.END}   "
        f"{C.YELLOW}⚠ {w} 警告{C.END}   "
        f"{C.RED}✗ {f} 失败{C.END}"
    )

    if f == 0:
        print(f"\n  {C.GREEN}{C.BOLD}全部通过，可以启动了：{C.END}")
        if venv_ok:
            cmd = ".venv\\Scripts\\python launcher.py" if os.name == "nt" else ".venv/bin/python launcher.py"
        else:
            cmd = "python launcher.py"
        print(f"  {cmd}\n")
        return 0
    else:
        print(f"\n  {C.RED}{C.BOLD}有 {f} 项失败，按上面的提示修复后再跑一次本检测。{C.END}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
