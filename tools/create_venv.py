"""create_venv 工具：在工作目录创建 Python 虚拟环境。

【为什么需要专用工具】
yuki 让用 ``run_command("python", ["-m", "venv", ".venv"])`` 经常失败:
- frozen 模式（yuki.exe）里 sys.executable 是 yuki.exe 本身，根本没 venv 模块
- run_command("python", ...) 找 PATH 里的 python — 不一定是 3.10+
- pip 命令路径在 Win/Unix 不一样，yuki 经常拼错

本工具自动:
1. 用 find_real_python() 解析真实系统 Python（绕开 frozen yuki.exe）
2. 检查 Python 版本 >= 3.10
3. 跑 ``<py> -m venv <workdir>/<name>``
4. 校验 pyvenv.cfg 真生成了
5. 可选升级 pip + 装初始包列表

安全:
- venv 名只允许字母数字下划线连字符点（拒绝路径穿越）
- venv 落在 workdir 内（不能逃出工作目录）
- 已存在时除非 force=True，拒绝覆盖
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ai_agent import tool
from paths import DEFAULT_WORKDIR
from tools._common import find_real_python

# venv 目录名校验：
# - 第一个字符可以是字母/数字/下划线/单个点（允许 .venv）
# - 后续字符允许字母/数字/下划线/连字符/点
# - 禁止 .. 路径穿越（".." / "..xxx" 都拒）
_VENV_NAME_RE = re.compile(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.\-]*$")

# 包名校验（跟 venv_install.py 同步）：拒绝 shell 元字符 / 路径穿越 / URL
_PKG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*([<>=!~]=?[a-zA-Z0-9_.\-+*]+)?$")


def _resolve_workdir(config: dict) -> Path:
    cfg = (config or {}).get("configurable", {}) if config else {}
    workdir = cfg.get("workdir")
    if workdir:
        try:
            p = Path(workdir).resolve()
            if p.exists():
                return p
        except Exception:
            pass
    return Path(DEFAULT_WORKDIR).resolve()


def _check_python_version(py: str) -> tuple[bool, str]:
    """跑 ``<py> -c "import sys; print(...)"`` 验证版本 >= 3.10。

    返回 (是否符合, 版本字符串 或 错误描述)。
    """
    try:
        r = subprocess.run(
            [py, "-c",
             "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        return False, f"运行 Python 失败: {type(e).__name__}: {e}"
    if r.returncode != 0:
        return False, f"Python 输出 returncode={r.returncode}: {r.stderr[:200]}"
    ver = r.stdout.strip()
    try:
        parts = ver.split(".")
        major, minor = int(parts[0]), int(parts[1])
    except Exception:
        return False, f"无法解析版本字符串: {ver!r}"
    if (major, minor) < (3, 10):
        return False, f"Python {ver} 太老（需要 3.10+）"
    return True, ver


@tool
def create_venv(
    name: str = ".venv",
    python: str = "",
    upgrade_pip: bool = True,
    install_packages: list = None,
    force: bool = False,
    config: dict = None,
) -> str:
    """**在工作目录创建 Python 虚拟环境** —— 比手动跑 ``python -m venv`` 可靠。

    什么时候用:
    - 主人说"建个虚拟环境" / "搞个 venv" / "做个独立环境"
    - 你要装包跑代码但不想污染主人的全局 Python
    - 主人项目需要隔离的 Python 环境

    什么时候不用:
    - 已经有 venv 了要装包 → 用 ``venv_install``
    - 主人指定用全局 Python → 直接 ``run_command("pip", ...)``

    工作机制:
    1. 自动找系统真实 Python 解释器（绕开 yuki.exe 内嵌的那个）
    2. 检查 Python >= 3.10
    3. 在 ``<workdir>/<name>`` 创建 venv
    4. 校验 ``pyvenv.cfg`` 真生成了
    5. 可选升级 pip + 装初始包列表

    Args:
        name: venv 目录名（默认 ``.venv``）。只允许字母数字下划线连字符点。
        python: 指定 Python 解释器绝对路径（空 = 自动检测系统 Python 3.10+）。
        upgrade_pip: 创建后是否升级 pip 到最新（默认 True）。
        install_packages: 创建后立即安装的包列表（如 ``["numpy", "pandas==2.0"]``）。
        force: 如果同名 venv 已存在，强制重建（默认 False = 拒绝）。

    Returns:
        成功: 包含 venv 路径 + Python 版本 + 激活命令的多行提示。
        失败: 错误原因 + 修复建议。
    """
    # 1. 参数校验
    n = (name or "").strip()
    if not _VENV_NAME_RE.match(n) or n.startswith("..") or "/" in n or "\\" in n:
        return (
            f"❌ venv 名 {name!r} 不合法。"
            f"允许 .venv / myenv / dev_env 等；禁含路径分隔符 / 或 \\，禁以 .. 开头。"
        )

    workdir = _resolve_workdir(config)
    venv_dir = (workdir / name).resolve()

    # 防越界（name 通过 .. 逃出 workdir）
    try:
        venv_dir.relative_to(workdir)
    except ValueError:
        return f"❌ venv 路径 {venv_dir} 落在 workdir 外，拒绝创建。"

    # 已存在？
    if venv_dir.exists():
        is_venv = (venv_dir / "pyvenv.cfg").is_file()
        if is_venv and not force:
            return (
                f"ℹ️ venv 已存在: {venv_dir}\n"
                f"如果要重建，传 force=True。\n"
                f"如果只是想装包，用 venv_install(package=...)。"
            )
        if not is_venv and not force:
            return (
                f"❌ {venv_dir} 已存在但不是 venv（没有 pyvenv.cfg）。\n"
                f"拒绝覆盖，避免误删用户文件。如确认要清掉，传 force=True。"
            )

    # 2. 解析 Python 解释器
    if python:
        py = python.strip()
        if not Path(py).is_file():
            return f"❌ 指定的 python 路径不存在: {py!r}"
    else:
        py = find_real_python()
        if not py:
            return (
                "❌ 找不到系统 Python 解释器。\n"
                "  请确认 Python 3.10+ 已装在系统 PATH，或显式传 python=<绝对路径>。"
            )

    ok, ver_or_err = _check_python_version(py)
    if not ok:
        return (
            f"❌ {ver_or_err}\n"
            f"  当前用的解释器: {py}\n"
            f"  请确认装了 Python 3.10+，或显式传 python=<其他路径>。"
        )
    py_version = ver_or_err

    # 3. 如果是 force 重建，先删旧的
    if venv_dir.exists() and force:
        import shutil
        try:
            shutil.rmtree(venv_dir)
        except Exception as e:
            return f"❌ 删除旧 venv 失败: {type(e).__name__}: {e}"

    # 4. 创建 venv
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            [py, "-m", "venv", str(venv_dir)],
            cwd=str(workdir),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "❌ venv 创建超时 (120s)，可能磁盘 IO 太慢或被防病毒拦截。"
    except Exception as e:
        return f"❌ venv 创建子进程启动失败: {type(e).__name__}: {e}"

    if r.returncode != 0:
        return (
            f"❌ python -m venv 失败 (returncode={r.returncode}):\n"
            f"stderr: {r.stderr[:500]}\n"
            f"stdout: {r.stdout[:300]}\n"
            f"提示: 系统 Python 可能没装 venv 模块（如某些精简版 Python），"
            f"或者目标路径权限不够。"
        )

    # 5. 校验真生成了
    cfg_file = venv_dir / "pyvenv.cfg"
    if not cfg_file.is_file():
        return (
            f"❌ python -m venv 报告成功但 pyvenv.cfg 不存在: {cfg_file}\n"
            f"可能 PyInstaller 打包的 Python 阉割了 venv 模块。"
        )

    # 6. 找 venv 内的 pip
    pip_paths = [
        venv_dir / "bin" / "pip",
        venv_dir / "bin" / "pip3",
        venv_dir / "Scripts" / "pip.exe",
        venv_dir / "Scripts" / "pip3.exe",
    ]
    pip_exe = next((p for p in pip_paths if p.is_file()), None)

    msgs: list[str] = [
        "✅ venv 创建成功",
        f"  路径: {venv_dir}",
        f"  Python: {py_version} ({py})",
        "  pyvenv.cfg: 已生成",
    ]

    # 7. 升级 pip（可选）
    if upgrade_pip and pip_exe:
        try:
            r = subprocess.run(
                [str(pip_exe), "install", "--upgrade", "pip", "--quiet"],
                cwd=str(workdir),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=90,
            )
            if r.returncode == 0:
                msgs.append("  pip: 已升级到最新")
            else:
                msgs.append(f"  pip 升级警告: {r.stderr[:120]}")
        except Exception as e:
            msgs.append(f"  pip 升级跳过: {type(e).__name__}")

    # 8. 装初始包（可选）
    if install_packages and pip_exe:
        # 校验每个包名
        clean_pkgs: list[str] = []
        bad_pkgs: list[str] = []
        for p in install_packages:
            ps = str(p).strip()
            if _PKG_RE.match(ps):
                clean_pkgs.append(ps)
            else:
                bad_pkgs.append(ps)
        if bad_pkgs:
            msgs.append(f"  ⚠️ 忽略不合法包名: {bad_pkgs}")
        if clean_pkgs:
            try:
                r = subprocess.run(
                    [str(pip_exe), "install", *clean_pkgs],
                    cwd=str(workdir),
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=300,
                )
                if r.returncode == 0:
                    msgs.append(f"  装包: {clean_pkgs} ✓")
                else:
                    msgs.append(f"  装包失败: {r.stderr[:200]}")
            except Exception as e:
                msgs.append(f"  装包跳过: {type(e).__name__}: {e}")

    # 9. 给出激活命令提示
    if sys.platform == "win32":
        activate = venv_dir / "Scripts" / "activate.bat"
        msgs.append("\n激活命令（cmd）:")
        msgs.append(f"  call {activate}")
    else:
        activate = venv_dir / "bin" / "activate"
        msgs.append("\n激活命令:")
        msgs.append(f"  source {activate}")

    return "\n".join(msgs)
