def list_workflows() -> str:
    """列出**所有已保存的工作流**（名字 + 描述 + 步骤数 + 历史教训数 + 取用次数）。

    什么时候用：想知道自己攒了哪些多工具组合配方时。看到合适的，再用
    read_workflow(名称) 取回完整步骤照着做。

    Returns:
        工作流索引文本；一个都没有时返回 "(none)"。
    """
    from tools.workflow_store import list_all
    data = list_all()
    if not data:
        return "(none)"
    lines = []
    for wf in data:
        lines.append(f"- {wf['name']}  —  {wf['description']}")
        meta = f"    {wf['steps']} 步, {wf['issues']} 条教训"
        if wf.get("used"):
            meta += f", 取用 {wf['used']} 次"
            if wf.get("last_used"):
                meta += f"（最近 {wf['last_used']}）"
        lines.append(meta)
    return "\n".join(lines)
