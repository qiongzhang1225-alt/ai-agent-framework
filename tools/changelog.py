"""regenerate_changelog 工具：从 git log 重建 CHANGELOG.md。

设计:
不在 self_edit 的 6 个 commit 调用点散加 hook（太散 + 跨平台麻烦），
改成 yuki 自己主动调一次，从 git log 拉所有 ``[ai-edit]`` commit + 用户
手写的 ``feat:`` / ``fix:`` / ``refactor:`` 等 conventional commit 拼成
按日期分组的 CHANGELOG.md。

何时调:
- 主人说"看看最近改了啥" / "整理 changelog"
- 你完成一个里程碑（如发版前）
- 每隔 1-2 周自我维护一次
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ai_agent import tool
from paths import PROJECT_ROOT


# 我们只关心这些 commit 类型（其他 [server-startup] auto-commit 等噪音过滤）
_INTERESTING_PREFIXES = (
    "feat", "fix", "refactor", "docs", "chore", "revert",
    "[ai-edit]", "[ai-edit-pre]",
)


def _classify(subject: str) -> tuple[str, str]:
    """从 commit subject 分类 + 提取标题。

    返回 (category, title)。
    category 用于按主题分组 (feat/fix/refactor/...)。
    """
    s = subject.strip()
    # [ai-edit] path: reason → category=ai-edit
    if s.startswith("[ai-edit]") or s.startswith("[ai-edit-pre]"):
        return ("ai-edit", s.split("]", 1)[1].strip() if "]" in s else s)
    # conventional: feat(scope): title / fix: title
    for prefix in ("feat", "fix", "refactor", "docs", "chore", "revert", "test", "perf"):
        if s.startswith(prefix + "(") or s.startswith(prefix + ":"):
            colon = s.index(":")
            return (prefix, s[colon + 1:].strip())
    return ("other", s)


@tool
def regenerate_changelog(
    last_n_days: int = 30,
    output_path: str = "CHANGELOG.md",
    config: dict = None,
) -> str:
    """从 git log 重新生成 CHANGELOG.md，按日期 + 类别分组。

    Args:
        last_n_days: 回看多少天的 commit（默认 30 天）
        output_path: 输出文件路径（项目相对路径，默认 CHANGELOG.md）

    返回: 生成统计（commit 数 / 写入大小）+ 文件路径。
    """
    if not shutil.which("git"):
        return "❌ 找不到 git 可执行文件"

    project_root = PROJECT_ROOT
    out = project_root / output_path
    if not str(out.resolve()).startswith(str(project_root.resolve())):
        return f"❌ output_path 越界: {output_path}"

    # 拿 git log，格式: <hash>|<iso date>|<subject>
    since = f"--since={int(last_n_days)} days ago"
    try:
        r = subprocess.run(
            ["git", "log", since, "--no-merges", "--pretty=format:%h|%aI|%s"],
            cwd=str(project_root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except Exception as e:
        return f"❌ git log 失败: {e}"

    if r.returncode != 0:
        return f"❌ git log returncode={r.returncode}: {r.stderr[:200]}"

    # 解析 + 过滤
    by_date: dict[str, dict[str, list[tuple[str, str]]]] = {}
    skipped = 0
    total = 0
    for line in r.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        h, iso_date, subject = parts
        total += 1

        # 过滤噪音
        if subject.startswith("[server-startup]"):
            skipped += 1
            continue
        if not any(subject.startswith(p) for p in _INTERESTING_PREFIXES):
            skipped += 1
            continue

        date = iso_date[:10]  # YYYY-MM-DD
        category, title = _classify(subject)
        by_date.setdefault(date, {}).setdefault(category, []).append((h, title))

    if not by_date:
        return f"ℹ️ 最近 {last_n_days} 天无值得记录的 commit（共扫 {total}，跳过 {skipped} 噪音）"

    # 生成 markdown
    lines = [
        "# CHANGELOG",
        "",
        f"> 自动从 git log 重建（最近 {last_n_days} 天）。",
        f"> 由 `regenerate_changelog` 工具生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}。",
        "",
    ]

    # 类别中文显示 + 排序权重
    cat_order = ["feat", "fix", "refactor", "ai-edit", "docs", "chore", "revert", "test", "perf", "other"]
    cat_labels = {
        "feat": "✨ 新功能",
        "fix": "🐛 修复",
        "refactor": "♻️ 重构",
        "ai-edit": "🤖 自我修改",
        "docs": "📝 文档",
        "chore": "🔧 杂项",
        "revert": "⏮️ 回滚",
        "test": "🧪 测试",
        "perf": "⚡ 性能",
        "other": "其他",
    }

    for date in sorted(by_date.keys(), reverse=True):
        lines.append(f"## {date}")
        lines.append("")
        cats = by_date[date]
        for cat in cat_order:
            if cat not in cats:
                continue
            entries = cats[cat]
            lines.append(f"### {cat_labels.get(cat, cat)}")
            for h, title in entries:
                # title 太长截断
                t = title if len(title) <= 100 else title[:97] + "..."
                lines.append(f"- `{h}` {t}")
            lines.append("")

    content = "\n".join(lines)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 原子写
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(out)

    interesting = total - skipped
    return (
        f"✅ CHANGELOG.md 已生成 → {out.relative_to(project_root) if out.is_relative_to(project_root) else out}\n"
        f"共 {interesting} 条值得记录的 commit（跳过 {skipped} 条 server-startup 噪音 / 其他无前缀）\n"
        f"日期范围: 最近 {last_n_days} 天 / 横跨 {len(by_date)} 天\n"
        f"文件大小: {out.stat().st_size} 字节"
    )
