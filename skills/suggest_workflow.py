def suggest_workflow(config: dict = None) -> str:
    """扫本对话历史，找**反复出现却还没固化**的多工具套路，建议 define_workflow 固化（优化 E）。

    什么时候用：
    - 你感觉自己最近在不同任务里一遍遍重复同一套多步操作（如 读模板 → execute_code → 截图核对）。
    - 主人问"有什么可以固化 / 沉淀成工作流的吗"。

    机制：读本对话的工具调用历史，按 user 消息切成"每个任务的工具序列"，挖反复出现
    （≥2 个任务）的有序工具 n-gram，剔掉已被现有工作流覆盖的，给出候选 + 现成的
    define_workflow 骨架。只**建议**不自动建 —— 你看完用上下文判断哪个值得固化、
    补好 description / triggers 再存。

    Returns:
        候选套路 + define_workflow 骨架；没有可固化的就说明原因。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "default")

    from paths import META_DIR
    from ai_agent.persist import JSONCheckpoint
    from tools.workflow_store import (
        list_all,
        extract_task_tool_sequences,
        find_crystallization_candidates,
        format_crystallization_suggestion,
    )

    conv = JSONCheckpoint(META_DIR).load(thread_id)
    seqs = extract_task_tool_sequences(conv)
    if not seqs:
        return "暂无可分析的工具调用历史（这个对话还没跑过多工具任务）。"

    covered = [set(w.get("tools", [])) for w in list_all()]
    cands = find_crystallization_candidates(seqs, covered_tool_sets=covered)
    if not cands:
        return (
            "暂无值得固化的重复套路：要么没出现 ≥2 次的多工具序列，"
            "要么重复的套路都已被现有工作流覆盖。"
        )
    return format_crystallization_suggestion(cands)
