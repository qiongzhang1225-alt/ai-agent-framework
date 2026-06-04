"""往工作区本地 .venv 中安装 Python 包 —— P2-L。

解决痛点：
- ``pip install`` 走的是系统 / base 环境的 Python，不是 .venv
- .venv 里可能没有 pip（某些虚拟环境创建方式不带 pip）
- 本工具自动 locate .venv → ensurepip → install

安全：
- 仅允许纯 ASCII 字母数字 + 连字符/下划线/点的包名
- 禁任何 shell 元字符、路径遍历、URL 形式
- 包名不合规直接拒，不给 subprocess 传递机会
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ai_agent import tool
from paths import DEFAULT_WORKDIR

# ── 包名校验 ──
# 允许：pygame / numpy / scikit-learn / flask_cors / msgpack==1.0.0
# 拒绝：/etc/passwd / ../../x / $(whoami) / package --install-option
_PKG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*([<>=!~]=?[a-zA-Z0-9_.\-+*]+)?$")


def _validate_package(package: str) -> str | None:
    """校验包名（含可选版本约束），不合规返回错误消息。"""
    pkg = (package or "").strip()
    if not pkg:
        return "包名不能为空"
    if not _PKG_RE.match(pkg):
        return (
            f"包名 {pkg!r} 格式不合法。"
            f"只允许字母/数字/连字符/下划线/点出发，"
            f"可选版本约束（如 package==1.0）。"
        )
    return None


def _locate_pip(workdir: Path) -> Path | None:
    """在 workdir/.venv 里定位 pip 可执行文件。"""
    candidates = [
        workdir / ".venv" / "bin" / "pip",
        workdir / ".venv" / "bin" / "pip3",
        workdir / ".venv" / "Scripts" / "pip.exe",
        workdir / ".venv" / "Scripts" / "pip3.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _locate_python(workdir: Path) -> Path | None:
    """在 workdir/.venv 里定位 python 可执行文件。"""
    candidates = [
        workdir / ".venv" / "bin" / "python",
        workdir / ".venv" / "bin" / "python3",
        workdir / ".venv" / "Scripts" / "python.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


@tool
def venv_install(
    package: str,
    config: dict,
    version: str = "",
    upgrade: bool = False,
) -> str:
    """往工作区 ``.venv`` 虚拟环境中安装一个 Python 包。

    自动检测 .venv 里的 pip；如果没有 pip 则先用 ``ensurepip`` 装好再执行安装。

    参数：
        package: 包名（如 ``"pygame"``、``"numpy>=1.21"``）
        version:  可选版本号（如 ``"2.5.2"``），等价于 ``package==version``
        upgrade:  是否使用 ``--upgrade`` 升级已安装的版本（默认 False）

    返回：安装结果摘要。
    """
    pkg = (package or "").strip()
    ver = (version or "").strip()
    if ver:
        pkg = f"{pkg}=={ver}"

    # 包名校验
    err = _validate_package(pkg)
    if err:
        return f"参数错误：{err}"

    # 定位 workdir
    cfg = (config or {}).get("configurable", {}) if config else {}
    workdir = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()

    venv_dir = workdir / ".venv"
    if not venv_dir.is_dir():
        return (
            f"未找到虚拟环境：{venv_dir}\n"
            f"请先在当前工作区创建 .venv。"
        )

    # 定位 python
    python_exe = _locate_python(workdir)
    if not python_exe:
        return f"未在 {venv_dir} 中找到 python 可执行文件，虚拟环境可能损坏。"

    # 定位 / 安装 pip
    pip_exe = _locate_pip(workdir)
    if not pip_exe:
        # 先 ensurepip
        r = subprocess.run(
            [str(python_exe), "-m", "ensurepip", "--upgrade", "--default-pip"],
            cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        if r.returncode != 0:
            return (
                f"虚拟环境中缺少 pip 且 ensurepip 失败：\n{r.stderr.strip()}"
            )
        pip_exe = _locate_pip(workdir)
        if not pip_exe:
            return "ensurepip 执行完成但仍未找到 pip，请检查虚拟环境。"

    # 执行安装
    install_args = [str(pip_exe), "install", pkg]
    if upgrade:
        install_args.append("--upgrade")

    try:
        r = subprocess.run(
            install_args,
            cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return f"安装 {pkg} 超时（300s）。可能是包太大或网络慢，请重试。"

    if r.returncode == 0:
        # 提取最后几行作为摘要
        lines = [l for l in r.stdout.split("\n") if l.strip()]
        summary = "\n".join(lines[-3:]) if lines else r.stdout
        return f"[成功] pip install {pkg}\n{summary}"
    else:
        return f"[失败] pip install {pkg}\n{r.stderr.strip()}"
