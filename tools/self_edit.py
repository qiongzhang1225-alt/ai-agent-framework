"""自我修改工具集（self-edit）—— 让有希自己优化代码 / prompt。

工具集（6 个 @tool + 内部 helpers）：

- ``self_read_file``        读项目内任何文件（含系统代码 / prompt）
- ``self_edit_file``        精确字符串替换（首选）
- ``self_write_file``       整文件覆盖
- ``self_edit_with_test``   强制先写自检脚本，跑过才 commit（Worker/Reviewer 精神）
- ``self_rollback``         git revert 撤销最近 N 个 commit
- ``self_diff``             看 commit 历史

核心保证（必读，改本文件前先看完）：

1. **改前自动 git commit**：每次 ``self_edit_*`` 工具的第一步是 subprocess
   直接调 git 提交当前 working tree（``_git_safety_checkpoint``）。即使她改了
   本文件让"以后不 commit"，**那次改动仍被旧版本的本函数 commit 了** ——
   永远存在最后一个干净 commit 可回滚。

2. **git 子命令白名单**：``_run_git`` 只放行安全子命令；禁所有破坏性操作
   （``reset --hard`` / ``push --force`` / ``rm`` / ``-c`` 注入 / ``--exec`` 等）。

3. **改后自动校验**：.py 文件 ``py_compile`` 检查语法；.md 文件检查长度合理性。
   校验失败 → 自动 ``git restore <path>`` 撤回本次改动 + 返回失败原因。

4. **完整回滚入口在主人手上**：``self_rollback`` 工具（``git revert``）/
   主人 PowerShell ``git reset --hard <hash>`` / GitHub remote 副本。三层保险。

权限分级（_validate_path）：

- **L0 永不可改**（即使主人在 prompt 里说"放权"工具也硬拒绝）：
    .env, .env.example, requirements.txt, .gitignore, .git/* (用户明确说"配置不改")
- **可改**：``tools/`` / ``ai_agent/`` / ``prompts/`` 前缀 + 6 个根入口文件
    (agent.py, server.py, audit.py, backups.py, paths.py, memory.py)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_agent import tool
from paths import PROJECT_ROOT


# ── 路径权限分级 ─────────────────────────────────────────────────────────────

# L0 永不可改（主人不希望改的配置文件 + git 历史本身）
_L0_BLOCKED_FILES = frozenset({
    ".env", ".env.example",
    "requirements.txt",
    ".gitignore",
})
_L0_BLOCKED_PREFIXES = (".git/", ".git\\")

# 可改的白名单前缀
_ALLOWED_PREFIXES = (
    "tools/", "tools\\",
    "ai_agent/", "ai_agent\\",
    "prompts/", "prompts\\",
    "templates/", "templates\\",
    "static/", "static\\",
)

# 可改的根文件（项目根 .py 入口）
_ALLOWED_ROOT_FILES = frozenset({
    "agent.py", "server.py", "audit.py", "backups.py", "paths.py", "memory.py",
})


# git 子命令白名单（其他全拒）
_GIT_ALLOWED_SUBCOMMANDS = frozenset({
    "add", "commit", "checkout", "branch", "log", "diff", "status",
    "rev-parse", "show", "restore", "revert",
})

# git 高危 flag 黑名单（任何子命令下出现都拒）
_GIT_BLOCKED_FLAGS = frozenset({
    "--hard", "--mixed", "--keep", "--merge",   # reset 的破坏性模式
    "--force", "-f",                            # 强制推/删
    "--no-verify",                              # 跳过 hooks
    "--exec", "--upload-pack", "--receive-pack",  # 命令注入向量
    "-c",                                       # git -c <key=val> 配置注入
})


# ── git 封装 ────────────────────────────────────────────────────────────────


def _git_bin() -> str:
    """找 git 可执行文件路径，找不到抛错。"""
    g = shutil.which("git")
    if not g:
        raise RuntimeError("找不到 git 可执行文件（请确认 git 已安装且在 PATH 中）")
    return g


def _atomic_write_text(path: Path, content: str) -> None:
    """原子写文件：先写 .tmp，再 rename 覆盖原文件。

    背景：``Path.write_text`` 不是原子写 —— 系统层先 truncate 再 write。
    如果在写到一半被中断（用户停止流式 / 进程异常 / 中断信号），
    会留下残缺文件。曾把 server.py 截断到 5 字节"utf-8"几乎丢失。

    原子写保证任何时刻看到的都是"完整旧内容"或"完整新内容"二选一，
    永不会出现写到一半的状态。

    用 ``with_name`` 而非 ``with_suffix`` 是因为后者只换最后一个扩展名，
    对 ``server.py`` 这种文件 ``.py + .tmp`` 没问题，但对 ``foo.tar.gz``
    会变成 ``foo.tar.tmp`` 丢掉 .gz —— with_name 显式 + 后缀更稳。
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)  # POSIX 上 rename 是原子的；Windows 上 Path.replace 等价
    except Exception:
        # 清理临时文件，避免在目录里留 .tmp 垃圾
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def _run_git(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    """跑一次 git 命令。args 不含 'git' 本身。

    L1 子命令白名单 + L2 高危 flag 黑名单。timeout 30s。
    """
    if not args:
        raise ValueError("git args 不能为空")

    subcmd = args[0]
    if subcmd not in _GIT_ALLOWED_SUBCOMMANDS:
        raise PermissionError(
            f"git 子命令 {subcmd!r} 不在白名单"
            f"（允许：{', '.join(sorted(_GIT_ALLOWED_SUBCOMMANDS))}）"
        )

    for a in args:
        if a in _GIT_BLOCKED_FLAGS:
            raise PermissionError(f"git 参数 {a!r} 被禁用（防破坏 / 命令注入）")

    return subprocess.run(
        [_git_bin(), *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _git_safety_checkpoint(reason: str) -> str | None:
    """改前安全 checkpoint：subprocess 直接 commit 当前 working tree。

    成功返回 commit hash；无未提交改动时返回 None；失败也返回 None（不抛）。

    **重要**：这函数直接 subprocess.run，**不依赖任何 Python 模块状态**。
    即使本文件被她改坏让本函数"什么都不做"，**那次改动仍被旧版本的本函数
    commit** —— 所以永远存在最后一个 safe commit 可回滚。
    """
    try:
        r = _run_git(["status", "--porcelain"])
        if r.returncode != 0 or not r.stdout.strip():
            return None
        _run_git(["add", "--all"])
        msg = f"[ai-edit-pre] safety before: {reason[:120]}"
        r = _run_git(["commit", "-m", msg])
        if r.returncode != 0:
            return None
        r = _run_git(["rev-parse", "HEAD"])
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _count_recent_edits(path: str, hours: int = 1) -> int:
    """git log 查最近 N 小时内涉及 path 的 ``[ai-edit]`` commit 数（不含 safety / rollback）。"""
    try:
        since = f"{hours} hours ago"
        r = _run_git([
            "log", f"--since={since}",
            "--pretty=format:%s", "--", path,
        ])
        if r.returncode != 0:
            return 0
        cnt = 0
        for line in r.stdout.split("\n"):
            line = line.strip()
            if line.startswith("[ai-edit] "):
                cnt += 1
        return cnt
    except Exception:
        return 0


def _count_recent_reverts(path: str, hours: int = 1) -> int:
    """统计最近 N 小时内该文件被 revert 的次数。

    熔断只应在"改动方向反复错误"时触发，而不是简单按编辑次数。
    Revert 意味着主人明确否决了那次改动——这是真正的"没通过"信号。
    """
    import re
    try:
        since = f"{hours} hours ago"
        r = _run_git([
            "log", f"--since={since}",
            "--pretty=format:%s", "--", path,
        ])
        if r.returncode != 0:
            return 0
        cnt = 0
        # Revert commit 的 subject 格式: Revert "[ai-edit] path: reason"
        revert_pat = re.compile(r'^Revert "?\[ai-edit\]')
        for line in r.stdout.split("\n"):
            if revert_pat.match(line.strip()):
                cnt += 1
        return cnt
    except Exception:
        return 0


# 熔断阈值：基于 revert 次数（真正"没通过"），不是编辑次数
# 同一文件 1 小时内被 revert >= 此值时硬拒绝
EDIT_LOOP_REVERT_THRESHOLD = 3
EDIT_LOOP_WINDOW_HOURS = 1

# 自查提示阈值：每 N 次 [ai-edit] commit 后建议跑一次 audit_stats
AUDIT_NUDGE_EVERY = 20


def _check_audit_nudge() -> str:
    """每 N 次 [ai-edit] 后追加 audit_stats 自查提示。

    设计：
    - 软提示，不强制 —— 加在 self_edit_* 成功返回 msg 末尾
    - 数 git log 全部历史的 [ai-edit] commit，count % AUDIT_NUDGE_EVERY == 0 时触发
    - 失败 / 0 commit / 数错都静默返回 "" —— 永不阻塞主流程
    """
    try:
        r = _run_git(["log", "--pretty=format:%s"])
        if r.returncode != 0:
            return ""
        count = sum(1 for line in r.stdout.split("\n") if line.startswith("[ai-edit] "))
        if count > 0 and count % AUDIT_NUDGE_EVERY == 0:
            return (
                f"\n📊 已累计 {count} 次自修改。建议跑 "
                f"`audit_stats(last_n=500)` 看你工具使用画像"
                f"（成功率 / 高失败工具 / 长期未用的工具）。"
            )
    except Exception:
        pass
    return ""


def _check_edit_loop(path: str) -> str | None:
    """检查熔断。仅在改动被反复 revert（真正"没通过"）时触发硬拒绝。

    设计理念：3 次编辑本身不是问题；3 次被主人 revert 才是。
    单纯编辑次数多只是警告，不拒绝。
    """
    reverts = _count_recent_reverts(path, hours=EDIT_LOOP_WINDOW_HOURS)
    if reverts >= EDIT_LOOP_REVERT_THRESHOLD:
        return (
            f"❌ [熔断保护] {path!r} 在最近 {EDIT_LOOP_WINDOW_HOURS} 小时内被 "
            f"revert 了 {reverts} 次（阈值 {EDIT_LOOP_REVERT_THRESHOLD}）。"
            f"这些改动被主人明确否决过，说明方向可能有问题。"
            f"**停下来**，调 ``ask_user`` 确认方向后再继续。"
        )
    return None


def detect_recent_rollbacks(last_commits: int = 50) -> list[dict]:
    """检测最近 N 个 commit 里被 ``git revert`` 掉的 ``[ai-edit]`` 改动。

    用作"回滚感知"：server 启动时把这些被否决的改动作为 prompt 的一段
    注入给 Agent，避免她下次见类似需求时重复同样的改动。

    返回 ``[{reverted_subject, reverted_hash, rollback_subject, rollback_at}, ...]``
    （按时间倒序，最新的在前），最多 5 条。
    """
    import re
    try:
        r = _run_git([
            "log", "-n", str(last_commits),
            "--pretty=format:%h|%s|%ad",
            "--date=short",
        ])
        if r.returncode != 0 or not r.stdout.strip():
            return []
    except Exception:
        return []

    out: list[dict] = []
    # 形如：Revert "[ai-edit] tools/files.py: 改 backup 逻辑"
    revert_pat = re.compile(r'^Revert ["\']?(\[ai-edit\][^"\']*?)["\']?$')
    for line in r.stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        h, subject, date = parts
        m = revert_pat.match(subject)
        if not m:
            continue
        out.append({
            "reverted_subject": m.group(1),
            "rollback_hash": h,
            "rollback_at": date,
        })
        if len(out) >= 5:
            break
    return out


def format_rollback_warnings_for_prompt() -> str:
    """生成"主人最近 revert 的改动"prompt 片段，供 agent.py 注入。"""
    try:
        events = detect_recent_rollbacks(50)
    except Exception:
        return ""
    if not events:
        return ""
    lines = [
        "【⚠ 主人最近 revert 过的自修改改动 —— 回滚感知】",
        "下面这些改动被主人撤回过。如果用户再次提出类似需求，**先 ask_user**",
        "确认这次的方向和上次不同在哪，不要重复做同样的改动：",
    ]
    for ev in events:
        lines.append(f"- {ev['reverted_subject']}（{ev['rollback_at']} 被 revert）")
    return "\n".join(lines)


def auto_commit_pending(label: str = "server-startup") -> str | None:
    """server 启动时兜底：若有未提交改动直接 commit。

    防御场景：她改坏了 self_edit_file 让"以后不 commit"。server 重启时本函数
    （独立调用路径）会把所有未提交改动落账，**确保所有改动都进 git 历史**。

    供 server.py @app.on_event("startup") 调用。
    """
    try:
        r = _run_git(["status", "--porcelain"])
        if r.returncode != 0 or not r.stdout.strip():
            return None
        _run_git(["add", "--all"])
        msg = f"[{label}] auto-commit pending working-tree changes"
        r = _run_git(["commit", "-m", msg])
        if r.returncode != 0:
            return None
        r = _run_git(["rev-parse", "--short", "HEAD"])
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


# ── 路径校验 ─────────────────────────────────────────────────────────────────


def _check_sub_restricted(config: dict) -> str | None:
    """受限子对话直接拒绝 self_edit_* 调用。返回错误字符串或 None（放行）。

    设计：self_edit 改的是系统代码 / prompt，影响全局。主对话和高级子对话允许；
    受限子对话**直接拒绝**（不走请求模式 —— 自修改太危险，必须主人在高级对话里做）。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    if cfg.get("conv_kind") == "sub" and cfg.get("sub_level") == "restricted":
        return (
            "❌ 受限模式子对话**不可调用 self_edit_*** —— 自修改系统代码 / prompt "
            "属于影响全局的高风险操作，必须在**主对话**或**高级模式子对话**里做。"
            "如果你想优化某段代码，告诉主人在高级对话里处理。"
        )
    return None


def _validate_path(path: str) -> tuple[Path | None, str | None]:
    """校验 path 是否允许 self_edit 写入。返回 (abs_path, error_msg)。"""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return None, "path 不能为空"

    # 必须用相对路径（防她传绝对路径绕过）
    if Path(p).is_absolute():
        return None, "path 必须是项目相对路径，不要用绝对路径"

    # 黑名单文件（精确匹配）
    if p in _L0_BLOCKED_FILES:
        return None, f"{p!r} 是配置文件，主人明确不希望改"

    # .git/ 目录
    for prefix in _L0_BLOCKED_PREFIXES:
        if p.startswith(prefix):
            return None, ".git/ 目录不可触碰（破坏 git 历史会失去回滚能力）"

    # 白名单（前缀 或 根文件）
    is_allowed = False
    if p in _ALLOWED_ROOT_FILES:
        is_allowed = True
    else:
        for prefix in _ALLOWED_PREFIXES:
            if p.startswith(prefix):
                is_allowed = True
                break
    if not is_allowed:
        return None, (
            f"{p!r} 不在可改路径白名单。允许：根入口文件"
            f"（{', '.join(sorted(_ALLOWED_ROOT_FILES))}）"
            f" 或 ``tools/`` / ``ai_agent/`` / ``prompts/`` 前缀"
        )

    # 解析绝对路径 + 越界校验
    try:
        abs_path = (PROJECT_ROOT / p).resolve()
        abs_path.relative_to(PROJECT_ROOT.resolve())
    except (ValueError, OSError) as e:
        return None, f"路径解析失败或越界：{e}"

    return abs_path, None


# ── 改后校验 + rollback ─────────────────────────────────────────────────────


def _verify_after_edit(path: Path) -> str | None:
    """改完后校验。返回 None=通过，str=错误原因（调用方应 git restore）。"""
    if path.suffix == ".py":
        import py_compile
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            return f"语法校验失败：{str(e.msg)[:300]}"
        except Exception as e:
            return f"校验异常：{type(e).__name__}: {e}"
    elif path.suffix == ".md":
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return f"读取失败：{e}"
        n = len(content)
        if n < 30:
            return f"内容过短 ({n} 字符)，疑似被错误清空"
        if n > 100_000:
            return f"内容过长 ({n} 字符)，超过 100KB 上限"
    return None


def _git_restore_path(path: Path) -> bool:
    """改坏了 → git restore <path> 撤回到 HEAD。返回是否成功。"""
    try:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        r = _run_git(["restore", "--source=HEAD", "--", rel])
        return r.returncode == 0
    except Exception:
        return False


# ── 5 个 @tool ──────────────────────────────────────────────────────────────


@tool
def self_read_file(path: str, offset: int = 0, limit: int = 0) -> str:
    """读取项目内任意文件（系统代码 / prompt / 文档都行），用于你自己学习 / 优化前预读。

    比 ``read_file`` 多出的能力：能读 workdir 外的项目文件（``tools/`` /
    ``ai_agent/`` / ``prompts/`` 等）。仅读，不写。

    什么时候用：
    - 你想改某个工具，先 ``self_read_file("tools/files.py")`` 看完再 ``self_edit_file``
    - 主人问"你内部是怎么实现的"，可以读完再解释
    - 你想看自己的 SYSTEM_PROMPT 是怎么写的 → ``self_read_file("prompts/system.md")``

    **行切片**（offset / limit）：
    - 默认 ``offset=0, limit=0`` → 整文件读（保持原行为，最多 30000 字符）
    - 指定后只读指定行段，输出带行号方便后续 ``self_edit_file`` 精确引用
    - 看大文件（如 ``tools/self_edit.py`` 800+ 行）推荐：
      1. 先 ``self_read_file("tools/foo.py")`` 看头部 + 行数提示
      2. 再 ``self_read_file("tools/foo.py", offset=N, limit=60)`` 精读关键段

    路径限制：
    - **可读**项目内任何文件（含 ``ai_agent/`` / ``audit.py`` 等系统代码）
    - **不可读** ``.env`` / ``.env.example``（含密钥）/ ``.git/`` 目录

    Args:
        path: 项目相对路径（如 ``"tools/files.py"`` / ``"prompts/system.md"`` /
              ``"ai_agent/loop.py"``）
        offset: 从第几行开始读（1-indexed；0 = 从头）
        limit: 最多读多少行（0 = 不限，仍受 30000 字符上限约束）

    Returns:
        文件内容；用 offset/limit 时输出带 ``行号\\t内容`` 前缀。
    """
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return "❌ path 不能为空"
    if p in _L0_BLOCKED_FILES:
        return f"❌ {p!r} 是敏感配置（含密钥等），不可读"
    for prefix in _L0_BLOCKED_PREFIXES:
        if p.startswith(prefix):
            return "❌ .git/ 目录不可读"

    try:
        abs_path = (PROJECT_ROOT / p).resolve()
        abs_path.relative_to(PROJECT_ROOT.resolve())
    except (ValueError, OSError):
        return f"❌ 路径越界：{p!r}"

    if not abs_path.is_file():
        return f"❌ 不是文件或不存在：{p!r}"

    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"❌ 读取失败：{e}"

    # 行切片分支
    if offset > 0 or limit > 0:
        from tools.files import _slice_text_by_lines
        return _slice_text_by_lines(text, offset, limit, 30_000)

    if len(text) > 30_000:
        total_lines = text.count("\n") + 1
        return (
            text[:30_000]
            + f"\n\n...(已截断，原 {len(text)} 字符 / {total_lines} 行)"
            f"\n提示: 用 self_read_file(path, offset=N, limit=M) 按行精读后续部分"
        )
    return text or "(空文件)"


@tool
def self_edit_file(
    path: str,
    old_string: str,
    new_string: str,
    reason: str,
    config: dict = None,
) -> str:
    """精确字符串替换式编辑项目内系统文件（你自己的代码 / prompt）—— 长期优化你自己。

    **生效需要主人重启 server**。改完只是写盘 + git commit，进程已加载的旧代码
    继续跑，给主人 review 窗口。

    安全机制（你不用担心）：
    1. 改前 subprocess 直接 git commit 当前状态（永远有干净 commit）
    2. 改后自动校验：.py 用 ``py_compile`` 查语法；.md 查长度合理性
    3. 校验失败 → 自动 ``git restore`` 撤回 + 返回错误原因
    4. 成功 → commit 本次改动 + 告诉主人 commit hash + 可 self_rollback

    路径白名单：
    - 根入口：``agent.py`` / ``server.py`` / ``audit.py`` / ``backups.py``
      / ``paths.py`` / ``memory.py``
    - 前缀：``tools/`` / ``ai_agent/`` / ``prompts/``
    - **禁**：``.env`` / ``.env.example`` / ``requirements.txt`` / ``.gitignore`` /
      ``.git/`` 目录

    严格匹配规则：
    - ``old_string`` 必须在文件中**唯一出现**（0 次或多次都失败）
    - 不唯一时扩展上下文（缩进 / 空格 / 换行精确匹配）

    使用要点：
    - **reason 写清楚动机**：会进 commit message 和 audit 日志，主人 review 时看
    - 改完简短告诉主人："我改了 X 的 Y 处，commit abc123；重启 server 生效；
      不喜欢可 ``self_rollback(1)`` 或 ``git revert abc123``"
    - **不要连续改同一文件 ≥ 3 次**。改 3 次还不对 → 停下问主人方向
    - 改 ``prompts/yuki.md``（你的人设核心）需要主人**明确要求**

    Args:
        path: 项目相对路径（如 ``"tools/files.py"``）
        old_string: 要替换的精确原文
        new_string: 替换成的新文本
        reason: 改动动机（一句话，会进 commit message + audit）

    Returns:
        成功提示 + commit hash，或失败原因（已自动 rollback）。
    """
    # 0. 子对话权限检查（受限模式直接拒绝自修改）
    restricted_err = _check_sub_restricted(config)
    if restricted_err:
        return restricted_err

    # 1. 路径校验
    abs_path, err = _validate_path(path)
    if err:
        return f"❌ {err}"
    if abs_path is None or not abs_path.is_file():
        return f"❌ 文件不存在：{path}"

    if not (reason or "").strip():
        return "❌ reason 不能为空（必须说明改动动机）"

    # 1.5 失败熔断（防"我再改一下就好"陷入死循环）
    loop_err = _check_edit_loop(path)
    if loop_err:
        return loop_err

    # 2. 改前 safety checkpoint
    safe_hash = _git_safety_checkpoint(f"edit {path}: {reason}")

    # 3. 读 + 校验 old_string
    try:
        text = abs_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"❌ 读取失败：{e}"

    if old_string == new_string:
        return "❌ old_string 和 new_string 相同，无操作"
    count = text.count(old_string)
    if count == 0:
        return f"❌ old_string 未在 {path} 中出现。请检查空白 / 缩进 / 大小写是否精确匹配"
    if count > 1:
        return f"❌ old_string 在 {path} 中出现 {count} 次，必须唯一。请扩展上下文使其唯一"

    # 4. 应用编辑（原子写，避免中断时残缺）
    new_text = text.replace(old_string, new_string, 1)
    try:
        _atomic_write_text(abs_path, new_text)
    except Exception as e:
        return f"❌ 写入失败：{e}"

    # 5. 改后校验
    verify_err = _verify_after_edit(abs_path)
    if verify_err:
        ok = _git_restore_path(abs_path)
        return (
            f"❌ 改动校验失败：{verify_err}\n"
            + ("已自动撤回（git restore）" if ok else "撤回失败 —— 请告知主人手动 git restore " + path)
        )

    # 6. commit 本次改动
    rel = abs_path.relative_to(PROJECT_ROOT).as_posix()
    _run_git(["add", "--", rel])
    commit_msg = f"[ai-edit] {path}: {reason[:100]}"
    r = _run_git(["commit", "-m", commit_msg])
    if r.returncode != 0:
        return (
            f"⚠️ 改动已写入但 commit 失败：{r.stderr[:200]}\n"
            f"主人重启 server 时会自动兜底 commit（auto_commit_pending）"
        )

    # 7. 增量更新代码索引（让 code_search / code_outline / find_references 立即看到新代码）
    try:
        from tools.code_indexer import get_indexer
        get_indexer().update_file(str(PROJECT_ROOT), rel)
    except Exception:
        pass  # 索引失败不阻塞 commit

    r = _run_git(["rev-parse", "--short", "HEAD"])
    new_hash = r.stdout.strip() if r.returncode == 0 else "<unknown>"

    msg = (
        f"✓ 已改 {path}（commit {new_hash}）。\n"
        f"原因：{reason}\n"
        f"重启 server 生效。撤回：self_rollback(1) 或 git revert {new_hash}。"
    )
    if safe_hash:
        msg += f"\n（改前已 safety commit：{safe_hash[:8]}）"
    msg += _check_audit_nudge()
    return msg


@tool
def self_write_file(
    path: str,
    content: str,
    reason: str,
    config: dict = None,
) -> str:
    """整文件覆盖式写入系统文件（你自己的代码 / prompt）。

    用途：整文件重写 / 新建文件。日常微调请用 ``self_edit_file``（精确字符串
    替换更稳，diff 也更清晰）。

    安全机制同 ``self_edit_file``：改前 safety commit、改后语法 / 长度校验、
    失败自动 git restore。

    Args:
        path: 项目相对路径
        content: 完整新内容（**完全替换**原文件）
        reason: 改动动机
    """
    restricted_err = _check_sub_restricted(config)
    if restricted_err:
        return restricted_err

    abs_path, err = _validate_path(path)
    if err:
        return f"❌ {err}"
    if abs_path is None:
        return f"❌ 路径无效：{path}"

    if not (reason or "").strip():
        return "❌ reason 不能为空"

    loop_err = _check_edit_loop(path)
    if loop_err:
        return loop_err

    safe_hash = _git_safety_checkpoint(f"write {path}: {reason}")

    # 备份旧内容（万一 git restore 失效）
    old_content = ""
    if abs_path.exists():
        try:
            old_content = abs_path.read_text(encoding="utf-8")
        except Exception:
            pass

    abs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _atomic_write_text(abs_path, content)
    except Exception as e:
        return f"❌ 写入失败：{e}"

    verify_err = _verify_after_edit(abs_path)
    if verify_err:
        if not _git_restore_path(abs_path):
            # git restore 失败 → 用本地备份回写
            if old_content:
                try:
                    _atomic_write_text(abs_path, old_content)
                except Exception:
                    pass
        return f"❌ 改动校验失败：{verify_err}（已撤回）"

    rel = abs_path.relative_to(PROJECT_ROOT).as_posix()
    _run_git(["add", "--", rel])
    commit_msg = f"[ai-edit] {path} (rewrite): {reason[:100]}"
    r = _run_git(["commit", "-m", commit_msg])
    if r.returncode != 0:
        return f"⚠️ 已写入但 commit 失败：{r.stderr[:200]}"

    # 增量更新代码索引（同 self_edit_file）
    try:
        from tools.code_indexer import get_indexer
        get_indexer().update_file(str(PROJECT_ROOT), rel)
    except Exception:
        pass

    r = _run_git(["rev-parse", "--short", "HEAD"])
    new_hash = r.stdout.strip() if r.returncode == 0 else "<unknown>"

    msg = (
        f"✓ 已覆盖写入 {path}（{len(content)} 字符，commit {new_hash}）。\n"
        f"原因：{reason}\n"
        f"重启 server 生效。撤回：self_rollback(1) 或 git revert {new_hash}。"
    )
    if safe_hash:
        msg += f"\n（改前已 safety commit：{safe_hash[:8]}）"
    msg += _check_audit_nudge()
    return msg


@tool
def self_rollback(steps: int = 1, config: dict = None) -> str:
    """撤销最近 N 次自修改（``git revert``，不是 reset，保留历史）。

    适用场景：
    - 主人说"刚那个改坏了 / 不喜欢，撤回吧"
    - 你自己觉得改错了方向

    机制：从 HEAD 起 revert 最近 N 个 commit。每个 revert 都产生新 commit
    （标记 ``Revert ...``），不破坏历史。**最终生效仍需主人重启 server**。

    Args:
        steps: 撤销最近多少个 commit（默认 1，最多 10）

    Returns:
        撤销结果摘要。
    """
    restricted_err = _check_sub_restricted(config)
    if restricted_err:
        return restricted_err
    n = max(1, min(int(steps or 1), 10))

    r = _run_git(["log", "-n", str(n), "--pretty=format:%h %s"])
    if r.returncode != 0 or not r.stdout.strip():
        return f"❌ 无法读 git log：{r.stderr[:200]}"

    commits = [line.strip() for line in r.stdout.strip().split("\n") if line.strip()]
    hashes = [line.split(maxsplit=1)[0] for line in commits]

    reverted: list[str] = []
    for h in hashes:
        rr = _run_git(["revert", "--no-edit", h])
        if rr.returncode != 0:
            return (
                f"⚠️ revert {h} 失败：{rr.stderr[:200]}\n"
                f"已成功 revert：{reverted}\n"
                f"请告知主人手动处理（可能有冲突需要解决）"
            )
        reverted.append(h)

    return (
        f"✓ 已撤销最近 {len(reverted)} 个 commit：{', '.join(reverted[:5])}"
        f"{'...' if len(reverted) > 5 else ''}\n"
        f"重启 server 生效。"
    )


@tool
def self_diff(last_n: int = 10) -> str:
    """看最近 N 个 commit 的概要（含自修改 + 主人修改 + auto-commit）。

    用途：
    - 主人问"你最近改了什么"
    - 你自己回顾改动方向
    - 改坏了想看自己改了什么

    Args:
        last_n: 看最近多少个 commit（默认 10，最多 30）

    Returns:
        commit 列表 + 每个 commit 改了哪些文件（``--stat`` 格式）。
    """
    n = max(1, min(int(last_n or 10), 30))
    r = _run_git(["log", "-n", str(n), "--stat", "--pretty=format:%h %ad %s%n", "--date=short"])
    if r.returncode != 0:
        return f"❌ git log 失败：{r.stderr[:200]}"

    out = r.stdout.strip()
    if not out:
        return "（没有 commit 历史）"
    if len(out) > 5000:
        out = out[:5000] + "\n...(已截断)"
    return out


# ── self_edit_with_test：量化自检（Worker/Reviewer 精神落地）─────────────────


@tool
def self_edit_with_test(
    path: str,
    old_string: str,
    new_string: str,
    reason: str,
    test_code: str,
    config: dict = None,
) -> str:
    """像 ``self_edit_file`` 一样精确替换，但**强制你先写自检脚本**，跑过才 commit。

    这是 Cadence 方案中"Worker/Reviewer 精神"的落地版本 ——
    迫使你改之前先想清楚**怎么验证这次改对了**，不能改完就交付。

    什么时候用：
    - 改动**有逻辑变化**（不只是改注释 / 改 prompt 文字）
    - 改动可能**影响其他模块**（改了被多处依赖的函数）
    - 你**自己拿不准**改对没改对
    - 改 ``ai_agent/`` 这种核心模块时建议**强制**用本工具而不是 self_edit_file

    什么时候**不需要**用：
    - 纯文字改动（注释 / docstring / prompt 段落措辞）
    - 你 100% 确定的小修补

    流程（你不用操心，工具自动跑）：
    1. 路径校验 + 熔断检查 + 改前 safety commit
    2. 应用 old_string → new_string
    3. .py 语法校验
    4. **把 test_code 写入临时文件 + 用 ``python`` 跑** — 必须 exit 0 才算通过
    5. 同时跑"项目加载冒烟"：``import agent; agent._full_prompt()`` 验证整套能起来
    6. 任一失败 → git restore 撤回改动 + 删临时 test 文件 + 返回失败输出
    7. 全过 → commit（test 文件不进 commit，是一次性的）

    Args:
        path:        项目相对路径
        old_string:  要替换的精确原文（必须唯一）
        new_string:  替换成的新文本
        reason:      改动动机
        test_code:   自检 Python 代码（**至少 80 字符 + 至少含一个 assert**）。
                     可以 ``import`` 项目内任何模块测真实行为。
                     好例子：
                       import importlib, tools.files
                       importlib.reload(tools.files)
                       wd_path = tools.files._next_version_path
                       from pathlib import Path
                       # 测改后的边界行为
                       assert callable(wd_path)
                       assert wd_path(Path('a.txt')).name == 'a_v2.txt'
                     坏例子（会被拒）：
                       assert True   # 太水，无效断言
                       print('ok')   # 没 assert

    Returns:
        成功提示 + commit hash + 自检脚本输出摘要；
        失败时返回详细的失败原因（已自动 rollback）。
    """
    restricted_err = _check_sub_restricted(config)
    if restricted_err:
        return restricted_err

    import subprocess as _subprocess
    import time as _time
    import uuid as _uuid

    # 0. test_code 基础质量校验
    tc = (test_code or "").strip()
    if len(tc) < 80:
        return (
            "❌ test_code 至少 80 字符（写有意义的自检 —— "
            "至少 import 一个模块 + 至少一个针对改动效果的 assert）"
        )
    if "assert" not in tc:
        return "❌ test_code 必须包含至少一个 assert（无 assert 的脚本不算验证）"
    if tc.strip() in ("assert True", "assert 1", "assert 1==1", "assert 1 == 1"):
        return "❌ 别水 ——『assert True』之类的不算验证"

    # 1. 路径校验
    abs_path, err = _validate_path(path)
    if err:
        return f"❌ {err}"
    if abs_path is None or not abs_path.is_file():
        return f"❌ 文件不存在：{path}"
    if not (reason or "").strip():
        return "❌ reason 不能为空"

    # 2. 熔断
    loop_err = _check_edit_loop(path)
    if loop_err:
        return loop_err

    # 3. 改前 safety commit
    safe_hash = _git_safety_checkpoint(f"edit-with-test {path}: {reason}")

    # 4. 读 + 校验 old_string
    try:
        text = abs_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"❌ 读取失败：{e}"
    if old_string == new_string:
        return "❌ old_string 和 new_string 相同"
    count = text.count(old_string)
    if count == 0:
        return f"❌ old_string 未在 {path} 中出现"
    if count > 1:
        return f"❌ old_string 在 {path} 中出现 {count} 次（必须唯一）"

    # 5. 应用编辑（原子写）
    new_text = text.replace(old_string, new_string, 1)
    try:
        _atomic_write_text(abs_path, new_text)
    except Exception as e:
        return f"❌ 写入失败：{e}"

    # 6. .py 语法校验（同 self_edit_file）
    verify_err = _verify_after_edit(abs_path)
    if verify_err:
        _git_restore_path(abs_path)
        return f"❌ 改动语法校验失败：{verify_err}（已 git restore）"

    # 7. 写临时 test + 跑
    tmp_dir = PROJECT_ROOT / ".sandbox" / "_self_edit_tests"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    test_id = _uuid.uuid4().hex[:8]
    test_path = tmp_dir / f"check_{test_id}.py"

    # 临时 test 自动包含项目根到 sys.path
    full_test = (
        "import sys, pathlib\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "# === 以下是 Agent 写的 test_code ===\n"
        + tc
    )
    try:
        _atomic_write_text(test_path, full_test)
    except Exception as e:
        _git_restore_path(abs_path)
        return f"❌ 写 test 文件失败：{e}（已 git restore）"

    def _cleanup_test():
        try:
            test_path.unlink(missing_ok=True)
        except Exception:
            pass

    # frozen 模式下 _sys.executable = yuki.exe，启动它会撞单实例锁弹窗
    # 必须找真 python.exe（venv / PATH / YUKI_PYTHON）
    from tools._common import find_real_python
    _py = find_real_python()
    if _py is None:
        _cleanup_test()
        _git_restore_path(abs_path)
        return (
            "❌ 打包模式下找不到真 Python 解释器跑测试脚本。\n"
            "解决：exe 旁建 .venv，或设 YUKI_PYTHON 环境变量指向 python.exe"
        )

    try:
        t0 = _time.monotonic()
        result = _subprocess.run(
            [_py, "-X", "utf8", str(test_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        elapsed_ms = int((_time.monotonic() - t0) * 1000)
    except _subprocess.TimeoutExpired:
        _cleanup_test()
        _git_restore_path(abs_path)
        return "❌ 自检脚本运行超过 60s 被强制终止（已 git restore）"
    except Exception as e:
        _cleanup_test()
        _git_restore_path(abs_path)
        return f"❌ 自检脚本启动失败：{e}（已 git restore）"

    if result.returncode != 0:
        _cleanup_test()
        _git_restore_path(abs_path)
        stderr_tail = (result.stderr or "")[-1200:]
        stdout_tail = (result.stdout or "")[-400:]
        return (
            f"❌ 自检脚本失败（returncode={result.returncode}，{elapsed_ms}ms）。"
            f"已 git restore 撤回改动。\n"
            + (f"\nstdout 末尾：\n{stdout_tail}" if stdout_tail.strip() else "")
            + (f"\nstderr 末尾：\n{stderr_tail}" if stderr_tail.strip() else "")
            + "\n请改进 test_code 或换思路再来。"
        )

    # 8. 项目加载冒烟：import agent + _full_prompt（确保整套没被改坏）
    smoke_code = (
        "import sys, pathlib;"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r});"
        "import agent; "
        "p = agent._full_prompt(); "
        "assert isinstance(p, str) and len(p) > 100, 'prompt 加载异常'"
    )
    try:
        smoke = _subprocess.run(
            [_py, "-X", "utf8", "-c", smoke_code],   # 复用上面找到的 _py（非 yuki.exe）
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as e:
        _cleanup_test()
        _git_restore_path(abs_path)
        return f"❌ 项目加载冒烟启动失败：{e}（已 git restore）"

    if smoke.returncode != 0:
        _cleanup_test()
        _git_restore_path(abs_path)
        return (
            f"❌ 项目加载冒烟失败 —— 改完后 import agent / _full_prompt() 挂了。"
            f"已 git restore。\n\nstderr 末尾：\n{(smoke.stderr or '')[-800:]}"
        )

    # 9. 全过 —— commit 改动（test 文件不进 commit）
    _cleanup_test()
    rel = abs_path.relative_to(PROJECT_ROOT).as_posix()
    _run_git(["add", "--", rel])
    commit_msg = f"[ai-edit] {path} (with-test): {reason[:100]}"
    r = _run_git(["commit", "-m", commit_msg])
    if r.returncode != 0:
        return f"⚠️ 改动已写入并通过自检，但 commit 失败：{r.stderr[:200]}"
    r = _run_git(["rev-parse", "--short", "HEAD"])
    new_hash = r.stdout.strip() if r.returncode == 0 else "<unknown>"

    out_preview = (result.stdout or "").strip()[:200]
    return (
        f"✓ 已改 {path}（commit {new_hash}）+ 自检通过（{elapsed_ms}ms）+ "
        f"项目加载冒烟通过。\n"
        f"原因：{reason}\n"
        + (f"自检输出：{out_preview}\n" if out_preview else "")
        + f"重启 server 生效。撤回：self_rollback(1) 或 git revert {new_hash}。"
        + (f"\n（改前 safety commit：{safe_hash[:8]}）" if safe_hash else "")
    )
