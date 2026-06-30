"""Unity 项目自动接入 MCPForUnity Bridge 包。

何时用
======
主人开了一个新 Unity 项目，想让有希能操控它（建 GameObject、改组件、跑测试）。
有希直接调本工具一行，往项目的 ``Packages/manifest.json`` 里加一行 file:
引用指向本机的 MCPForUnity 源码，然后告诉主人去 Unity 里点 Start Session。

为什么不复制源码进项目
====================
file: 引用让所有 Unity 项目共享同一份 MCPForUnity 源码（在
``F:\\unity_program\\MCP\\unity-mcp-main\\MCPForUnity\\`` 之类）。
- 升级 Bridge：改源码一处，所有项目自动跟上
- 项目体积不增（不复制源码，不入 git）
- 卸载也容易（删 manifest.json 那一行就行）

源码路径来源（优先级从高到低）
==============================
1. ``mcp_source`` 参数显式传
2. ``mcp.json`` 里 ``mcpServers.unity._unity_mcp_source`` 字段
3. 常见候选路径（F:/D:/E:/C: 各盘 unity_program/MCP/unity-mcp-main/MCPForUnity）
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ai_agent import tool


# Unity 进程通过命令行参数 -projectPath <path> 传项目路径；
# 我们查 wmic / powershell 获取所有 Unity.exe 进程的 cmdline 并提取该参数。
_UNITY_PROJECT_FLAG = "-projectPath"


# Unity MCP Bridge 包名（CoplayDev 官方）。Unity 端 + Python server 用同一个 name。
_PKG_NAME = "com.coplaydev.unity-mcp"

# 候选源码路径：用户在哪个盘 / 哪个目录下载了 unity-mcp-main 都覆盖到。
_CANDIDATE_SOURCES = [
    "F:/unity_program/MCP/unity-mcp-main/MCPForUnity",
    "D:/unity_program/MCP/unity-mcp-main/MCPForUnity",
    "E:/unity_program/MCP/unity-mcp-main/MCPForUnity",
    "C:/unity_program/MCP/unity-mcp-main/MCPForUnity",
]


def _read_yuki_mcp_source() -> str | None:
    """从 yuki 的 mcp.json 读 ``mcpServers.unity._unity_mcp_source`` 字段。"""
    try:
        from paths import PROJECT_ROOT
        cfg_path = Path(PROJECT_ROOT) / "mcp.json"
        if not cfg_path.is_file():
            return None
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        unity = (cfg.get("mcpServers") or {}).get("unity") or {}
        src = unity.get("_unity_mcp_source")
        if src and isinstance(src, str):
            return src.strip() or None
    except Exception:
        return None
    return None


def _resolve_mcp_source(explicit: str | None) -> tuple[Path | None, list[str]]:
    """按优先级解析源码路径。返回 (Path 或 None, 失败时尝试过的所有路径)。"""
    tried: list[str] = []

    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    from_cfg = _read_yuki_mcp_source()
    if from_cfg:
        candidates.append(from_cfg)
    candidates += _CANDIDATE_SOURCES

    for raw in candidates:
        p = Path(raw)
        tried.append(str(p))
        # MCPForUnity 包必须有 package.json 才是有效 Unity package
        if (p / "package.json").is_file():
            return p, tried
    return None, tried


@tool
def setup_unity_mcp_bridge(unity_project_path: str, mcp_source: str = "") -> str:
    """给 Unity 项目接入 MCPForUnity Bridge 包（写 Packages/manifest.json）。

    主人开新 Unity 项目想用有希操控时调本工具。会：

    1. 校验 ``unity_project_path`` 是 Unity 项目（含 ``Packages/manifest.json``）
    2. 找本机 MCPForUnity 源码（优先用参数 ``mcp_source``，否则查 ``mcp.json``
       的 ``mcpServers.unity._unity_mcp_source``，最后扫常见候选）
    3. 往 manifest.json ``dependencies`` 加 ``"com.coplaydev.unity-mcp": "file:<源码>"``
    4. 已接入指向同一源码：幂等返回；指向不同源码：自动更新并提示
    5. 返回下一步给主人（在 Unity 里打开项目 → Window → MCP For Unity → Start Session）

    Args:
        unity_project_path: Unity 项目根目录（绝对路径，含 Assets/Packages 子目录）
        mcp_source: 可选，MCPForUnity 源码绝对路径；不传则按 mcp.json / 候选自动找
    """
    # 1. 校验项目目录
    proj = Path(unity_project_path).expanduser().resolve()
    if not proj.is_dir():
        return f"❌ 路径不存在或不是目录：{proj}"
    manifest = proj / "Packages" / "manifest.json"
    if not manifest.is_file():
        return (
            f"❌ 不像 Unity 项目：{proj}\n"
            f"  缺少 {manifest}。检查路径是否指向 Unity 项目根目录"
            f"（含 Assets/Packages/ProjectSettings 子目录）。"
        )

    # 2. 找 MCPForUnity 源码
    src, tried = _resolve_mcp_source(mcp_source.strip() or None)
    if src is None:
        tried_str = "\n  - ".join(tried)
        return (
            "❌ 找不到 MCPForUnity 源码。已尝试以下路径都无 package.json：\n"
            f"  - {tried_str}\n\n"
            "下一步：\n"
            "  - 确认源码下载位置，把 MCPForUnity 文件夹路径传给 mcp_source 参数；\n"
            "  - 或者在 yuki 的 mcp.json 加 ``mcpServers.unity._unity_mcp_source`` 字段记住该路径。"
        )

    # 3. 读 manifest.json
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as e:
        return f"❌ manifest.json 解析失败：{type(e).__name__}: {e}"

    deps = data.setdefault("dependencies", {})
    file_ref = f"file:{src.as_posix()}"

    # 4. 幂等 / 更新
    if _PKG_NAME in deps:
        existing = deps[_PKG_NAME]
        if existing == file_ref:
            return (
                f"✓ 已接入（指向同一源码，无需改动）：\n"
                f"  项目：{proj.name}\n"
                f"  manifest 已有 `{_PKG_NAME}: {existing}`\n\n"
                f"如 Unity 没看到 MCP For Unity 菜单：\n"
                f"  - 重启 Unity Editor 让 Package Manager 重新解析\n"
                f"  - 顶部 Window → MCP For Unity → Start Session"
            )
        old = existing
        deps[_PKG_NAME] = file_ref
        manifest.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return (
            f"✓ 已更新接入路径：\n"
            f"  项目：{proj.name}\n"
            f"  旧：{old}\n"
            f"  新：{file_ref}\n\n"
            f"重启 Unity Editor 让新源码生效。"
        )

    # 5. 注入
    deps[_PKG_NAME] = file_ref
    manifest.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return (
        f"✓ 已接入 {_PKG_NAME} → {proj.name}\n"
        f"  源码：{file_ref}\n\n"
        f"下一步（你来做）：\n"
        f"  1. Unity Hub 打开 {proj.name} 项目（已开则关掉再开，让 Package Manager 重新解析 manifest）\n"
        f"  2. 第一次会拉 newtonsoft-json 依赖，等几十秒\n"
        f"  3. 顶部菜单 Window → MCP For Unity → 点 Start Session（红点变绿）\n"
        f"  4. 告诉我可以了，我用 unity 工具试试看场景"
    )


@tool
def remove_unity_mcp_bridge(unity_project_path: str) -> str:
    """从 Unity 项目移除 MCPForUnity Bridge 包接入。

    主人临时不想让有希连这个项目时调。只删 manifest.json 那一行，
    不删源码本体（其他项目可能还在用）。

    Args:
        unity_project_path: Unity 项目根目录
    """
    proj = Path(unity_project_path).expanduser().resolve()
    manifest = proj / "Packages" / "manifest.json"
    if not manifest.is_file():
        return f"❌ 不像 Unity 项目：{proj}（缺 Packages/manifest.json）"

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as e:
        return f"❌ manifest.json 解析失败：{type(e).__name__}: {e}"

    deps = data.get("dependencies") or {}
    if _PKG_NAME not in deps:
        return f"✓ 该项目本来就没接入 {_PKG_NAME}，无需操作。"

    old = deps.pop(_PKG_NAME)
    data["dependencies"] = deps
    manifest.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return (
        f"✓ 已移除接入：{proj.name}\n"
        f"  原引用：{_PKG_NAME} = {old}\n\n"
        f"在 Unity 里 Package Manager 会自动卸载该包；如菜单还在，重启 Editor。"
    )


# ── 进程探测：当前打开的 Unity 项目 ────────────────────────────────────

def _list_unity_cmdlines_via_wmic() -> list[str] | None:
    """老路：wmic 列 Unity.exe 进程的 CommandLine。

    Win11 24H2 (build 26200) 之后 wmic 被默认移除，此时返回 None 触发 fallback。
    创建子进程时加 CREATE_NO_WINDOW 防 GUI 模式弹黑窗。
    """
    creationflags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='Unity.exe'", "get", "CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=8, creationflags=creationflags,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, OSError):
        return None
    except subprocess.SubprocessError:
        return None
    lines = [ln.strip()[len("CommandLine="):] for ln in result.stdout.splitlines()
             if ln.strip().startswith("CommandLine=") and len(ln.strip()) > len("CommandLine=")]
    return lines


def _list_unity_cmdlines_via_powershell() -> list[str]:
    """新路：PowerShell + Get-CimInstance。Win11 24H2+ 唯一选择。"""
    creationflags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\" "
             "| ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, timeout=10, creationflags=creationflags,
            encoding="utf-8", errors="replace",
        )
    except Exception:
        return []
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]


def _extract_project_path_from_cmdline(cmdline: str) -> str | None:
    """从 Unity.exe 命令行里抽 ``-projectPath <path>``（大小写不敏感）。

    Unity 实际格式有两种，都要兼容：
    - Editor 直接调：``Unity.exe -projectPath "F:\\..."`` （驼峰）
    - Hub 启动：``Unity.exe -projectpath "F:\\..." -useHub ...`` （全小写）

    支持引号包围 / 无引号两种路径格式。
    """
    lower = cmdline.lower()
    flag_lower = _UNITY_PROJECT_FLAG.lower()
    idx = lower.find(flag_lower)
    if idx < 0:
        return None
    after = cmdline[idx + len(flag_lower):].lstrip()
    if not after:
        return None
    if after.startswith('"'):
        end = after.find('"', 1)
        if end < 0:
            return None
        return after[1:end]
    # 无引号：找下一个 " -" 或字符串末尾
    end = after.find(" -")
    return (after[:end] if end > 0 else after).strip()


@tool
def detect_current_unity_project() -> str:
    """探测当前你打开的 Unity 项目（路径）。

    通过查正在运行的 ``Unity.exe`` 进程的命令行参数 ``-projectPath`` 获取。
    多开 Unity 时返回所有路径。没在跑 Unity 时返回提示。

    用法：开了 Unity 项目想让有希接管时，先调本工具拿到路径，再调
    ``setup_unity_mcp_bridge(unity_project_path=...)`` 接入，省得手贴路径。
    """
    if os.name != "nt":
        return "❌ 当前仅支持 Windows（依赖 wmic / PowerShell 查进程）。"

    cmdlines = _list_unity_cmdlines_via_wmic()
    if cmdlines is None:
        # wmic 没了，转 PowerShell
        cmdlines = _list_unity_cmdlines_via_powershell()

    if not cmdlines:
        return (
            "ℹ️ 没探测到正在运行的 Unity Editor。\n"
            "  - 打开 Unity Hub → 启动一个项目 → 再来调本工具\n"
            "  - 或直接告诉我 Unity 项目绝对路径"
        )

    projects = []
    for cmdline in cmdlines:
        p = _extract_project_path_from_cmdline(cmdline)
        if p and p not in projects:
            projects.append(p)

    if not projects:
        return (
            f"⚠️ 找到 {len(cmdlines)} 个 Unity.exe 进程但都没有 -projectPath 参数。\n"
            f"  Unity 可能是 Hub 模式而非项目模式启动的。"
        )

    if len(projects) == 1:
        proj = Path(projects[0])
        manifest = proj / "Packages" / "manifest.json"
        mcp_status = ""
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if _PKG_NAME in (data.get("dependencies") or {}):
                    mcp_status = "  MCP Bridge: 已接入 ✓"
                else:
                    mcp_status = "  MCP Bridge: 未接入（要接入调 setup_unity_mcp_bridge）"
            except Exception:
                pass
        return f"✓ 当前 Unity 项目：{projects[0]}\n{mcp_status}".rstrip()

    return "✓ 检测到多个 Unity Editor 实例：\n" + "\n".join(
        f"  - {p}" for p in projects
    )


# ── 扫盘：列出本机 Unity 项目 ──────────────────────────────────────────

_UNITY_PROJECT_MARKERS = ("Assets", "Packages", "ProjectSettings")


def _is_unity_project(p: Path) -> bool:
    """快速判定一个目录是否 Unity 项目根：含 Assets/Packages/ProjectSettings + manifest.json。"""
    if not p.is_dir():
        return False
    for marker in _UNITY_PROJECT_MARKERS:
        if not (p / marker).is_dir():
            return False
    return (p / "Packages" / "manifest.json").is_file()


@tool
def list_unity_projects(search_root: str = "") -> str:
    """列出指定根目录下所有 Unity 项目，标注谁已接入 MCP。

    Args:
        search_root: 扫描根目录（如 ``F:/unity_program``）。空则按候选盘
            F:/D:/E:/C: 下的 ``unity_program/`` 都扫一遍。

    返回每个项目一行：``[mcp]`` 或 ``[ ]`` 标记是否接入 + 项目名 + 绝对路径。
    """
    if search_root.strip():
        roots = [Path(search_root.strip())]
    else:
        roots = [Path(f"{d}:/unity_program") for d in ("F", "D", "E", "C")]

    found_any_root = False
    project_lines: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        found_any_root = True
        # 只扫一层子目录（不递归，避免误判子文件夹）
        for child in sorted(root.iterdir()):
            if not _is_unity_project(child):
                continue
            try:
                manifest = child / "Packages" / "manifest.json"
                data = json.loads(manifest.read_text(encoding="utf-8"))
                has_mcp = _PKG_NAME in (data.get("dependencies") or {})
            except Exception:
                has_mcp = False
            mark = "[mcp]" if has_mcp else "[   ]"
            project_lines.append(f"  {mark} {child.name}  ({child})")

    if not found_any_root:
        return (
            f"❌ 候选目录都不存在：\n  - "
            + "\n  - ".join(str(r) for r in roots)
            + "\n传入 search_root 参数指定实际目录。"
        )
    if not project_lines:
        return f"ℹ️ 在 {[str(r) for r in roots if r.is_dir()]} 下没找到 Unity 项目。"

    header = f"找到 {len(project_lines)} 个 Unity 项目（[mcp]=已接入 Bridge）："
    return header + "\n" + "\n".join(project_lines)
