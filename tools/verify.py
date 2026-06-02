"""verify_change 工具 —— 改完代码后的"自动断言"层。

设计动机：
assistant 写 CSS 选择器 `\\/` 错了 30 处，浏览器静默忽略，DOM 不报错。
她没有"打开 DevTools 看哪些规则被划掉"的检查习惯。这个工具把
"写完检查关键 pattern" 变成一行调用。

约束（主人定的）：
- 检测失败**不强制 revert**，只返回警告
- 让 assistant 自己看完决定是修还是接受
- audit.jsonl 里记录每次 verify 结果（pass / warn）
"""
from __future__ import annotations

import re
from pathlib import Path

from ai_agent import tool
from paths import PROJECT_ROOT


def _resolve_safe(path: str) -> Path | None:
    """项目相对路径解析；越界返回 None。"""
    try:
        p = (PROJECT_ROOT / path).resolve()
        p.relative_to(PROJECT_ROOT.resolve())
        return p if p.is_file() else None
    except (ValueError, OSError):
        return None


@tool
def verify_change(
    files: list,
    must_exist: list = None,
    must_not_exist: list = None,
    config: dict = None,
) -> str:
    """**改完关键文件后调一次**：断言关键 pattern 应该出现 / 不该出现。

    检测失败**不会**回滚你的改动 —— 只返回警告。看完自己决定是修还是接受。

    什么时候必须调（硬约束）：
    - **改完 CSS / HTML 后**：验证选择器没写错、关键 class 都在
    - **改完工具实现后**：验证导出符号都在、没漏 @tool 装饰器
    - **改完 prompts/system.md 后**：验证段落标题没被误删

    什么时候可以不调：
    - 改了个错别字 / 单字符替换
    - self_edit_with_test 已经跑过测试

    Args:
        files: 要检查的文件路径列表（项目相对路径）。
              例：``["static/style.css", "templates/index.html"]``
        must_exist: 必须**存在**的 pattern 列表（普通字符串，逐文件都查一遍）。
              所有 pattern 在**任一**文件出现即算通过。
              例：``["_streamBuf", "body.theme-light"]``
        must_not_exist: 必须**不存在**的 pattern 列表。
              所有 pattern 在**所有**文件都不出现才算通过。
              例：``["\\\\\\\\/", "TODO: 留给后人"]`` （CSS 双反斜杠 / 残留 TODO）
        config: (注入参数，不用填)

    Returns:
        每个 pattern 的检查结果 + 总体 PASS / WARN。
        例：
            ✅ verify_change 全部通过 (5 个 pattern, 2 个文件)
            或
            ⚠️ verify_change 警告（你可以选择修或接受）：
              [must_exist] '_streamBuf' 没在任何文件出现
              [must_not_exist] '\\\\/' 在 static/style.css 第 246 行出现
    """
    must_exist = must_exist or []
    must_not_exist = must_not_exist or []

    if not files:
        return "❌ files 不能为空"
    if not must_exist and not must_not_exist:
        return "❌ must_exist 和 must_not_exist 至少要传一个"

    # 解析所有文件
    resolved: list[tuple[str, Path]] = []
    missing: list[str] = []
    for f in files:
        path = str(f).strip()
        p = _resolve_safe(path)
        if p is None:
            missing.append(path)
        else:
            resolved.append((path, p))

    if missing:
        return f"❌ 这些文件找不到或越界：{missing}"

    # 读所有文件
    contents: list[tuple[str, str]] = []  # (rel_path, text)
    for rel, abs_path in resolved:
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            contents.append((rel, text))
        except Exception as e:
            return f"❌ 读取 {rel} 失败：{e}"

    warnings: list[str] = []
    passes: list[str] = []

    # must_exist：所有 pattern 在任一文件出现即可
    for pat in must_exist:
        pat_str = str(pat)
        found = False
        for rel, text in contents:
            if pat_str in text:
                found = True
                passes.append(f"[must_exist] {pat_str!r} ✓ 在 {rel} 出现")
                break
        if not found:
            warnings.append(f"[must_exist] {pat_str!r} ✗ 没在任何文件出现")

    # must_not_exist：所有 pattern 在所有文件都不能出现
    for pat in must_not_exist:
        pat_str = str(pat)
        violations: list[str] = []
        for rel, text in contents:
            if pat_str in text:
                # 找首次出现的行号
                idx = text.index(pat_str)
                line_no = text[:idx].count("\n") + 1
                count = text.count(pat_str)
                violations.append(f"{rel}:{line_no}（共 {count} 处）")
        if violations:
            warnings.append(f"[must_not_exist] {pat_str!r} ✗ 出现在: " + "; ".join(violations))
        else:
            passes.append(f"[must_not_exist] {pat_str!r} ✓ 所有文件都没出现")

    total = len(must_exist) + len(must_not_exist)

    if not warnings:
        body = [f"✅ verify_change 全部通过 ({total} 个 pattern, {len(resolved)} 个文件)"]
        body.extend("  " + p for p in passes)
        return "\n".join(body)

    body = [
        f"⚠️  verify_change 警告（{len(warnings)}/{total} 失败，未自动回滚 —— 你看完决定修还是接受）",
        "",
        "失败:",
    ]
    body.extend("  - " + w for w in warnings)
    if passes:
        body.append("")
        body.append("通过:")
        body.extend("  - " + p for p in passes)
    body.append("")
    body.append("建议：如果是误报（pattern 写错了），改 verify_change 调用；")
    body.append("如果是真 bug，回去修。")
    return "\n".join(body)
