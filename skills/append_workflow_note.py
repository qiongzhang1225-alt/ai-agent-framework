def append_workflow_note(workflow_name: str, issue: str, fix: str) -> str:
    """给某个工作流**追加一条踩坑记录**（issue + 怎么修），让它越用越准。

    什么时候用：照着某个工作流做时踩了新坑、或发现了更好的做法，记下来 ——
    下次 read_workflow 就能看到，避免重复犯同样的错。

    Args:
        workflow_name: 目标工作流名称。
        issue: 踩了什么坑 / 遇到什么问题。
        fix: 怎么解决的 / 以后该怎么做。

    Returns:
        追加结果（含当前教训总数），或 "未找到工作流: ..."。
    """
    from tools.workflow_store import append_note
    return append_note(workflow_name, issue, fix)
