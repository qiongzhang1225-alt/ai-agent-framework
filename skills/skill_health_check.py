import datetime

def skill_health_check(skills: list = None) -> str:
    """
    生成技能健康检查报告。
    
    Args:
        skills: 要检查的技能名称列表。为 None 时由 LLM 配合 list_skills 获取全量列表。
    
    Returns:
        结构化报告，包含每个技能的检查状态和测试建议。
    """
    if not skills:
        return "未指定技能列表。请先用 list_skills 获取所有技能，再传入 skill_health_check(skills=[...]) 进行检测。"
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"=== 技能健康检查报告 ({now}) ===", ""]
    
    for name in skills:
        lines.append(f"【{name}】")
        lines.append(f"  状态：待测试")
        lines.append(f"  测试方式：由 LLM 调用该技能并验证返回结果")
        lines.append("")
    
    lines.append("=== 说明 ===")
    lines.append("本报告为测试计划框架。实际调用由 LLM 逐项执行并填入结果。")
    
    return "\n".join(lines)
