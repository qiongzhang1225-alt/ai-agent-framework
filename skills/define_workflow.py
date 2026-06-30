def define_workflow(name: str, description: str, steps: list, triggers: list = None, last_issues: list = None) -> str:
    """保存一个**工作流**（多工具组合配方）到 workflows/，下次做同类任务用 read_workflow 取回照做。

    什么时候用：
    - 你刚摸索出一套"多步骤、跨工具"的稳定打法（如"填实验报告模板" =
      读模板 → execute_code 填 → 截图核对），值得固化下来反复复用时。
    - 区分：单个可复用**函数**用 define_skill；这里存的是"步骤清单/编排指导"，不是代码。

    Args:
        name: 工作流名称（唯一，做文件名）。重名会覆盖，覆盖时**保留原创建日期与已有 triggers**。
        description: 一句话说明这个工作流**什么场景触发 + 产出什么**。
                     它是你以后发现/选中这个工作流的唯一依据，别写太短太泛。
        steps: 步骤列表，每步是 dict，含 tool（用哪个工具）/ description（做什么）/ notes（坑/提醒，可选）。
               例：[{"tool": "read_file", "description": "读模板分析结构", "notes": "关注下划线 run"}]
        triggers: 可选，触发关键词列表（路由用）。做同类任务时这些词出现在对话里，
                  这个工作流就会自动浮到你眼前。建议填 2-5 个高频词，如 ["实验报告", "模板填充"]。
                  留空也能存，但只能靠工作流名 + 引用的工具来命中，会偏弱。
        last_issues: 可选，历史踩坑列表；一般留空，之后用 append_workflow_note 增量追加。

    Returns:
        保存成功的提示 + 文件路径；若有规范问题会附**非阻断**警告（已保存，你决定要不要补）。
    """
    from tools.workflow_store import write, lint_workflow
    try:
        warns = lint_workflow(name, description, steps, triggers)
        path = write(name, description, steps, last_issues or [], triggers)
        msg = f"已保存工作流「{name}」→ {path}"
        if warns:
            msg += "\n⚠️ 规范提醒（已保存，未阻断 —— 你看完决定要不要补）：\n" + "\n".join("  - " + w for w in warns)
        return msg
    except Exception as e:
        return f"fail: {type(e).__name__}: {e}"
