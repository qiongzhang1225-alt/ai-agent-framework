"""execute_code 工具：子进程隔离 + 路径守卫的 Python 沙箱。

设计：
- 每次调用生成一个临时 .py 脚本，前面注入 ``_PREAMBLE``（UTF-8、matplotlib 中文字体、
  ``builtins.open`` 路径守卫、禁用 subprocess / os.system）
- 子进程 ``cwd`` 强制为 workdir、``AGENT_WORKDIR`` 环境变量传入
- 60 秒硬超时；stdout 超 4000 字符存日志再截断
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from ai_agent import tool
from paths import DEFAULT_WORKDIR, META_DIR
from tools._common import find_real_python


# 注入到每段用户代码前面的 setup：UTF-8、matplotlib 中文配置、路径守卫
_PREAMBLE = r'''# -*- coding: utf-8 -*-
import sys, os, builtins, subprocess
from pathlib import Path

# Windows 控制台中文输出必备
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# matplotlib 静默后端 + 中文字体（避免方块与无 GUI 弹窗）
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
    _plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    _plt.rcParams["axes.unicode_minus"] = False
except Exception:
    pass

# 路径守卫：写操作只允许工作目录内；读操作另放行已安装库的内部资源
# （否则 python-docx、pdfplumber、matplotlib 这类库读不了自己的模板/字体）
_WORKDIR = Path(os.environ["AGENT_WORKDIR"]).resolve()
os.chdir(_WORKDIR)

_AG_READ_ROOTS = [
    _WORKDIR,
    Path(sys.prefix).resolve(),       # .venv
    Path(sys.base_prefix).resolve(),  # 系统 Python
]

def _ag_under(child, parent):
    try:
        c = os.path.normcase(str(Path(child).resolve()))
        p = os.path.normcase(str(Path(parent).resolve()))
        return c == p or c.startswith(p + os.sep)
    except (OSError, ValueError):
        return False

def _ag_check(p, mode):
    is_write = any(m in mode for m in ("w", "a", "x", "+"))
    if is_write:
        if not _ag_under(p, _WORKDIR):
            raise PermissionError(
                f"写入越界：{p} 不在工作目录 {_WORKDIR} 内。请改用工作目录内的相对路径。"
            )
    else:
        if not any(_ag_under(p, r) for r in _AG_READ_ROOTS):
            raise PermissionError(
                f"读取越界：{p} 不在工作目录或受信任的库路径内。"
            )

_ag_open = builtins.open

# C2 保护：写模式 + 目标已存在 → 先快照到 .execute_trash/YYYY-MM-DD/
# 让被用户代码意外覆盖的原文件能找回来。7 天自动清理。
import shutil as _ag_shutil
from datetime import datetime as _ag_datetime, timedelta as _ag_timedelta

_AG_EXEC_TRASH = _WORKDIR / ".execute_trash"
_AG_TRASH_KEEP_DAYS = 7

def _ag_cleanup_trash():
    if not _AG_EXEC_TRASH.exists():
        return
    cutoff = _ag_datetime.now() - _ag_timedelta(days=_AG_TRASH_KEEP_DAYS)
    try:
        for day_dir in _AG_EXEC_TRASH.iterdir():
            if not day_dir.is_dir():
                continue
            try:
                mtime = _ag_datetime.fromtimestamp(day_dir.stat().st_mtime)
                if mtime < cutoff:
                    _ag_shutil.rmtree(day_dir, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass

_ag_cleanup_trash()

def _ag_backup_before_write(p):
    """目标存在则在 .execute_trash 下保留快照（备份失败永不阻塞主流程）。"""
    try:
        src = Path(p) if not isinstance(p, Path) else p
        if not src.exists() or not src.is_file():
            return
        # 不备份 .execute_trash 自身内的文件（避免无限递归）
        try:
            src.resolve().relative_to(_AG_EXEC_TRASH.resolve())
            return
        except ValueError:
            pass
        day = _ag_datetime.now().strftime("%Y-%m-%d")
        ts = _ag_datetime.now().strftime("%H%M%S")
        dest_dir = _AG_EXEC_TRASH / day
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{src.stem}__{ts}{src.suffix}"
        seq = 1
        while target.exists():
            target = dest_dir / f"{src.stem}__{ts}_{seq}{src.suffix}"
            seq += 1
        _ag_shutil.copy2(src, target)
    except Exception:
        pass

def _ag_safe_open(file, *args, **kwargs):
    mode = args[0] if args else kwargs.get("mode", "r")
    if isinstance(file, (str, bytes, os.PathLike)) and isinstance(mode, str):
        _ag_check(file, mode)
        # 写模式 + 目标已存在 → 先备份到 .execute_trash/
        if any(m in mode for m in ("w", "a", "x", "+")):
            _ag_backup_before_write(file)
    return _ag_open(file, *args, **kwargs)
builtins.open = _ag_safe_open

# D1 激进放权：subprocess / os.system 不再被硬禁
# 兜底依赖：git history + audit.jsonl + .execute_trash + 工作目录 cwd
# 子进程默认 cwd = _WORKDIR（已由上方 os.chdir 设置）
# 用户 Python 层 open() 写入仍受 _ag_safe_open 守卫
# subprocess 调外部命令（如 rm / del）能绕过 Python 守卫
# 但 git 能完整恢复代码；workdir 外的破坏由审计日志记录

# ─── 用户代码开始 ───
'''


@tool
def execute_code(code: str, config: dict) -> str:
    """在当前会话的工作目录中执行 Python 代码（子进程隔离，UTF-8，180s 超时）。

    适用：表格处理（pandas/openpyxl）、画图（matplotlib）、读写文本/Word/PDF、
    调系统命令、pip 装包、跑 playwright 等。
    已预装：pandas、openpyxl、numpy、matplotlib、python-docx、pdfplumber。

    约束（请阅读，避免反复试错）：
    - Python 层 ``open()`` 写入仍受工作目录守卫：所有文件路径必须落在当前
      工作目录内，越界会抛 PermissionError。
    - 子进程不能交互，禁止使用 input()。
    - **subprocess / os.system 现在允许**（D1 放权）：可以跑 git / pip /
      playwright / npm 等。子进程默认 cwd 在 workdir，但**外部命令本身**
      （如 ``subprocess.run(["rm", ...])``）能绕过 Python 文件守卫 ——
      自己谨慎，git 能完整恢复代码，但 workdir 外的破坏不可逆。
    - 画图请用 plt.savefig('<文件名>.png')，不要用 plt.show()。
    - 读写中文文件统一使用 encoding='utf-8'。
    - 修改用户原文件前先复制 .bak（execute_trash 也会自动备份你覆盖的文件）。
    - 长流程一次性写完整脚本，避免拆成多次调用（state 不会跨次保留）。
    - 超时 180s，需要更长（如下载大文件）请用 ``run_command`` 加 ``timeout=300``

    返回：执行状态 + stdout（超长会截断并写日志）+ stderr + 新生成的文件列表。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    workdir_str = cfg.get("workdir") or str(DEFAULT_WORKDIR)
    workdir = Path(workdir_str).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    thread_id = str(cfg.get("thread_id", "default"))
    meta_dir = META_DIR / thread_id
    scripts_dir = meta_dir / "scripts"
    logs_dir = meta_dir / "logs"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    job_id = uuid.uuid4().hex[:8]
    script_path = scripts_dir / f"{job_id}.py"
    script_path.write_text(_PREAMBLE + "\n" + code, encoding="utf-8")

    # 工作目录快照，用于检测新增文件
    before = {p.name for p in workdir.iterdir() if p.is_file()}

    env = os.environ.copy()
    env["AGENT_WORKDIR"] = str(workdir)
    env["PYTHONIOENCODING"] = "utf-8"

    # 找 Python 解释器（frozen 模式避开 yuki.exe 自递归启动）
    python_exe = find_real_python()
    if python_exe is None:
        return (
            "[execute_code 不可用] 打包模式下找不到真的 Python 解释器。\n"
            "解决方法（任选其一）：\n"
            "1. 在 exe 同级目录建 .venv（python -m venv .venv），yuki 会自动用它\n"
            "2. 设环境变量 YUKI_PYTHON 指向你的 python.exe 完整路径\n"
            "3. 把 python 加入系统 PATH\n"
            f"脚本副本: {script_path}"
        )

    # D1 放权：超时 60 → 180s（给长流程任务空间，如 pip install / playwright install）
    _EXEC_TIMEOUT = 180
    try:
        result = subprocess.run(
            [python_exe, str(script_path)],
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_EXEC_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return (
            f"[执行超时] 超过 {_EXEC_TIMEOUT} 秒被强制终止。\n"
            f"工作目录: {workdir}\n脚本副本: {script_path}"
        )
    except Exception as e:
        return f"[启动失败] {e}\n脚本副本: {script_path}"

    after = {p.name for p in workdir.iterdir() if p.is_file()}
    new_files = sorted(after - before)

    parts = [
        f"[{'执行成功' if result.returncode == 0 else f'执行失败 returncode={result.returncode}'}]",
        f"工作目录: {workdir}",
    ]

    stdout = result.stdout or ""
    if len(stdout) > 4000:
        log_path = logs_dir / f"{job_id}.log"
        log_path.write_text(stdout, encoding="utf-8")
        parts.append(
            f"stdout (截断到前 4000 字符，完整日志: {log_path}):\n{stdout[:4000]}\n...(已截断)"
        )
    elif stdout:
        parts.append(f"stdout:\n{stdout}")

    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")

    if new_files:
        files_list = "\n".join(f"  - {workdir / f}" for f in new_files)
        parts.append(f"工作目录新增文件:\n{files_list}")

    return "\n\n".join(parts)
