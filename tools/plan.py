"""plan_task 工具 —— 复杂任务开工前的"先想后做"约束。

设计动机：
DeepSeek V4 在复杂任务上的最大失败模式是"看到任务就开 Edit"，
没有先比较实现路径，最后做出最差方案（白天主题 60+ commit 翻车）。
本工具不是 hard block，是 forcing function：

- 在 prompts/system.md 里硬约束："UI / 多文件改动 / 自修改 prompts 开工前
  必须先调 plan_task"
- 调用结果写入 .sandbox/_meta/<tid>/plans/<ts>.json + audit.jsonl
- 主人能在事后审阅 plan vs 实际做法的偏差

不强制：plan 之后她依然可以走偏，但至少思考过；
       audit 里能看到她是否跳过了 plan。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ai_agent import tool
from paths import META_DIR


def _plans_dir(thread_id: str) -> Path:
    d = META_DIR / thread_id / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_recent_plans(thread_id: str, n: int = 3) -> list[dict]:
    """读取最近 N 个 plan（供 system prompt 注入用）。"""
    d = META_DIR / thread_id / "plans"
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict] = []
    for f in files[:n]:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


@tool
def plan_task(
    task: str,
    candidates: list[str],
    choice: str,
    why: str,
    verify_plan: str,
    risks: str = "",
    config: dict = None,
) -> str:
    """**复杂任务开工前调一次**：把你打算做什么、为什么这样做、怎么验证写出来。

    什么时候必须调（硬约束）：
    - **UI / CSS / HTML 改动**（哪怕只改一行 CSS 也算 —— UI 翻车成本极高）
    - **跨 ≥2 个文件**的改动（重构、加新工具、改主流程）
    - **改 prompts/ 下任何文件**（影响你自己的行为）
    - **改 agent.py / server.py / ai_agent/loop.py** 等核心入口

    什么时候不用调：
    - 单个 execute_code / run_command / 单文件简单查询
    - 改个错别字、补一行 import、加一条 log
    - 只读不写的探索（self_read_file、grep、glob）

    设计意图（必读）：
    你之前做白天主题 60+ commit 还失败，根因是"看到任务就开 Edit"
    —— 没比较"用 CSS Variables vs 用 .theme-light 一对一覆盖"的成本。
    plan_task 不是束缚，是让你**至少**思考过路径选择再动手。
    如果只想到 1 条路径，往往就是想偏的信号 —— 强制自己想第 2 条。

    Args:
        task: 一句话任务概述（"做白天主题"、"加 verify_change 工具"）
        candidates: **≥2 条**实现路径候选。每条一句话描述。
                    例：["CSS Variables 集中 token", ".theme-light 覆盖每个原子类",
                          "用 color-scheme: light + filter"]
        choice: 选哪条。必须是 candidates 里的一条（字符串匹配）。
        why: 为什么选这条。强调**客观成本**（工程量 / 维护性 / 已知坑），
              不要写"因为简单"这种空话。
        verify_plan: 做完怎么验证。具体到命令：
              "grep 'theme-light' style.css 应该 ≥ 30 处" /
              "curl /static/style.css 应该 200" / "截图对比空对话 + 密集对话"
        risks: 已知风险。可空。但**最好填**，比如"CSS 转义 \\/ 容易写成 \\\\/"
        config: (注入参数，不用填)

    Returns:
        plan_id（如 "plan_20260530_103015"）+ 一句确认 + 提醒。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "default")

    # 字段校验
    errors: list[str] = []
    task = (task or "").strip()
    if not task:
        errors.append("task 不能为空")
    if not isinstance(candidates, list) or len(candidates) < 2:
        errors.append("candidates 必须 ≥ 2 条 —— 强迫你想第 2 条路径")
    elif any(not str(c).strip() for c in candidates):
        errors.append("candidates 里不能有空字符串")

    choice = (choice or "").strip()
    if not choice:
        errors.append("choice 不能为空")
    elif candidates and choice not in [str(c).strip() for c in candidates]:
        errors.append(f"choice 必须是 candidates 里的一条 —— 你写的 {choice!r} 不在候选里")

    why = (why or "").strip()
    if len(why) < 10:
        errors.append("why 太短（< 10 字符）—— 写清楚客观理由（工程量 / 维护 / 风险）")

    verify_plan = (verify_plan or "").strip()
    if len(verify_plan) < 10:
        errors.append("verify_plan 太短 —— 写出具体的验证命令")

    if errors:
        return "❌ plan_task 校验失败:\n  - " + "\n  - ".join(errors)

    # 写盘
    ts = datetime.now()
    plan_id = f"plan_{ts.strftime('%Y%m%d_%H%M%S')}"
    record = {
        "plan_id": plan_id,
        "ts": ts.isoformat(),
        "thread_id": thread_id,
        "task": task,
        "candidates": [str(c).strip() for c in candidates],
        "choice": choice,
        "why": why,
        "verify_plan": verify_plan,
        "risks": (risks or "").strip(),
    }

    path = _plans_dir(thread_id) / f"{plan_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    # 短回执
    parts = [
        f"✅ plan 已记录：{plan_id}",
        f"任务：{task}",
        f"路径：{choice}",
        f"验证：{verify_plan}",
        "",
        "提醒：",
        "- 动手中如果发现方案不对，**停下来重新 plan_task**（不要硬撑）",
        "- 完成后调 verify_change 自检",
        "- 任务结束调 write_postmortem 总结",
    ]
    return "\n".join(parts)
