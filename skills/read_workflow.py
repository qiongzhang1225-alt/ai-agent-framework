def read_workflow(name: str) -> str:
    """取回一个**工作流的完整步骤 + 历史踩坑**，照着做。

    什么时候用：要动手做某类任务前，先想想自己有没有存过对应工作流
    （不确定就先 list_workflows 看名字），有就用本工具把步骤和教训取回来，
    按它执行，避免重复踩以前记下的坑。

    Args:
        name: 工作流名称（支持精确文件名，或按 name 字段模糊匹配）。

    Returns:
        渲染好的步骤清单 + 历史踩坑；找不到时返回提示并列出现有工作流名。
    """
    from tools.workflow_store import render, bump_usage
    bump_usage(name)  # 记一次"真正取用"（优化 C 的使用统计信号）；找不到时是 no-op
    return render(name)
