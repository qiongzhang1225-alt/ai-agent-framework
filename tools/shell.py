"""系统命令调用（白名单制，5 层兜底）—— P2-H。

让私人助手能调系统工具处理 execute_code 难做的事：
- ``git`` 看仓库历史 / diff / blame
- ``7z`` 压缩 / 解压
- ``ffmpeg`` 音视频转码
- ``pdftk`` PDF 合并 / 拆分
- ``pandoc`` 文档格式转换（md ↔ docx ↔ html）
- ``curl`` 调外部 REST API

5 层兜底（按检查顺序）：
1. **命令白名单**：不在 ``_WHITELIST`` 的命令全拒（NotInWhitelist）
2. **参数限制**：每命令独立的子命令白名单 / 高危 flag 黑名单
3. **强制 cwd = workdir**：subprocess 的 cwd 锁死工作目录
4. **资源限制**：60s 超时；stdout / stderr 各 ≤ 1MB（截断后给 LLM）
5. **审计日志**：通过 ai_agent/loop.py 的 on_tool_event hook 自动落到 audit.jsonl

注意 vs ``execute_code``：
- execute_code 内部禁用了 subprocess.Popen / os.system（_PREAMBLE 守卫）
- 但 run_command 是直接在 server 进程发起 subprocess（不走 _PREAMBLE 路径），
  所以白名单 + 参数限制是这里唯一的保护层

流式运行（D3）：
- ``run_command_stream`` 使用 asyncio.create_subprocess_exec() 实时流式输出
- 输出通过 ``config["_event_emitter"]`` 推送到 chat SSE，前端实时显示
- 运行中的进程注册在 ``_running_processes`` 字典里，可按 task_id 取消
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from ai_agent import tool
from paths import DEFAULT_WORKDIR

# 运行中的进程：{task_id: {"process": asyncio.subprocess.Process, "cmd": str}}
_running_processes: dict[str, dict] = {}


# 白名单结构：
#   allowed_subcommands: set | None
#     非 None 时，第一个非 flag 参数必须在集合里
#   blocked_args: set
#     任何位置出现这个 flag（或 `flag=value` 形式）就拒绝
#   desc: 描述（仅供调试 / 文档）
_WHITELIST: dict[str, dict] = {
    "git": {
        "allowed_subcommands": {
            "status", "log", "diff", "show", "branch", "tag",
            "rev-parse", "ls-files", "blame", "describe", "remote",
            "shortlog", "reflog",
            "push", "fetch", "pull",          # D3: GitHub 推送
        },
        # 防 git -c <配置> 注入命令钩子；--exec 在 fetch / push 里执行任意命令
        "blocked_args": {"-c", "--exec", "--upload-pack", "--receive-pack", "--force", "-f"},
        "desc": "Git VCS（看历史 / diff / blame，纯只读）",
    },
    "7z": {
        # l=列内容, x=解压保目录结构, a=添加, t=测试, e=解到当前
        "allowed_subcommands": {"l", "x", "a", "t", "e"},
        "blocked_args": set(),
        "desc": "7-Zip 压缩 / 解压",
    },
    "ffmpeg": {
        "allowed_subcommands": None,  # 无子命令概念
        "blocked_args": set(),
        "desc": "音视频转码 / 剪辑",
    },
    "pdftk": {
        "allowed_subcommands": None,
        "blocked_args": set(),
        "desc": "PDF 工具集（合并 / 拆分 / 水印）",
    },
    "pandoc": {
        "allowed_subcommands": None,
        # --filter / --lua-filter 能执行任意外部脚本，必须禁
        "blocked_args": {"--filter", "--lua-filter"},
        "desc": "文档格式转换（md / docx / html / pdf 互转）",
    },
    "curl": {
        "allowed_subcommands": None,
        # 禁下载到任意路径（防越界写文件）；让私人助手用 write_file 自己保存响应体
        "blocked_args": {"-o", "--output", "-O", "--remote-name", "--remote-header-name"},
        "desc": "调用外部 REST API（不能下载到任意文件，要保存响应请配合 write_file）",
    },
    "playwright": {
        # 限制子命令为 install / install-deps（够她装 chromium / firefox / webkit）
        "allowed_subcommands": {"install", "install-deps"},
        "blocked_args": set(),
        "desc": "Playwright CLI —— 装浏览器 binary（screenshot_and_describe 依赖 chromium）",
    },
    # ── D2 激进放权：开发命令大白名单（依赖 git + audit 兜底，限制改为黑名单制）──
    "pip": {
        # pip uninstall / list / show / freeze / check 都允许；
        # 防 hash 校验绕过 / 索引污染 / 全卸载
        "allowed_subcommands": {
            "install", "uninstall", "list", "show", "freeze", "check",
            "download", "wheel", "config",
        },
        # 防外部源污染 / 强制不校验
        "blocked_args": {
            "--index-url", "--extra-index-url", "--find-links",
            "--trusted-host", "--no-deps",  # 跳过依赖检查太危险
        },
        "desc": "pip 包管理（install / uninstall / list / freeze 等）",
    },
    "python": {
        "allowed_subcommands": None,
        "blocked_args": set(),
        "desc": "运行 Python 脚本 / 模块（python -m / python script.py）",
    },
    "node": {
        "allowed_subcommands": None,
        "blocked_args": set(),
        "desc": "Node.js 运行时（跑 JS 脚本）",
    },
    "npm": {
        "allowed_subcommands": {
            "install", "i", "uninstall", "remove", "rm",
            "list", "ls", "outdated", "update", "audit",
            "run", "test", "build", "init",
        },
        "blocked_args": set(),
        "desc": "Node 包管理",
    },
    "pnpm": {
        "allowed_subcommands": None,  # pnpm 子命令丰富，全开
        "blocked_args": set(),
        "desc": "pnpm Node 包管理（npm 兼容 + 高效）",
    },
    "yarn": {
        "allowed_subcommands": None,
        "blocked_args": set(),
        "desc": "Yarn Node 包管理",
    },
    "tsc": {
        "allowed_subcommands": None,
        "blocked_args": set(),
        "desc": "TypeScript 编译器",
    },
    "cargo": {
        "allowed_subcommands": None,
        "blocked_args": set(),
        "desc": "Rust 包管理 / 构建 / 测试",
    },
    "go": {
        "allowed_subcommands": None,
        "blocked_args": set(),
        "desc": "Go 工具链（build / run / test / mod 等）",
    },
    "make": {
        "allowed_subcommands": None,
        "blocked_args": set(),
        "desc": "构建系统（Makefile 任务）",
    },
}

MAX_OUTPUT_BYTES = 1024 * 1024   # 1MB
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 3600       # 1 小时（pip install torch / 编译大型项目等）
# timeout=0 表示不限时（配合「■ 停止」按钮手动中止）


def _validate_args(cmd: str, args: list[str]) -> str | None:
    """校验参数。返回错误消息或 None（通过）。"""
    spec = _WHITELIST[cmd]

    # 黑名单 flag 检查（精确匹配 + flag=value 前缀匹配）
    blocked: set[str] = spec["blocked_args"]
    for a in args:
        if a in blocked:
            return f"参数 {a!r} 被禁用（{cmd} 的高危 flag）"
        # 防 `--filter=xxx` 形式逃过精确匹配
        for b in blocked:
            if b.startswith("--") and a.startswith(b + "="):
                return f"参数 {a!r} 被禁用"

    # 子命令白名单（取第一个非 flag 参数）
    allowed_sub = spec["allowed_subcommands"]
    if allowed_sub is not None:
        first_positional = None
        for a in args:
            if not a.startswith("-"):
                first_positional = a
                break
        if first_positional is None:
            return (
                f"{cmd} 需要子命令"
                f"（允许的：{', '.join(sorted(allowed_sub))})"
            )
        if first_positional not in allowed_sub:
            return (
                f"{cmd} 子命令 {first_positional!r} 不在白名单"
                f"（允许：{', '.join(sorted(allowed_sub))})"
            )

    return None


def _truncate_output(s: str) -> str:
    """截断到 MAX_OUTPUT_BYTES（按字节算，UTF-8 安全）。"""
    if not s:
        return ""
    b = s.encode("utf-8", errors="replace")
    if len(b) <= MAX_OUTPUT_BYTES:
        return s
    truncated = b[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return truncated + f"\n...(已截断，原 {len(b)/1024:.0f} KB)"


@tool
def run_command(
    cmd: str,
    args: list[str],
    config: dict,
    timeout: int = 0,
) -> str:
    """运行白名单内的系统命令。当 ``execute_code`` 不够直接时（外部 CLI、装包、
    构建等）用本工具。

    支持的命令（D2 放权后 17 个）：

    **基础 / 文档转换**
    - ``git``        看仓库历史 / diff / blame（只读类）
    - ``7z``         压缩 / 解压
    - ``ffmpeg``     音视频转码
    - ``pdftk``      PDF 合并 / 拆分 / 水印
    - ``pandoc``     文档格式转换（md / docx / html / pdf）
    - ``curl``       调外部 REST API（不能用 -o 下载到任意文件）

    **开发工具链（D2 新加）**
    - ``python``     跑独立 Python 脚本（``python -m`` / ``python file.py``）
    - ``pip``        包管理（install / uninstall / list / freeze 等）
    - ``node``       Node.js 运行时
    - ``npm`` / ``pnpm`` / ``yarn``  Node 包管理
    - ``tsc``        TypeScript 编译
    - ``cargo``      Rust 工具链
    - ``go``         Go 工具链
    - ``make``       构建系统
    - ``playwright`` 装浏览器 binary（``install`` / ``install-deps``）

    安全约束：
    - 工作目录强制锁死当前对话的 workdir
    - 默认超时 60s；长任务（``pip install`` 大包 / ``playwright install chromium``
      / ``cargo build`` 等）传 ``timeout=300`` 或更高（最大 600s）
    - stdout/stderr 各最多 1MB（超出截断）
    - 每次调用自动写 audit.jsonl

    pip 安全细节（防滥用）：
    - 禁 ``--index-url`` / ``--extra-index-url`` / ``--find-links`` / ``--trusted-host``
      / ``--no-deps``（这些会绕过依赖校验或污染索引源）
    - 装包后告诉主人你装了什么 + 原因

    什么时候用：
    - 跑 git / 7z / ffmpeg / pdftk / pandoc / curl 等外部命令
    - 装包：``run_command("pip", ["install", "playwright==1.40"], timeout=120)``
    - 编译：``run_command("tsc", ["--noEmit"])``
    - 装 chromium：``run_command("playwright", ["install", "chromium"], timeout=300)``

    什么时候**不要**用：
    - Python 能搞定的（pandas / Pillow / pdfplumber 等）→ 用 ``execute_code``
    - 下载文件 → ``fetch_webpage`` 拿内容 + ``write_file`` 写盘
    - 不确定主人是否同意装的包 → 用 ``request_pip_install`` 走 ask_user

    参数：
        cmd: 命令名（必须 ∈ 上述命令列表）
        args: 参数列表（不要把命令名拼进来）
        timeout: 超时秒数。0（默认）= 60s；最大 600s

    返回：执行状态 + stdout + stderr 文本。
    """
    cmd = (cmd or "").strip().lower()
    args = list(args or [])
    # 解析超时（0 = 默认）
    try:
        timeout_s = int(timeout) if timeout else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT_SECONDS
    # timeout=0 不限时（配合「■ 停止」按钮手动中止）
    timeout_s = max(1, min(timeout_s, MAX_TIMEOUT_SECONDS)) if timeout_s > 0 else 0

    # L1: 命令白名单
    if cmd not in _WHITELIST:
        allowed = ", ".join(sorted(_WHITELIST.keys()))
        return (
            f"命令 {cmd!r} 不在白名单。允许的命令：{allowed}。"
            f"如果确实需要其他命令，请告诉主人加进白名单（修改 tools/shell.py 的 _WHITELIST）。"
        )

    # L2: 参数白名单 / 黑名单
    err = _validate_args(cmd, args)
    if err:
        return f"参数校验失败：{err}"

    # L3: 强制 cwd = workdir
    cfg = (config or {}).get("configurable", {}) if config else {}
    workdir = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    # L4: 资源限制 + 启动
    try:
        result = subprocess.run(
            [cmd, *args],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=None if timeout_s == 0 else timeout_s,
        )
    except FileNotFoundError:
        return (
            f"命令 {cmd!r} 未在系统 PATH 中找到。"
            f"主人需要先安装这个工具（如 ``choco install {cmd}`` / ``scoop install {cmd}``）。"
        )
    except subprocess.TimeoutExpired:
        return f"[超时] {cmd} 运行超过 {timeout_s} 秒被强制终止（默认 60s，可传 timeout=300 等）"
    except Exception as e:  # noqa: BLE001
        return f"[启动失败] {type(e).__name__}: {e}"

    head = (
        "[成功]"
        if result.returncode == 0
        else f"[失败 returncode={result.returncode}]"
    )
    cmdline_preview = f"{cmd} {' '.join(args)}"[:200]

    parts = [f"{head} {cmdline_preview}"]
    if result.stdout:
        parts.append(f"stdout:\n{_truncate_output(result.stdout)}")
    if result.stderr:
        parts.append(f"stderr:\n{_truncate_output(result.stderr)}")
    return "\n\n".join(parts)


async def _stream_subprocess(
    task_id: str,
    cmd: str,
    args: list[str],
    workdir: Path,
    timeout_s: int,
    emitter,
) -> str:
    """用 asyncio.subprocess 启动进程，逐行读取输出并通过 emitter 推送。

    返回完整的 stdout+stderr 文本（同 run_command 格式）。
    """
    full_stdout: list[str] = []
    full_stderr: list[str] = []

    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return (
            f"命令 {cmd!r} 未在系统 PATH 中找到。"
            f"主人需要先安装这个工具。"
        )
    except Exception as e:
        return f"[启动失败] {type(e).__name__}: {e}"

    _running_processes[task_id] = {"process": proc, "cmd": f"{cmd} {' '.join(args)}"}

    async def _read_stream(stream, tag: str, lines: list[str]):
        """读取流的一行，存下来并发射事件。"""
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            lines.append(text)
            if emitter:
                try:
                    await emitter({
                        "type": "command_output",
                        "task_id": task_id,
                        "text": text,
                        "tag": tag,
                    })
                except Exception:
                    pass  # emitter 失败不阻塞

    stdout_task = asyncio.create_task(_read_stream(proc.stdout, "stdout", full_stdout))
    stderr_task = asyncio.create_task(_read_stream(proc.stderr, "stderr", full_stderr))

    try:
        retcode = await asyncio.wait_for(proc.wait(), timeout=None if timeout_s == 0 else timeout_s)
    except asyncio.TimeoutError:
        # 超时 → 杀进程
        try:
            proc.kill()
        except Exception:
            pass
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        head = f"[超时] {cmd} 运行超过 {timeout_s} 秒"
        if emitter:
            try:
                await emitter({"type": "command_done", "task_id": task_id, "exit_code": -1, "text": head})
            except Exception:
                pass
        return head + _format_output(full_stdout, full_stderr)
    
    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    head = "[成功]" if retcode == 0 else f"[失败 returncode={retcode}]"
    result = head + _format_output(full_stdout, full_stderr)

    if emitter:
        try:
            await emitter({"type": "command_done", "task_id": task_id, "exit_code": retcode, "text": head})
        except Exception:
            pass

    return result


def _format_output(stdout_lines: list[str], stderr_lines: list[str]) -> str:
    """把行列表拼成返回字符串（截断到 MAX_OUTPUT_BYTES）。"""
    parts = []
    if stdout_lines:
        out_text = "\n".join(stdout_lines)
        parts.append(f"stdout:\n{_truncate_output(out_text)}")
    if stderr_lines:
        err_text = "\n".join(stderr_lines)
        parts.append(f"stderr:\n{_truncate_output(err_text)}")
    return "\n\n" + "\n\n".join(parts) if parts else ""


@tool
async def run_command_stream(
    cmd: str,
    args: list[str],
    config: dict,
    task_id: str = "",
    timeout: int = 0,
    background: bool = False,
) -> str:
    """运行白名单内的系统命令并**实时流式输出到前端**。适合安装包、编译、下载
    等需要看中间进度的长任务。

    支持的命令、安全约束、参数同 ``run_command``。

    区别：
    - 使用 asyncio subprocess，输出逐行流到前端终端面板
    - 前端面板会显示实时输出，并有「取消」按钮
    - ``background=True`` 时任务独立于 chat SSE，关页面不断连，
      可在侧栏「后台任务」面板查看进度和取消
    - 返回结果格式同 run_command

    参数：
        cmd: 命令名（必须在白名单内）
        args: 参数列表
        task_id: 任务标识（用于取消），不传时自动生成
        timeout: 超时秒数。0（默认）= 60s；最大 600s
        background: 是否后台运行（独立于 SSE，关页面不断连）

    返回：执行状态 + stdout + stderr 文本。
    """
    cmd = (cmd or "").strip().lower()
    args = list(args or [])
    try:
        timeout_s = int(timeout) if timeout else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT_SECONDS
    # timeout=0 不限时（配合「■ 停止」按钮手动中止）
    timeout_s = max(1, min(timeout_s, MAX_TIMEOUT_SECONDS)) if timeout_s > 0 else 0

    # L1: 命令白名单
    if cmd not in _WHITELIST:
        allowed = ", ".join(sorted(_WHITELIST.keys()))
        return f"命令 {cmd!r} 不在白名单。允许的命令：{allowed}。"

    # L2: 参数校验
    err = _validate_args(cmd, args)
    if err:
        return f"参数校验失败：{err}"

    # L3: workdir
    cfg_configurable = (config or {}).get("configurable", {}) if config else {}
    workdir = Path(cfg_configurable.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    # L4: task_id + emitter
    import uuid
    tid = task_id or f"cmd_{uuid.uuid4().hex[:8]}"

    # ── 后台模式：交给 TaskManager ──
    if background:
        from tools.task_manager import get_manager
        mgr = get_manager()
        task = await mgr.start(cmd, args, workdir, task_id=tid)
        # 告诉前端 task_id，方便用户打开后台面板看
        emitter = None
        if config and isinstance(config, dict):
            emitter = config.get("_event_emitter")
        if emitter:
            try:
                await emitter({
                    "type": "command_background",
                    "task_id": tid,
                    "cmd": f"{cmd} {' '.join(args)}",
                })
            except Exception:
                pass
        # 返回 summary（后台继续跑）
        return (
            f"[后台任务已启动] task_id={tid}\n"
            f"命令: {cmd} {' '.join(args)}\n"
            f"侧栏「后台任务」面板可查看进度和取消。"
        )

    # ── 前台模式：走原流式逻辑 ──
    emitter = None
    if config and isinstance(config, dict):
        emitter = config.get("_event_emitter")

    return await _stream_subprocess(tid, cmd, args, workdir, timeout_s, emitter)


def cancel_command(task_id: str) -> bool:
    """取消正在运行的命令。返回 True 表示成功杀死进程。"""
    entry = _running_processes.get(task_id)
    if not entry:
        return False
    proc = entry.get("process")
    if proc and proc.returncode is None:
        try:
            proc.kill()
            return True
        except Exception:
            pass
    return False
