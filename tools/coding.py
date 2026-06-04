"""编程辅助工具集 (6 个 @tool)。

跟 Claude Code 等成熟 Agent 看齐，给 yuki 加上日常编程必需的高频能力：

- ``lint(paths, fix)``        —— 静态检查（ruff check）
- ``format_code(paths, ...)`` —— 格式化（ruff format）
- ``run_tests(path, ...)``    —— 跑 pytest 测试 + 结构化输出
- ``apply_patch(patch_text)`` —— 应用 unified diff（多文件多 hunk）
- ``find_references(...)``    —— 找符号引用（code_indexer + grep 兜底）
- ``smoke_test(...)``         —— 烟雾测试：能 import + 关键 API 存在

设计要点：
- 全部跑在项目根 / 工作目录，不污染主进程
- 用 find_real_python() 解析 Python 解释器，frozen 模式安全
- 输出做摘要化（top-K 结果 / 截断长输出 + 写日志），LLM context 友好
- ruff 用 JSON 输出格式，便于结构化解析

依赖（不在则报错提示用户装）：
- ruff   →  pip install ruff
- pytest →  pip install pytest
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from ai_agent import tool
from paths import PROJECT_ROOT
from tools._common import find_real_python


# ── 公用 ────────────────────────────────────────────────────────────────

_RUN_TIMEOUT = 120  # 工具默认超时


def _resolve_workdir(config: dict) -> Path:
    """从 config 拿 workdir，没有则用 PROJECT_ROOT。"""
    cfg = (config or {}).get("configurable", {}) if config else {}
    workdir = cfg.get("workdir")
    if workdir:
        try:
            p = Path(workdir).resolve()
            if p.exists():
                return p
        except Exception:
            pass
    return PROJECT_ROOT


def _resolve_paths(paths, base: Path) -> list[str]:
    """规范化路径参数（兼容 str / list / 相对 base）。"""
    if not paths:
        return ["."]
    if isinstance(paths, str):
        paths = [paths]
    out = []
    for p in paths:
        s = str(p).strip()
        if not s:
            continue
        if Path(s).is_absolute():
            out.append(s)
        else:
            out.append(str((base / s).resolve()))
    return out or ["."]


def _truncate(text: str, max_chars: int = 4000) -> str:
    """长输出截断（防止淹没 context）。"""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...(截断，原 {len(text)} 字符)"


def _have_module(module_name: str) -> bool:
    """检测 Python 模块是否可用（通过真 python 跑 -c）。"""
    py = find_real_python()
    if not py:
        return False
    try:
        r = subprocess.run(
            [py, "-c", f"import {module_name}"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


# ── 1. lint ─────────────────────────────────────────────────────────────


def _run_mypy(py: str, target_paths: list[str], workdir: Path) -> str:
    """跑 mypy 类型检查（非 strict，--ignore-missing-imports）。

    设计取舍：
    - 不开 --strict —— 项目里很多函数没全注解，strict 会爆出几百条噪音
    - --ignore-missing-imports —— 第三方库（chromadb / tree_sitter 等）多无 stub
    - 只保留 error 级别（warning / note 在非 strict 下意义不大）
    - 最多展示 30 条
    """
    if not _have_module("mypy"):
        return (
            "ℹ️ mypy 未安装，跳过类型检查\n"
            "  装一下: pip install mypy（或 run_command('pip', ['install', 'mypy'])）"
        )

    cmd = [
        py, "-m", "mypy",
        "--ignore-missing-imports",
        "--no-error-summary",
        "--show-error-codes",
        "--no-color-output",
        *target_paths,
    ]
    try:
        r = subprocess.run(
            cmd, cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_RUN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"❌ mypy 超时 ({_RUN_TIMEOUT}s)"
    except Exception as e:
        return f"❌ mypy 启动失败: {e}"

    out = (r.stdout or "").strip()
    # mypy returncode: 0 = 无错；非 0 = 有错。stderr 偶有"启动信息"，正常忽略。
    if not out and r.returncode == 0:
        return "✓ mypy: 无类型错误"
    if not out:
        # 没输出但 returncode 非 0 —— 比如所有 paths 都被 ignore
        return f"ℹ️ mypy: 无可检查文件（returncode={r.returncode}）"

    # 解析 "file:line: error: msg  [error-code]"
    err_entries: list[str] = []
    for line in out.splitlines():
        if ": error:" not in line:
            continue
        m = re.match(r"^(.+?):(\d+):(?:\d+:)?\s*error:\s*(.+?)(?:\s+\[([\w-]+)\])?$", line)
        if m:
            fname, lno, msg, code = m.group(1), m.group(2), m.group(3), m.group(4) or "?"
            try:
                fname = str(Path(fname).relative_to(workdir))
            except (ValueError, OSError):
                pass
            err_entries.append(f"  {fname}:{lno}  [{code}] {msg}")
        else:
            err_entries.append(f"  {line}")

    if not err_entries:
        return "✓ mypy: 无类型错误"

    head = f"⚠️ mypy 发现 {len(err_entries)} 个类型问题（非 strict 模式）"
    if len(err_entries) > 30:
        return head + ":\n" + "\n".join(err_entries[:30]) + f"\n  ...(还有 {len(err_entries) - 30} 条)"
    return head + ":\n" + "\n".join(err_entries)


@tool
def lint(
    paths: list = None,
    fix: bool = False,
    type_check: bool = False,
    config: dict = None,
) -> str:
    """跑 ruff 静态检查（可选叠加 mypy 类型检查）。

    ``fix=True`` 时自动修可修复的（未用 import、import 排序等）。
    ``type_check=True`` 时**额外**跑一遍 mypy（非 strict，--ignore-missing-imports）。

    什么时候用：
    - 改完 .py 文件后立刻自检
    - 主人说"看看代码有什么问题"
    - 提交前过一遍

    什么时候开 ``type_check=True``：
    - 改了涉及类型的关键代码（函数签名 / Optional / 返回值）
    - 主人说"查下类型有没有错"
    - **不建议每次都开** —— mypy 比 ruff 慢，且非 strict 下漏检也多

    Args:
        paths: 要检查的路径列表（相对工作目录或绝对路径）。
               默认 ``["."]``（整个工作目录递归）。
        fix: True 则自动修可自动修的问题；False 仅报告。
        type_check: True 则在 ruff 之后再跑 mypy（默认 False）。

    返回：ruff 结果（+ 可选 mypy 结果）。
    依赖 ruff：未装时返回提示 + pip install 命令。
    type_check=True 但 mypy 未装时不报错，给安装提示后继续。
    """
    workdir = _resolve_workdir(config)
    target_paths = _resolve_paths(paths, workdir)

    py = find_real_python()
    if not py:
        return "❌ 找不到 Python 解释器（参考 execute_code 的提示）"
    if not _have_module("ruff"):
        return (
            "❌ ruff 未安装。装一下:\n"
            "  request_pip_install('ruff', '', '静态代码检查')\n"
            "或主人手动: pip install ruff"
        )

    cmd = [py, "-m", "ruff", "check", *target_paths, "--output-format=json"]
    if fix:
        cmd.append("--fix")

    try:
        r = subprocess.run(
            cmd, cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_RUN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"❌ ruff 超时 ({_RUN_TIMEOUT}s)"
    except Exception as e:
        return f"❌ ruff 启动失败: {e}"

    # ruff 0 = 无问题；1 = 有问题但跑成功
    try:
        issues = json.loads(r.stdout) if r.stdout.strip() else []
    except json.JSONDecodeError:
        return f"❌ ruff 输出解析失败:\n{_truncate(r.stdout, 1000)}\nstderr:\n{_truncate(r.stderr, 500)}"

    # 构造 ruff 结果字符串
    if not issues:
        ruff_result = "✓ ruff: 无问题"
    else:
        by_code: dict[str, int] = {}
        for it in issues:
            code = it.get("code", "?")
            by_code[code] = by_code.get(code, 0) + 1
        summary = ", ".join(f"{c}: {n}" for c, n in sorted(by_code.items(), key=lambda x: -x[1]))

        lines = [
            f"⚠️ ruff 发现 {len(issues)} 个问题（按错误码: {summary}）"
            + ("  [已自动修复部分]" if fix else ""),
            "",
        ]
        for it in issues[:30]:
            fname = it.get("filename", "?")
            try:
                fname = str(Path(fname).relative_to(workdir))
            except (ValueError, OSError):
                pass
            loc = it.get("location") or {}
            row = loc.get("row", "?")
            col = loc.get("column", "?")
            code = it.get("code", "?")
            msg = it.get("message", "")
            lines.append(f"  {fname}:{row}:{col}  [{code}] {msg}")
        if len(issues) > 30:
            lines.append(f"  ...(还有 {len(issues) - 30} 条)")
        ruff_result = "\n".join(lines)

    # 不开 type_check → 保持原行为
    if not type_check:
        # 完全没问题时仍返回"✓ 无问题"（向后兼容）
        return "✓ 无问题" if not issues else ruff_result

    # 开 type_check → 追加 mypy 段
    mypy_result = _run_mypy(py, target_paths, workdir)
    return ruff_result + "\n\n" + mypy_result


# ── 2. format_code ──────────────────────────────────────────────────────


@tool
def format_code(
    paths: list = None,
    check_only: bool = False,
    config: dict = None,
) -> str:
    """跑 ruff format 格式化 Python 代码（也支持 import 排序）。

    ``check_only=True`` 仅检查不改文件（适合"看哪些需要格式化"）。

    什么时候用：
    - 加完新代码后统一风格
    - 主人说"格式化这个文件"
    - lint 报了 "format" 类问题

    Args:
        paths: 要格式化的路径列表。默认 ``["."]``。
        check_only: True 仅报告"哪些需要格式化"，不改文件。
                    False 直接格式化（建议改前 git commit）。

    返回：摘要（X files reformatted / X files already formatted）+ 列表。
    """
    workdir = _resolve_workdir(config)
    target_paths = _resolve_paths(paths, workdir)

    py = find_real_python()
    if not py:
        return "❌ 找不到 Python 解释器"
    if not _have_module("ruff"):
        return "❌ ruff 未安装（pip install ruff）"

    cmd = [py, "-m", "ruff", "format", *target_paths]
    if check_only:
        cmd.append("--check")

    try:
        r = subprocess.run(
            cmd, cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_RUN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"❌ ruff format 超时 ({_RUN_TIMEOUT}s)"
    except Exception as e:
        return f"❌ ruff format 启动失败: {e}"

    out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    out = out.strip()
    if not out:
        return "✓ 无变化"

    prefix = "ℹ️ check 模式（未实际改文件）\n" if check_only else "✓ 格式化完成\n"
    return prefix + _truncate(out, 3000)


# ── 3. run_tests ────────────────────────────────────────────────────────


@tool
def run_tests(
    path: str = ".",
    pattern: str = "",
    verbose: bool = False,
    config: dict = None,
) -> str:
    """跑 pytest 测试，给结构化摘要（通过/失败/错误 + 失败详情）。

    什么时候用：
    - 改完代码（特别是修 bug）后立刻验证
    - 主人说"跑下测试"
    - self_edit_with_test 之外的常规测试

    Args:
        path: 测试文件或目录（相对工作目录）。默认 ``"."`` 整工作目录。
        pattern: pytest ``-k`` 参数，只跑名字匹配的 test 函数。可空。
                 例: ``"test_login"`` 或 ``"login or signup"``。
        verbose: True 显示完整测试名 + 每个测试结果；False 仅摘要 + 失败详情。

    返回：通过 N / 失败 N / 错误 N + 失败的 traceback 摘要。
    """
    workdir = _resolve_workdir(config)
    target = _resolve_paths([path], workdir)[0]

    py = find_real_python()
    if not py:
        return "❌ 找不到 Python 解释器"
    if not _have_module("pytest"):
        return (
            "❌ pytest 未安装。装一下:\n"
            "  request_pip_install('pytest', '', '跑测试')"
        )

    cmd = [py, "-m", "pytest", target, "--tb=short", "--no-header", "-q"]
    if verbose:
        cmd.extend(["-v"])
    if pattern.strip():
        cmd.extend(["-k", pattern.strip()])

    try:
        r = subprocess.run(
            cmd, cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,  # 测试可能跑久点
        )
    except subprocess.TimeoutExpired:
        return "❌ pytest 超时 (300s) — 测试卡死或太慢"
    except Exception as e:
        return f"❌ pytest 启动失败: {e}"

    out = r.stdout or ""

    # 解析 pytest 末尾的 summary line（"5 passed, 2 failed in 0.3s"）
    m = re.search(
        r"=+\s*((?:\d+\s+(?:passed|failed|error[s]?|skipped|xfailed|xpassed|warning[s]?)(?:,\s*)?)+)"
        r".*?in\s+[\d.]+s",
        out,
    )
    summary = m.group(1) if m else "(无法解析摘要)"

    # pytest returncode: 0=全过、1=有失败、2=中断、5=无测试可跑
    status_emoji = {0: "✓", 1: "❌", 2: "⚠️", 5: "ℹ️"}.get(r.returncode, "❌")
    if r.returncode == 5:
        return f"ℹ️ 没找到测试: {target}"

    lines = [f"{status_emoji} pytest: {summary}", ""]

    # 仅在失败时附详情
    if r.returncode != 0 and out:
        # 取 "FAILED ..." 行 + 紧跟 traceback 段
        # 简化版：直接附 stdout 后 3000 字符
        lines.append("详情:")
        lines.append(_truncate(out[-4000:], 4000))

    if r.stderr.strip() and r.returncode != 0:
        lines.append("\nstderr:")
        lines.append(_truncate(r.stderr, 1000))

    return "\n".join(lines)


# ── 4. apply_patch ──────────────────────────────────────────────────────


@tool
def apply_patch(
    patch_text: str,
    config: dict = None,
) -> str:
    """应用 unified diff（多文件多 hunk）—— 比 edit_file 适合大段重写。

    底层用 git apply 处理，路径基于工作目录解析。

    什么时候用：
    - 你已经把改动想清楚成 diff 形式
    - 同一改动跨多文件 / 多处
    - edit_file 太碎（要 5+ 次调用）

    什么时候不用：
    - 单个小修改（用 edit_file）
    - 不确定改对不对（先 self_edit_with_test）

    Args:
        patch_text: 标准 unified diff 文本。例：

            --- a/foo.py
            +++ b/foo.py
            @@ -10,3 +10,4 @@
             def hello():
            -    return "old"
            +    return "new"
            +    # added comment

            支持多文件：每段以 ``--- a/<file>`` 开头。

    返回：✓ 成功 + 改动的文件列表，或 ❌ 失败原因 + 拒绝的 hunk。
    """
    workdir = _resolve_workdir(config)

    if not patch_text or not patch_text.strip():
        return "❌ patch_text 不能为空"

    # 写到临时文件再 git apply（比 stdin 更稳）
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", encoding="utf-8",
        dir=str(workdir), delete=False,
    ) as f:
        f.write(patch_text)
        patch_path = f.name

    def _cleanup():
        try:
            os.unlink(patch_path)
        except OSError:
            pass

    git = shutil.which("git")
    if not git:
        _cleanup()
        return "❌ 找不到 git 可执行文件"

    # 先 --check 看能否干净应用
    try:
        check = subprocess.run(
            [git, "apply", "--check", "--unsafe-paths", patch_path],
            cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except Exception as e:
        _cleanup()
        return f"❌ git apply --check 失败: {e}"

    if check.returncode != 0:
        _cleanup()
        return (
            "❌ patch 不能干净应用（git apply --check 失败）:\n"
            f"{_truncate(check.stderr or check.stdout, 2000)}\n\n"
            "可能原因：基础内容不匹配（行号偏移 / 上下文变化），"
            "考虑用 edit_file 改一小块再来 patch。"
        )

    # 实际应用
    try:
        r = subprocess.run(
            [git, "apply", "--unsafe-paths", patch_path],
            cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    finally:
        _cleanup()

    if r.returncode != 0:
        return f"❌ git apply 失败:\n{_truncate(r.stderr or r.stdout, 2000)}"

    # 解析改动的文件列表（git apply 不直接给，自己 parse patch_text）
    files = sorted(set(re.findall(r"^\+\+\+ b/(.+)$", patch_text, re.MULTILINE)))
    if not files:
        files = sorted(set(re.findall(r"^--- a/(.+)$", patch_text, re.MULTILINE)))

    if files:
        return "✓ patch 应用成功，改动文件:\n  " + "\n  ".join(files)
    return "✓ patch 应用成功"


# ── 5. find_references ──────────────────────────────────────────────────


@tool
def find_references(
    symbol: str,
    kind: str = "any",
    config: dict = None,
) -> str:
    """找符号被哪里引用 —— 重构前看影响面。

    底层先用 code_indexer（如果已索引），fallback ripgrep 全文搜。

    什么时候用：
    - 改函数签名前，看谁调用过
    - 删某个 class 前，看哪里 import
    - 主人问"这个 XX 是哪里用的"

    Args:
        symbol: 符号名（函数 / 类 / 变量 / import 名）。
                例: ``"start_server"`` / ``"DEFAULT_WORKDIR"``。
        kind: 限定符号类型，可选 ``"function"`` / ``"class"`` /
              ``"any"`` (默认任意)。

    返回：引用列表（文件:行 + 该行内容片段），按出现频次排序。
    """
    workdir = _resolve_workdir(config)
    symbol = symbol.strip()
    if not symbol:
        return "❌ symbol 不能为空"

    rg = shutil.which("rg")
    git_grep_ok = shutil.which("git") is not None

    # 用 word boundary 正则匹配，避免子串误中（XXX 不匹配 XXXY）
    pattern = rf"\b{re.escape(symbol)}\b"

    if rg:
        cmd = [
            rg, "--type", "py", "--type", "js", "--type", "html",
            "-n", "--no-heading", "--color=never", "-S",
            pattern, str(workdir),
        ]
    elif git_grep_ok and (workdir / ".git").exists():
        cmd = ["git", "grep", "-n", "-E", pattern]
    else:
        return "❌ 找不到 rg (ripgrep) 也没 git，无法搜索"

    try:
        r = subprocess.run(
            cmd, cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except Exception as e:
        return f"❌ 搜索失败: {e}"

    if r.returncode > 1:  # rg/grep: 0=有结果 1=无结果 >1=错误
        return f"❌ 搜索异常: {_truncate(r.stderr, 500)}"

    lines = (r.stdout or "").strip().splitlines()
    if not lines:
        return f"ℹ️ 没找到 `{symbol}` 的引用"

    # 按文件聚合 + 摘要
    by_file: dict[str, list[tuple[str, str]]] = {}
    for line in lines:
        # 格式: <path>:<lineno>:<content>  (rg) 或 <path>:<lineno>:<content> (git grep)
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path, lineno, content = parts
        try:
            rel = str(Path(path).relative_to(workdir))
        except (ValueError, OSError):
            rel = path
        by_file.setdefault(rel, []).append((lineno, content.strip()))

    # 按引用次数倒排
    sorted_files = sorted(by_file.items(), key=lambda kv: -len(kv[1]))

    out = [f"找到 `{symbol}` 的 {len(lines)} 处引用（{len(by_file)} 个文件）:"]
    out.append("")
    for rel, hits in sorted_files[:20]:  # 最多 20 个文件
        out.append(f"📄 {rel} ({len(hits)} 处):")
        for ln, content in hits[:5]:  # 每文件最多 5 行
            preview = content[:100] + ("..." if len(content) > 100 else "")
            out.append(f"  L{ln}: {preview}")
        if len(hits) > 5:
            out.append(f"  ...(还有 {len(hits) - 5} 处)")
    if len(sorted_files) > 20:
        out.append(f"\n...(还有 {len(sorted_files) - 20} 个文件)")

    return "\n".join(out)


# ── 6. smoke_test ───────────────────────────────────────────────────────


# 跑在子进程里的检查脚本：从 stdin 读 JSON 配置，结果 JSON 写 stdout
_SMOKE_RUNNER = r'''
import sys, json, importlib

# argv[1] 是要加到 sys.path[0] 的根目录
if len(sys.argv) > 1:
    sys.path.insert(0, sys.argv[1])

data = json.loads(sys.stdin.read())
modules_to_check = data.get("modules") or []
asserts_to_check = data.get("asserts") or []

results = {"imports": [], "asserts": []}

for mod_name in modules_to_check:
    try:
        importlib.import_module(mod_name)
        results["imports"].append({"name": mod_name, "ok": True})
    except BaseException as e:
        results["imports"].append({
            "name": mod_name,
            "ok": False,
            "err": f"{type(e).__name__}: {e}",
        })

for spec in asserts_to_check:
    try:
        if ":" not in spec:
            raise ValueError("spec 必须是 'module:attr.path' 格式")
        mod_name, attr_path = spec.split(":", 1)
        obj = importlib.import_module(mod_name)
        for part in attr_path.split("."):
            obj = getattr(obj, part)
        results["asserts"].append({"spec": spec, "ok": True})
    except BaseException as e:
        results["asserts"].append({
            "spec": spec,
            "ok": False,
            "err": f"{type(e).__name__}: {e}",
        })

sys.stdout.write("\n__SMOKE_RESULT__" + json.dumps(results) + "\n")
'''


@tool
def smoke_test(
    modules: list = None,
    asserts: list = None,
    cwd: str = "",
    config: dict = None,
) -> str:
    """烟雾测试：验证模块能 import + 关键 API 存在 —— 最低门槛"能跑吗"检查。

    跟其他工具的关系（从浅到深）：
    - ``lint``        语法 / 风格（ruff，秒级）
    - ``smoke_test``  能 import + 关键 API 存在（本工具，秒级）
    - ``run_tests``   功能正确性（pytest，分钟级）

    什么时候用：
    - 改完**多个文件**后（特别是动了 import / 重构 / 文件复制）
    - **同步 public/ 后**验证另一边还能 boot（cwd="public"）
    - 跑完整 pytest 之前先过一道关卡
    - 主人说"能跑吗 / 别炸了"
    - 新 ``define_skill`` 后验证持久化文件能加载

    什么时候**不要**用：
    - 改单个文件且没动 import → ``lint`` 就够
    - 想验证业务逻辑对不对 → 用 ``run_tests``

    工作机制：
    - 在**子进程**里 import（不影响你当前进程的 module cache）
    - 这意味着 ``self_edit_file`` 改完**立刻**就能用 smoke_test 验证 —— 子
      进程从磁盘读最新文件，不受你内存里旧版本影响
    - 子进程结束后退出，不污染状态

    Args:
        modules: 要 import 的模块名列表（点分形式）。
                 默认 ``["agent", "server", "tools"]`` —— 项目三个核心入口。
                 例: ``["tools.coding", "tools.code_indexer"]``。
                 传 ``[]`` 跳过 import 检查（只验 asserts）。
        asserts: 要验证存在的 API 列表，每项格式 ``"module:attr.path"``：
                 - ``"tools.coding:lint"`` → ``hasattr(tools.coding, "lint")``
                 - ``"tools.code_indexer:CodeIndexer.update_file"`` →
                   验证类方法存在
                 默认 ``[]``。
        cwd: 在哪个目录跑（决定 sys.path[0]）。
             默认 ``""`` → PROJECT_ROOT；传相对路径 → workdir/cwd；
             传绝对路径 → 直接用。例: ``"public"`` 验证 public 仓库。

    Returns:
        ``✓ smoke test 通过 (N import + M asserts)`` 或
        ``❌ smoke test 失败`` + 每条挂的 spec + 错误类型 + 详情。
    """
    workdir = _resolve_workdir(config)

    if modules is None:
        modules = ["agent", "server", "tools"]
    if asserts is None:
        asserts = []

    if not modules and not asserts:
        return "❌ modules 和 asserts 不能都为空"

    # 决定运行目录
    if cwd:
        cwd_p = Path(cwd)
        if not cwd_p.is_absolute():
            cwd_p = workdir / cwd
        try:
            cwd_p = cwd_p.resolve()
        except Exception as e:
            return f"❌ cwd 解析失败: {e}"
        if not cwd_p.is_dir():
            return f"❌ cwd 不是目录: {cwd}"
        run_dir = cwd_p
    else:
        run_dir = PROJECT_ROOT

    py = find_real_python()
    if not py:
        return "❌ 找不到 Python 解释器"

    try:
        r = subprocess.run(
            [py, "-c", _SMOKE_RUNNER, str(run_dir)],
            cwd=str(run_dir),
            input=json.dumps({"modules": modules, "asserts": asserts}),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "❌ smoke_test 超时 (60s)"
    except Exception as e:
        return f"❌ smoke_test 启动失败: {e}"

    # 用 marker 抓结果（避免 import 时模块的 print/log 干扰 JSON 解析）
    out = r.stdout or ""
    marker = "__SMOKE_RESULT__"
    idx = out.rfind(marker)
    if idx < 0:
        return (
            f"❌ smoke_test 无结果（找不到 marker）:\n"
            f"stdout: {_truncate(out, 800)}\n"
            f"stderr: {_truncate(r.stderr, 400)}"
        )
    json_part = out[idx + len(marker):].strip().split("\n")[0]
    try:
        result = json.loads(json_part)
    except Exception as e:
        return f"❌ smoke_test 解析失败: {e}\n{_truncate(json_part, 500)}"

    import_fails = [x for x in result.get("imports", []) if not x.get("ok")]
    assert_fails = [x for x in result.get("asserts", []) if not x.get("ok")]

    if not import_fails and not assert_fails:
        return (
            f"✓ smoke test 通过 ({len(modules)} import + {len(asserts)} asserts)"
            f" @ {run_dir.name}"
        )

    lines = [
        f"❌ smoke test 失败 @ {run_dir.name}",
        f"  ({len(import_fails)}/{len(modules)} import 错，"
        f"{len(assert_fails)}/{len(asserts)} assert 错)",
    ]
    if import_fails:
        lines.append("")
        lines.append("import 失败:")
        for f in import_fails[:10]:
            lines.append(f"  - {f['name']}")
            lines.append(f"      {f['err']}")
        if len(import_fails) > 10:
            lines.append(f"  ...(还有 {len(import_fails) - 10} 条)")
    if assert_fails:
        lines.append("")
        lines.append("assert 失败:")
        for f in assert_fails[:10]:
            lines.append(f"  - {f['spec']}")
            lines.append(f"      {f['err']}")
        if len(assert_fails) > 10:
            lines.append(f"  ...(还有 {len(assert_fails) - 10} 条)")
    return "\n".join(lines)
