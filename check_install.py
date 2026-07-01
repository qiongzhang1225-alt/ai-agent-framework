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

# ── 终端编码 ──
# Windows 默认控制台是 GBK(cp936)，下面那些 ✓/✗/⚠/━ 直接 print 会 UnicodeEncodeError，
# 脚本会在第一项检查就崩、根本跑不到记忆库那段。先把控制台输出页 + Python stdout 都切 UTF-8。
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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
    # 检测占位符（覆盖 .env.example 实际用的 your_deepseek_key_here 等变体）
    PLACEHOLDERS = (
        "your_deepseek_key_here", "your_key_here", "Your true key here",
        "your_api_key", "sk-xxx", "<your", "填这里", "替换为",
    )
    if any(ph in text for ph in PLACEHOLDERS):
        R.fail(
            ".env 仍含占位符（DEEPSEEK_API_KEY 未填真实 key）",
            "编辑 .env，把占位符换成真实 key：https://platform.deepseek.com/api_keys",
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


# ── 6. 记忆库实跑（真正加载模型 + chromadb 存取一次）──
# 为什么单列这一项：上面只看文件在不在、大小对不对，从不真正加载模型、
# 不建 chromadb 客户端、不做一次 embedding。所以"通过"≠记忆能用。
# 朋友机器就是卡在这里——文件检查全过，但实跑就炸（多半 sqlite<3.35 或模型联网校验）。
def check_memory_runtime():
    header("记忆库实跑（真正加载模型 + 存取一次）")

    # 前置不满足的话，前面已经报过错了，这里跳过避免重复刷屏
    try:
        import chromadb
    except ImportError:
        R.warn("chromadb 没装，跳过记忆实跑（见上面「Python 包依赖」）")
        return
    model_dir = HERE / "models" / "bge-base-zh-v1.5"
    if not model_dir.exists():
        R.warn("模型目录不存在，跳过记忆实跑（见上面「Embedding 模型」）")
        return

    import tempfile
    import shutil
    import sqlite3

    # ── 6a. chromadb 客户端：import 能过不代表能建，sqlite < 3.35 会在这一步才炸 ──
    tmp_db = tempfile.mkdtemp(prefix="yuki_check_db_")
    try:
        chromadb.PersistentClient(path=tmp_db)
        R.ok(f"chromadb 客户端可建立 (sqlite {sqlite3.sqlite_version})")
    except Exception as e:
        ok_sqlite = tuple(map(int, sqlite3.sqlite_version.split("."))) >= (3, 35, 0)
        if not ok_sqlite:
            R.fail(
                f"chromadb 建不起来：sqlite {sqlite3.sqlite_version} < 3.35.0",
                "换 python.org 的 Python 3.11+，或按 chromadb 文档用 pysqlite3-binary 顶替",
            )
        else:
            R.fail(
                f"chromadb 客户端建不起来：{type(e).__name__}: {e}",
                "跑 .venv\\Scripts\\python.exe diagnose_memory.py 看完整 traceback",
            )
        return
    finally:
        shutil.rmtree(tmp_db, ignore_errors=True)

    # ── 6b. 真正加载 bge 模型 + embedding 一次，隔离 模型/网络/torch 问题 ──
    embed_fn = None
    try:
        import memory
        embed_fn = memory._get_embedding_fn()
        vec = embed_fn(["测试一句话"])
        dim = len(vec[0]) if vec else 0
        if dim == 768:
            R.ok(f"bge 模型可加载并出向量 (维度 {dim})")
        else:
            R.warn(f"bge 模型出向量但维度异常 ({dim}，bge-base-zh 应为 768)")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        low = msg.lower()
        if any(k in low for k in (
            "connection", "timeout", "huggingface", "couldn't connect",
            "max retries", "localentrynotfound", "offline",
        )):
            fix = ("模型在联网校验时卡住：确认 models 目录完整；离线变量 "
                   "HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE 已在 memory.py 内设置")
        elif "weights_only" in low or "torch.load" in low:
            fix = "torch 版本与模型权重不配：pip install 'torch<2.6' 或重下模型"
        elif "keyerror" in low or "config" in low:
            fix = "transformers 版本与模型不配：pip install 'transformers<5'"
        else:
            fix = "跑 .venv\\Scripts\\python.exe diagnose_memory.py 看完整 traceback"
        R.fail(f"bge 模型加载失败：{msg}", fix)
        return

    # ── 6c. 端到端：存两条 + 语义检索（和 server/工具走同一条 chromadb+embedding 路）──
    #   用临时目录的独立 collection，绝不碰用户真实的 .memory 数据。
    #   查询词和命中文档「零关键词重叠」，能命中才证明 embedding 真的在做语义匹配。
    tmp_mem = tempfile.mkdtemp(prefix="yuki_check_mem_")
    try:
        client = chromadb.PersistentClient(path=tmp_mem)
        col = client.get_or_create_collection(
            name="yuki_check",
            embedding_function=embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        col.add(
            ids=["c1", "c2"],
            documents=["用户喜欢用 Python 写后端", "用户养了一只橘猫"],
        )
        res = col.query(query_texts=["他平时用什么编程语言"], n_results=1)
        docs = (res.get("documents") or [[]])[0]
        hit = docs[0] if docs else ""
        if "Python" in hit:
            R.ok(f"端到端存取 + 语义检索通过（零关键词命中：{hit!r}）")
        else:
            R.warn(
                f"能存能取，但语义检索没命中预期（返回 {hit!r}）",
                "embedding 可能退化成随机向量，记忆能存但搜不准 —— 跑 diagnose_memory.py",
            )
    except Exception as e:
        R.fail(
            f"记忆端到端失败：{type(e).__name__}: {e}",
            "跑 .venv\\Scripts\\python.exe diagnose_memory.py 看完整 traceback",
        )
    finally:
        shutil.rmtree(tmp_mem, ignore_errors=True)


# ── 7. 代码文件 ──
CRITICAL_FILES = [
    "agent.py", "server.py", "memory.py", "paths.py",
    "launcher.py", "requirements.txt",
    "ai_agent/__init__.py", "ai_agent/loop.py", "ai_agent/llm.py",
    "tools/__init__.py", "tools/execute.py", "tools/files.py",
    "tools/memory_tools.py", "tools/dialog.py",
    "prompts/yuki.md", "prompts/core.md", "prompts/constitution.md",
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


# ── 8. 端口 ──
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
    check_memory_runtime()
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
