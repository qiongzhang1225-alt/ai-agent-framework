"""工作流仓库 —— 有希自建的"多工具组合配方"持久化后端。

与另外两个概念严格区分（别混淆、别合并）：

- ``skills/*.py``      = 有希自写的可复用 Python 函数（代码即工具），启动时加载、常驻。
- ``prompts/playbooks/*.md`` = **人**写的、稳定的领域参考手册（按关键词/工具路由，不随意改）。
- ``workflows/*.json`` = **有希自写**的多工具编排配方，自我改进（last_issues 累积踩坑教训）。

本模块是 ``workflows/*.json`` 的纯读写层，被 4 个工作流技能复用
（define_workflow / list_workflows / read_workflow / append_workflow_note）
以及 agent.py 的 ``format_workflows_for_prompt`` 注入索引。

历史：原 ``_wf_helper.py`` 用 subprocess + 硬编码 ``E:\\AI-Agent`` 路径，
frozen 模式下根本跑不通（sys.executable 是 yuki.exe，传 .py 参数会重启应用）。
本模块改为直接 import，目录走 ``paths.PROJECT_ROOT``。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from paths import PROJECT_ROOT

WF_DIR = PROJECT_ROOT / "workflows"


def _safe_name(name: str) -> str:
    """工作流名 → 安全文件名（路径分隔符 / 冒号换成全角，避免越界或非法文件名）。"""
    return name.replace("/", "／").replace("\\", "／").replace(":", "：")


def _path_for(name: str) -> Path:
    return WF_DIR / f"{_safe_name(name)}.json"


# ── 工作流规范 v1.1（参考社区 Agent Skills 的"描述纪律 + 固定结构"约定，零风险借格式）──
# 顶层字段：name / description / created / triggers / steps / last_issues
#   triggers   可选[str]：触发关键词（路由用，self-authored）。空则回退到"工作流名 + 引用工具"。
# step 标准形状：{tool, description, notes}
#   tool        用哪个工具（纯判断步可留空 ""）
#   description 这步做什么（统一用 description，废弃旧的 desc 别名）
#   notes       可选：坑/提醒，默认 ""


def _tool_tokens(raw: str) -> list[str]:
    """把 step.tool 文本切成候选工具名 token。

    真实数据里 tool 可能是 ``"execute_code / python-docx"`` 这种"工具 + 库说明"，
    所以按 ``/ , ; 空格 括号 |`` 切开，逐个 token 去跟工具表/近期工具比对。
    """
    import re
    return [t for t in re.split(r"[\s/,;（）()|]+", (raw or "").strip()) if t]


def _known_tool_names() -> set[str]:
    """当前已注册工具名集合（小写）。注册表没加载时返回空集（让校验自动跳过，不误报）。"""
    try:
        from ai_agent.tools import list_tools
        return {t.name.lower() for t in list_tools()}
    except Exception:
        return set()


def normalize_steps(steps: list | None) -> list:
    """把 steps 归一到标准 schema ``{tool, description, notes}``。

    - ``desc`` 键 → ``description``（迁移旧数据）
    - 缺失字段补默认值
    - 纯字符串步 → ``{tool:"", description:<str>, notes:""}``
    - 丢弃 schema 外的未知键（v1 固定三字段）
    """
    out: list[dict] = []
    for s in steps or []:
        if isinstance(s, dict):
            desc = s.get("description") or s.get("desc") or ""
            out.append({
                "tool": s.get("tool", ""),
                "description": desc,
                "notes": s.get("notes", ""),
            })
        else:
            out.append({"tool": "", "description": str(s), "notes": ""})
    return out


def lint_workflow(
    name: str,
    description: str,
    steps: list,
    triggers: list | None = None,
) -> list[str]:
    """对工作流做**非阻断**规范检查，返回警告列表（空列表=干净）。

    贴合本项目"警告不回滚"的哲学（同 verify_change）：只提醒，不挡保存。

    检查项：
    - description 偏短（路由发现的唯一依据，太短会找不到）
    - steps 为空 / 某步缺 description
    - **tool 引用校验**（独有优化 A）：step.tool 拿去比对**活的工具注册表**，
      一个 token 都对不上就提醒（可能改名/删除/笔误，也可能只是纯库名说明）。
    - triggers 为空（路由会偏弱）的轻提醒。
    """
    warns: list[str] = []
    norm = normalize_steps(steps)
    if len((description or "").strip()) < 8:
        warns.append("description 偏短（建议写清『什么场景触发 + 产出什么』，它是有希发现/选中工作流的唯一依据）")
    if not norm:
        warns.append("steps 为空")
    for i, s in enumerate(norm, 1):
        if not s["description"].strip():
            warns.append(f"第 {i} 步缺 description（说不清这步做什么）")

    # 独有优化 A：tool 引用校验（对照活的工具注册表）
    known = _known_tool_names()
    if known:  # 注册表没加载（如纯后端单测）就跳过，避免误报
        for i, s in enumerate(norm, 1):
            tool = s["tool"].strip()
            if not tool:
                continue
            toks = _tool_tokens(tool)
            if toks and not any(tok.lower() in known for tok in toks):
                warns.append(
                    f"第 {i} 步的 tool {tool!r} 在当前工具表里找不到对应"
                    "（可能工具改名/删除/笔误；若只是纯库名说明可忽略）"
                )

    if not (triggers or []):
        warns.append("未设 triggers（触发关键词）：路由只能靠工作流名 + 引用工具，命中偏弱；建议补几个高频触发词")
    return warns


def _resolve_path(name: str) -> Path | None:
    """工作流名 → 实际文件路径：先精确文件名，再按 name 字段模糊匹配。找不到返回 None。

    单独抽出来，让 read（拿 dict）与 bump_usage（要原地回写同一文件）共用同一套解析，
    不会出现"读的是 A 文件、写回 B 文件"的错位。
    """
    exact = _path_for(name)
    if exact.is_file():
        return exact
    if not WF_DIR.exists():
        return None
    for f in WF_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("name") == name:
            return f
    return None


def read(name: str) -> dict | None:
    """按名读一个工作流 dict：先精确文件名匹配，再按 name 字段模糊匹配。找不到返回 None。"""
    p = _resolve_path(name)
    if p is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write(
    name: str,
    description: str,
    steps: list,
    last_issues: list | None = None,
    triggers: list | None = None,
    stats: dict | None = None,
) -> Path:
    """写入/覆盖一个工作流。

    覆盖既有工作流时**保留原 created 日期**（不再像旧版那样硬编码刷成某天）；
    新建则用今天。``triggers`` / ``stats`` 传 None 时同样**保留已有值**（让 append_note、
    redefine 等不带这些字段的回写不会把触发词或使用计数清空）。
    """
    WF_DIR.mkdir(parents=True, exist_ok=True)
    existing = read(name)
    created = (existing or {}).get("created") or str(date.today())
    if triggers is None:
        triggers = (existing or {}).get("triggers", [])
    if stats is None:
        stats = (existing or {}).get("stats") or {"used": 0, "last_used": None}
    data = {
        "name": name,
        "description": description,
        "created": created,
        "triggers": triggers or [],
        "steps": normalize_steps(steps),  # 落盘统一归一到 v1 schema（顺带迁移旧 desc 键）
        "last_issues": last_issues or [],
        "stats": stats,  # 使用统计（独有优化 C）：used 次数 + last_used 日期
    }
    path = _path_for(name)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def bump_usage(name: str) -> dict | None:
    """记一次工作流被**真正取用**（read_workflow 触发）：used +1、last_used=今天。

    独有优化 C —— 给"哪些工作流值得留 / 该淘汰"提供客观依据。
    取"被 read_workflow 取回"为使用信号（= 有希真的把它拉出来照做），
    比"在索引里被路由展示"强：展示≠用。

    实现上直接读改写目标 JSON、**只动 stats**，其余字段原样保留
    （不重新 normalize steps，避免无关 diff）。找不到工作流返回 None。
    """
    p = _resolve_path(name)
    if p is None:
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    stats = d.get("stats") or {"used": 0, "last_used": None}
    stats["used"] = int(stats.get("used", 0) or 0) + 1
    stats["last_used"] = str(date.today())
    d["stats"] = stats
    try:
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return None
    return stats


def match_workflows_by_text(text: str) -> list[dict]:
    """给一段自由文本（如 postmortem 的 任务+教训），返回**语义可能相关**的工作流索引项。

    供 postmortem↔workflow 桥（独有优化 D）用：复盘写完后判断这条教训
    是否该沉淀进某个已有工作流的 last_issues。

    匹配信号**只用 triggers + 工作流名**（语义身份），刻意**不含派生 tool token** ——
    tool 太泛（execute_code 几乎每个工作流都有），拿来判"这教训是否属于这工作流"
    会大量误命中。返回顺序同 list_all。
    """
    text_l = (text or "").lower()
    if not text_l.strip():
        return []
    out: list[dict] = []
    for w in list_all():
        keys = list(w.get("triggers", []) or []) + [w.get("name", "")]
        if any(k and str(k).lower() in text_l for k in keys):
            out.append(w)
    return out


# ── 独有优化 E：自动结晶（反复出现的多工具套路 → 提议固化成工作流）──────────
# 思路 = 轻量级"频繁有序模式挖掘"：把对话按任务切成工具序列，挖反复出现的
# 有序工具 n-gram，剔掉已被现有工作流覆盖的，剩下的就是"你一直在手搓、却没固化"
# 的套路。只**提议**不自动建（同 C/D：不污染策展数据，让有希判断+补全再存）。

# 工作流元工具 / 通用胶水工具：不该作为"套路骨架"的锚点，n-gram 前先滤掉，
# 否则会提议"关于使用工作流工具的工作流"这种自指噪声。
_CRYSTALLIZE_IGNORE = {
    "read_workflow", "define_workflow", "list_workflows",
    "append_workflow_note", "suggest_workflow", "search_tools",
    "todo_write", "todo_read", "ask_user",
}


def extract_task_tool_sequences(conv: dict | None) -> list[list[str]]:
    """把一段对话按 user 消息切成"每个任务的有序工具名序列"。

    一个任务 = 从一条 user 消息到下一条 user 消息之间、assistant 依次调过的工具。
    只收非空工具序列（纯聊天回合跳过）。供自动结晶用。空对话返回 []。
    """
    messages = (conv or {}).get("messages") or []
    tasks: list[list[str]] = []
    cur: list[str] = []
    started = False
    for m in messages:
        if m.get("role") == "user":
            if started and cur:
                tasks.append(cur)
            cur = []
            started = True
            continue
        for tc in (m.get("tool_calls") or []):
            name = (tc.get("name") or "").strip()
            if name:
                cur.append(name)
    if started and cur:
        tasks.append(cur)
    return tasks


def _collapse_runs(seq: list[str]) -> list[str]:
    """折叠连续重复工具：execute_code×5 → 一个 execute_code（反映逻辑步，非重试次数）。"""
    out: list[str] = []
    for t in seq:
        if not out or out[-1] != t:
            out.append(t)
    return out


def find_crystallization_candidates(
    task_sequences: list[list[str]],
    *,
    n: int = 3,
    min_repeat: int = 2,
    covered_tool_sets: list[set] | None = None,
    max_candidates: int = 3,
) -> list[dict]:
    """从多个任务的工具序列里找**反复出现、尚未固化**的有序多工具 n-gram。

    - 每个任务先折叠连续重复 + 滤掉元工具，取长度 n 的连续 n-gram（有序＝配方骨架）。
    - 按"出现在多少个**不同任务**里"计数（同一任务内重复只算一次），≥ min_repeat 入选。
    - n-gram 工具集 ⊆ 某现有工作流 tools 的剔除（已固化，别重复提议）。
    - 去重叠（同工具集不同序只留计数高的）+ 截断到 max_candidates。

    返回 [{"sequence": [...], "count": N}]，按出现任务数降序。纯函数，不碰磁盘。
    """
    from collections import defaultdict
    covered = [set(s) for s in (covered_tool_sets or []) if s]
    ngram_tasks: dict[tuple, int] = defaultdict(int)
    for seq in task_sequences:
        cleaned = [t for t in _collapse_runs(seq) if t not in _CRYSTALLIZE_IGNORE]
        if len(cleaned) < n:
            continue
        grams = {tuple(cleaned[i:i + n]) for i in range(len(cleaned) - n + 1)}
        for g in grams:
            ngram_tasks[g] += 1

    cands: list[dict] = []
    for g, cnt in ngram_tasks.items():
        if cnt < min_repeat:
            continue
        gset = set(g)
        if any(gset <= cov for cov in covered):
            continue
        cands.append({"sequence": list(g), "count": cnt})

    cands.sort(key=lambda c: (-c["count"], -len(c["sequence"])))
    kept: list[dict] = []
    for c in cands:
        cset = set(c["sequence"])
        if any(cset <= set(k["sequence"]) for k in kept):
            continue
        kept.append(c)
        if len(kept) >= max_candidates:
            break
    return kept


def format_crystallization_suggestion(candidates: list[dict]) -> str:
    """把候选渲染成给有希看的"要不要固化"提议 + 现成 define_workflow 骨架。空→空串。"""
    if not candidates:
        return ""
    lines = [
        "【可固化为工作流的重复套路（优化 E：自动结晶建议）】",
        "你最近在多个任务里重复跑了下面的多工具序列，却还没固化成工作流。",
        "若确属稳定打法，用 define_workflow 存下来（下次 read_workflow 照做、还能 append 踩坑）：",
        "",
    ]
    for c in candidates:
        arrow = " → ".join(c["sequence"])
        lines.append(f"- {arrow}（在 {c['count']} 个任务里出现）")
    steps_skel = ", ".join(
        '{"tool": "%s", "description": "这步做什么"}' % t
        for t in candidates[0]["sequence"]
    )
    lines += [
        "",
        "骨架（填好 description / triggers 再存）：",
        'define_workflow(name="给它起个名", description="什么场景触发 + 产出什么", '
        f'triggers=["关键词1", "关键词2"], steps=[{steps_skel}])',
    ]
    return "\n".join(lines)


def list_all() -> list[dict]:
    """列出所有工作流的索引。

    每项含：name / description / steps(步数) / issues(教训数) /
    **triggers**(触发词，路由用) / **tools**(从各步 tool 派生的去重 token，sticky 路由用) /
    **used**(取用次数，优化 C) / **last_used**(最近取用日期)。
    （``list_workflows`` 技能只读前四个键，多出的键无害。）
    """
    if not WF_DIR.exists():
        return []
    result: list[dict] = []
    for f in sorted(WF_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            tools: list[str] = []
            for s in d.get("steps", []):
                raw = s.get("tool", "") if isinstance(s, dict) else ""
                for tok in _tool_tokens(raw):
                    if tok not in tools:
                        tools.append(tok)
            stats = d.get("stats") or {}
            result.append({
                "name": d.get("name", f.stem),
                "description": d.get("description", ""),
                "steps": len(d.get("steps", [])),
                "issues": len(d.get("last_issues", [])),
                "triggers": d.get("triggers", []),
                "tools": tools,
                "used": int(stats.get("used", 0) or 0),
                "last_used": stats.get("last_used"),
            })
        except Exception:
            result.append({
                "name": f.stem, "description": "（读取失败）",
                "steps": 0, "issues": 0, "triggers": [], "tools": [],
                "used": 0, "last_used": None,
            })
    return result


def append_note(name: str, issue: str, fix: str) -> str:
    """给某工作流追加一条踩坑记录（date 自动填今天）。返回结果文本。"""
    d = read(name)
    if not d:
        return f"未找到工作流: {name}"
    d.setdefault("last_issues", []).append(
        {"date": str(date.today()), "issue": issue, "fix": fix}
    )
    write(d["name"], d.get("description", ""), d.get("steps", []), d["last_issues"])
    return f"已追加，共 {len(d['last_issues'])} 条"


def _fmt_step(i: int, step) -> str:
    """格式化单个步骤。容忍 step 是 dict（键 tool / description 或 desc / notes）或纯字符串。

    两个真实工作流的 step schema 不一致（一个用 ``description``+``notes``，
    一个只用 ``desc``），所以这里要对键名宽容。
    """
    if isinstance(step, dict):
        tool = step.get("tool", "")
        desc = step.get("description") or step.get("desc") or ""
        notes = step.get("notes", "")
        head = f"{i}. "
        if tool:
            head += f"[{tool}] "
        head += desc
        if notes:
            head += f"  （注: {notes}）"
        return head
    return f"{i}. {step}"


def render(name: str) -> str:
    """把一个工作流渲染成给有希照着做的可读文本（步骤 + 历史踩坑）。找不到时给提示并列出现有名字。"""
    d = read(name)
    if not d:
        avail = [w["name"] for w in list_all()]
        hint = ("，现有：" + "、".join(avail)) if avail else ""
        return f"未找到工作流「{name}」{hint}"
    lines = [
        f"工作流「{d.get('name', name)}」",
        d.get("description", ""),
    ]
    stats = d.get("stats") or {}
    if stats.get("used"):
        lines.append(f"（已取用 {stats['used']} 次，最近 {stats.get('last_used') or '?'}）")
    lines += ["", "步骤："]
    steps = d.get("steps", [])
    if steps:
        for i, s in enumerate(steps, 1):
            lines.append("  " + _fmt_step(i, s))
    else:
        lines.append("  （无步骤）")
    issues = d.get("last_issues", [])
    if issues:
        lines.append("")
        lines.append("历史踩坑（照着避开）：")
        for it in issues:
            lines.append(f"  - [{it.get('date', '')}] {it.get('issue', '')} → {it.get('fix', '')}")
    return "\n".join(lines)


_WF_INDEX_HEADER = (
    "【你已有的工作流（多工具组合配方，持久化在 workflows/）】\n"
    "做同类任务前先 `read_workflow(名称)` 取回完整步骤与历史踩坑，照着做；\n"
    "做完若发现新坑/更优解，用 `append_workflow_note` 记下来，让它越用越准。"
)


def format_workflows_index(items: list[dict], routed: bool = False) -> str:
    """把**给定**的一批工作流索引项格式化成 prompt 段落。

    只给索引（名字 + 描述 + 步骤/教训数），**不展开步骤** —— 让有希先
    ``read_workflow(名称)`` 再照着做（渐进式披露，省 token）。``items`` 空返回空串。

    Args:
        items: ``list_all()`` 返回项的子集（路由后只剩命中的）。
        routed: True 时在 header 标注"按当前任务筛选"，提示这不是全部。
    """
    if not items:
        return ""
    header = _WF_INDEX_HEADER
    if routed:
        header += "\n（下面只列出与当前任务相关的；全部工作流用 `list_workflows` 查看）"
    lines = [header, ""]
    for w in items:
        lines.append(
            f"- `{w['name']}`: {w['description']}"
            f"（{w['steps']} 步, {w['issues']} 条教训）"
        )
    return "\n".join(lines)


def format_workflows_pointer(count: int) -> str:
    """路由后一个都没命中时的兜底：给一行指针，保住可发现性而不稀释 prompt。"""
    if count <= 0:
        return ""
    return (
        f"【工作流】你存了 {count} 个工作流（多工具配方），"
        "当前任务未命中；需要时用 `list_workflows` 查看、`read_workflow(名称)` 取用。"
    )


def format_workflows_for_prompt() -> str:
    """生成"你已有的工作流"索引段落（**全量**，不路由）。

    保留给 conv=None（CLI / smoke）或工作流数量未超阈值时的全量注入路径。
    """
    return format_workflows_index(list_all(), routed=False)
