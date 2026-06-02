"""持久化技能（L2）：让 LLM 把可复用能力沉淀为 ``skills/<name>.py`` 文件。

- ``define_skill``  注册并持久化新技能
- ``list_skills``   列出所有已注册技能
- ``delete_skill``  卸载并删除技能文件

模块导入时会自动扫描 ``skills/*.py`` 并加载（``_load_all_skills()``）。
"""
from __future__ import annotations

import ast
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_agent import tool
from paths import PROJECT_ROOT, SKILLS_DIR

# 删除技能时的"回收站"，C4 暴露 restore 工具
SKILLS_TRASH_DIR = PROJECT_ROOT / ".skills_trash"
SKILLS_TRASH_KEEP_DAYS = 7


# 用于检查 skill 的合法性（define_skill / delete_skill 中拒绝"覆盖核心工具"的保护）
_CORE_TOOL_NAMES = frozenset({
    "web_search", "fetch_webpage", "calculate", "get_current_datetime",
    "execute_code", "remember", "recall",
    "update_memory", "merge_memories", "forget_memory",
    "restore_memory", "restore_skill", "list_trash",
    "read_file", "write_file", "edit_file", "grep", "glob",
    "define_skill", "list_skills", "delete_skill",
    "todo_write", "todo_read",
    "ask_user", "vision_describe", "audit_query", "request_pip_install",
    "run_command",
    "self_read_file", "self_edit_file", "self_write_file", "self_rollback", "self_diff",
    "self_edit_with_test",
    "spawn_sub_conversation",
    "screenshot_and_describe",
})


def _load_skill_file(skill_path: Path) -> tuple[bool, str]:
    """加载单个 skill 文件，注册到 _REGISTRY。返回 (success, msg)。"""
    from ai_agent.tools import build_tool_meta, register

    name = skill_path.stem
    if not name.isidentifier() or name.startswith("_"):
        return False, f"跳过 {skill_path.name}：文件名不是合法标识符"

    try:
        code = skill_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"读取失败 {skill_path.name}: {e}"

    # 解析 AST 确保有同名函数定义
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"语法错误 {skill_path.name}: {e}"

    has_target = any(
        isinstance(node, ast.FunctionDef) and node.name == name
        for node in tree.body
    )
    if not has_target:
        return False, f"{skill_path.name} 必须含 def {name}(...) 同名函数"

    # 在隔离 namespace 中 exec
    ns: dict[str, Any] = {"__file__": str(skill_path), "__name__": f"skill_{name}"}
    try:
        exec(compile(code, str(skill_path), "exec"), ns)
    except Exception as e:
        return False, f"执行失败 {skill_path.name}: {type(e).__name__}: {e}"

    func = ns.get(name)
    if not callable(func):
        return False, f"{skill_path.name} 中 {name} 不是 callable"

    try:
        meta = build_tool_meta(func)
        register(meta)
    except Exception as e:
        return False, f"注册失败 {skill_path.name}: {e}"

    return True, f"已加载技能: {name}"


def _load_all_skills() -> list[str]:
    """启动时扫描 skills/ 加载所有技能。返回加载成功的名字列表。"""
    if not SKILLS_DIR.exists():
        return []
    loaded: list[str] = []
    for skill_path in sorted(SKILLS_DIR.glob("*.py")):
        if skill_path.name.startswith("_") or skill_path.name == "README.py":
            continue
        ok, msg = _load_skill_file(skill_path)
        if ok:
            loaded.append(skill_path.stem)
        else:
            print(f"[skill] {msg}")
    return loaded


# 模块导入时立即加载所有持久化技能
_load_all_skills()


def format_skills_for_prompt() -> str:
    """生成"你已有的自定义技能"段落，附到 SYSTEM_PROMPT 末尾。

    让私人助手在每次新对话开始时就能"看到"自己有什么技能，不必先调 list_skills。
    无技能时返回空字符串。
    """
    if not SKILLS_DIR.exists():
        return ""
    from ai_agent.tools import get_tool

    lines: list[str] = []
    for p in sorted(SKILLS_DIR.glob("*.py")):
        if p.name.startswith("_") or p.name == "README.py":
            continue
        name = p.stem
        t = get_tool(name)
        if not t:
            continue
        # 取 description 第一行（一般是 docstring 首行摘要）
        head = t.description.split("\n", 1)[0].strip()[:100]
        lines.append(f"- `{name}`: {head}")

    if not lines:
        return ""
    return (
        "【你已有的自定义技能（持久化在 skills/，可直接调用）】\n"
        + "\n".join(lines)
    )


async def _check_restricted_skills(action_summary: str, config: dict) -> str | None:
    """技能工具的受限子对话权限网关（同 memory_tools._check_restricted_or_pass）。"""
    from tools.dialog import is_restricted_sub, require_master_approval
    if not is_restricted_sub(config):
        return None
    approved, raw = await require_master_approval(action_summary, config)
    if not approved:
        return (
            f"❌ 主人拒绝（或超时）了这次技能编辑请求：{raw[:60]}。"
            f"技能是全局资产，受限子对话需主人批准；请换到主对话或高级模式做。"
        )
    return None


@tool
async def define_skill(name: str, code: str, description: str = "", config: dict = None) -> str:
    """注册一个**新技能**（可复用的 Python 函数）并**持久化**到 skills/ 目录。

    什么时候用：
    - 主人明确说"以后..."、"把这个变成工具"、"保存为技能"、"做个 ... 函数"
    - 你识别出主人会反复需要某个操作，且**主人同意**封装

    什么时候**不要**用：
    - 主人没明确要求时，不要"自作主张"封装。一次性任务用 execute_code 即可
    - 已存在同名工具时（会拒绝以避免覆盖核心工具）

    code 的格式要求（很严格）：
    - 必须含 `def <name>(...)` 同名顶层函数
    - 函数应有 docstring（会作为 LLM 看到的工具描述）
    - 参数类型注解用 str / int / float / bool / list / dict
    - 函数体可以 import 任何标准库或预装库（pandas / requests / Pillow 等）

    示例：
        define_skill(
            name="sha256_hex",
            description="计算文本的 SHA256 十六进制摘要",
            code='''
def sha256_hex(text: str) -> str:
    \"\"\"返回 text 的 SHA256 十六进制摘要。\"\"\"
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()
''',
        )

    重启 server 后此技能会被自动加载，无需重新 define。
    **受限模式子对话**调本工具会先弹窗请求主人批准。
    """
    denied = await _check_restricted_skills(
        f"define_skill(name={name!r}, description={description[:60]!r}, code 长度={len(code or '')})",
        config,
    )
    if denied:
        return denied

    from ai_agent.tools import build_tool_meta, register, get_tool

    if not name.isidentifier() or name.startswith("_"):
        return f"错误: name {name!r} 必须是合法 Python 标识符且不能以下划线开头"
    if name in _CORE_TOOL_NAMES:
        return f"错误: {name!r} 与内置工具同名，不允许覆盖"
    if get_tool(name) is not None:
        return f"错误: 技能 {name!r} 已存在。先调 delete_skill({name!r}) 再重新定义。"

    # AST 校验
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"错误: code 语法错误: {e}"
    has_target = any(
        isinstance(node, ast.FunctionDef) and node.name == name
        for node in tree.body
    )
    if not has_target:
        return f"错误: code 必须含顶层 def {name}(...) 函数定义"

    # exec 校验 + 拿到 callable
    ns: dict[str, Any] = {"__name__": f"skill_{name}"}
    try:
        exec(compile(code, f"<skill:{name}>", "exec"), ns)
    except Exception as e:
        return f"错误: code 执行失败: {type(e).__name__}: {e}"
    func = ns.get(name)
    if not callable(func):
        return f"错误: 执行后 namespace 没有可调用的 {name}"

    # 若用户传了 description 但函数没 docstring，用 description 补
    if description and not (func.__doc__ or "").strip():
        func.__doc__ = description

    # 构造元数据并注册
    try:
        meta = build_tool_meta(func)
    except Exception as e:
        return f"错误: 构造元数据失败: {e}"
    if description:
        meta.description = description
    register(meta)

    # 持久化到 skills/<name>.py
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skill_path = SKILLS_DIR / f"{name}.py"
    skill_path.write_text(code, encoding="utf-8")

    count = sum(1 for f in SKILLS_DIR.glob("*.py") if not f.name.startswith("_"))
    return (
        f"技能 {name!r} 已注册并持久化到 skills/{name}.py，"
        f"下次重启自动加载。当前共 {count} 个自定义技能。"
    )


@tool
def list_skills() -> str:
    """列出 skills/ 目录中所有自定义技能（持久化的 + 当前对话新增的）。

    返回每个技能的 name + description 摘要。
    """
    if not SKILLS_DIR.exists():
        return "（尚无自定义技能）"
    skills = []
    from ai_agent.tools import get_tool
    for p in sorted(SKILLS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        name = p.stem
        t = get_tool(name)
        if t:
            desc = t.description.split("\n", 1)[0][:80]
            skills.append(f"- {name}: {desc}")
        else:
            skills.append(f"- {name}: (未加载 / 失败)")
    if not skills:
        return "（尚无自定义技能）"
    return "\n".join(skills)


def _move_skill_to_trash(skill_path: Path) -> Path:
    """删除技能前把文件移到 trash，便于 C4 的 restore_skill 找回。

    返回 trash 路径；失败时只 print 不抛（不能因为备份失败阻塞删除）。
    """
    try:
        day_dir = SKILLS_TRASH_DIR / datetime.now().strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        # 文件名带 timestamp 避免同名（同一天先删后建再删的情况）
        ts = datetime.now().strftime("%H%M%S")
        target = day_dir / f"{skill_path.stem}__{ts}.py"
        seq = 1
        while target.exists():
            target = day_dir / f"{skill_path.stem}__{ts}_{seq}.py"
            seq += 1
        shutil.move(str(skill_path), str(target))
        _cleanup_skills_trash()
        return target
    except Exception as e:
        print(f"[skills.trash] 移入 trash 失败（退化到直接删除）: {e}")
        try:
            skill_path.unlink()
        except Exception:
            pass
        return Path()


def _cleanup_skills_trash() -> int:
    """清理超过 SKILLS_TRASH_KEEP_DAYS 天的 trash 日目录。"""
    if not SKILLS_TRASH_DIR.exists():
        return 0
    cutoff = datetime.now().timestamp() - SKILLS_TRASH_KEEP_DAYS * 86400
    cleaned = 0
    for day_dir in SKILLS_TRASH_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            if day_dir.stat().st_mtime < cutoff:
                shutil.rmtree(day_dir, ignore_errors=True)
                cleaned += 1
        except Exception:
            pass
    return cleaned


def list_skills_trash(limit: int = 100) -> list[dict]:
    """列出 trash 里的所有技能快照（最新在前）。"""
    if not SKILLS_TRASH_DIR.exists():
        return []
    items: list[dict] = []
    for day_dir in SKILLS_TRASH_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        for f in day_dir.glob("*.py"):
            try:
                # 文件名形如 weather__HHMMSS.py 或 weather__HHMMSS_1.py
                stem = f.stem
                name = stem.split("__")[0]
                items.append({
                    "name": name,
                    "file": str(f),
                    "trashed_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                })
            except Exception:
                continue
    items.sort(key=lambda x: x["trashed_at"], reverse=True)
    return items[:limit]


def restore_skill_from_trash(name: str) -> dict:
    """把最近被删的同名技能从 trash 恢复回 skills/ 并注册。

    Raises:
        KeyError: 没找到匹配的快照
        FileExistsError: 同名技能已存在（应先 delete 才能恢复）
    """
    target = SKILLS_DIR / f"{name}.py"
    if target.exists():
        raise FileExistsError(f"技能 {name!r} 当前已存在，无法恢复（请先 delete_skill）")

    candidates = [it for it in list_skills_trash(limit=10000) if it["name"] == name]
    if not candidates:
        raise KeyError(f"trash 里没有名为 {name!r} 的技能快照")
    src = Path(candidates[0]["file"])
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    ok, msg = _load_skill_file(target)
    return {
        "restored_name": name,
        "from_trash": str(src),
        "to": str(target),
        "load_ok": ok,
        "load_msg": msg,
    }


@tool
async def restore_skill(name: str, config: dict = None) -> str:
    """从回收站恢复一个被 delete_skill 删掉的技能（7 天内有效）。受限子对话需主人批准。

    什么时候用：
    - 主人说"那个技能恢复一下"、"我不该删 xxx"
    - 你识别到刚删的技能其实还需要

    参数：
        name: 技能名（不带 .py）

    返回：恢复结果（含文件路径 + 注册状态）。
    """
    denied = await _check_restricted_skills(f"restore_skill(name={name!r})", config)
    if denied:
        return denied

    try:
        res = restore_skill_from_trash(name)
    except (KeyError, FileExistsError, ValueError) as e:
        return f"恢复失败：{e}"
    status = "已注册" if res["load_ok"] else f"注册失败 - {res['load_msg']}"
    return f"已恢复技能 {name!r} 到 skills/{name}.py（{status}）"


@tool
async def delete_skill(name: str, config: dict = None) -> str:
    """删除一个自定义技能（从 _REGISTRY 卸载 + 移到 .skills_trash/）。受限子对话需主人批准。

    Args:
        name: 技能名（不带 .py）

    不能删除核心内置工具（calculate / execute_code 等会被拒绝）。
    技能文件不会真删，而是移到 ``.skills_trash/YYYY-MM-DD/<name>__HHMMSS.py``，
    7 天后才真删。期间可用 restore_skill 找回。
    """
    denied = await _check_restricted_skills(f"delete_skill(name={name!r})", config)
    if denied:
        return denied

    from ai_agent.tools import unregister

    if name in _CORE_TOOL_NAMES:
        return f"错误: {name!r} 是内置工具，不允许删除"
    skill_path = SKILLS_DIR / f"{name}.py"
    if not skill_path.exists():
        return f"错误: 技能 {name!r} 不存在（skills/{name}.py 不存在）"

    trash_path = _move_skill_to_trash(skill_path)
    unregister(name)
    if trash_path and trash_path.exists():
        return f"已删除技能 {name!r}（移入 {trash_path.name}，7 天内可 restore_skill 找回）"
    return f"已删除技能 {name!r}（文件 + 注册表；trash 备份失败但删除已完成）"
