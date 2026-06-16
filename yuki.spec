# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件打包配置。

产出：
- Windows:   dist/yuki.exe
- macOS:     dist/yuki  +  dist/yuki.app（双击启动）
- Linux:     dist/yuki

打包命令：
    pyinstaller yuki.spec --noconfirm

清理：
    rmdir /s /q build dist  (Windows)
    rm -rf build dist        (Unix)

资源策略：
- 代码（.py）→ 打进 exe 内部
- prompts/ assets/  → 也打进 exe，首次启动 launcher 解压到 exe 旁（用户可改）
- templates/ static/ → 打进 exe，FastAPI 直接从 sys._MEIPASS 读（用户不改）
- .sandbox/ .memory/ skills/ → **不打包**，运行时在 exe 旁生成
- models/ → **不打包**（太大，用户自行下载或软链接）
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

PROJECT_ROOT = Path(SPECPATH)

# ── 大依赖完整收集（避免 PyInstaller 静态分析漏文件 / 子模块）──
# 这些库内部用 importlib.resources / pkg_resources 等动态加载，
# PyInstaller 默认 hooks 收得不全，必须用 collect_all 强制收齐。
_extra_datas = []
_extra_binaries = []
_extra_hiddenimports = []

for pkg in (
    "sentence_transformers",  # bge embedding 模型加载
    "transformers",           # sentence-transformers 依赖
    "tokenizers",
    "chromadb",
    "huggingface_hub",
    "safetensors",
    "weixin_ilink",           # 微信 iLink Bot SDK（含 cryptography / requests / 子模块）
    "mcp",                    # MCP 客户端 SDK（含 client.stdio + ClientSession 子模块）
):
    try:
        d, b, h = collect_all(pkg)
        _extra_datas += d
        _extra_binaries += b
        _extra_hiddenimports += h
    except Exception as e:
        print(f"[spec] collect_all {pkg} 失败: {e}")

# ── 数据资源：(源路径, 目标相对路径) ──
datas = [
    # 模板 + 静态资源
    (str(PROJECT_ROOT / "templates"), "templates"),
    (str(PROJECT_ROOT / "static"),    "static"),
    # 资产（图标 / 背景图等）—— launcher 会把这个解压到 exe 旁
    (str(PROJECT_ROOT / "assets"),    "assets"),
    # Prompt 文件 —— launcher 会把这个解压到 exe 旁（让 yuki 可改）
    (str(PROJECT_ROOT / "prompts"),   "prompts"),
]

# ── 隐式 import（PyInstaller 静态分析不到的）──
hiddenimports = [
    # uvicorn workers
    "uvicorn.logging",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    # pywebview 平台后端
    "webview.platforms.winforms",     # Windows
    "webview.platforms.cocoa",        # Mac
    "webview.platforms.gtk",          # Linux
    "webview.platforms.qt",           # Linux fallback
    # pystray 后端
    "pystray._win32",
    "pystray._darwin",
    "pystray._xorg",
    "pystray._appindicator",
    # 项目模块（tools 动态注册）
    "tools",
    "tools.basic",
    "tools.execute",
    "tools.memory_tools",
    "tools.files",
    "tools.skills",
    "tools.todo",
    "tools.dialog",
    "tools.vision",
    "tools.shell",
    "tools.self_edit",
    "tools.spawn_sub",
    "tools.ui_check",
    "tools.plan",
    "tools.verify",
    "tools.postmortem",
    "tools.code_indexer",
    "tools.coding",
    "tools.changelog",
    "tools.sub_complete",
    "tools.search",
    "tools._search",
    "tools._search.core",
    "tools._search.router",
    "tools._search.cache",
    "tools._search.filters",
    "tools._search.models",
    "tools._search.proxies",
    "tools._search.headers",
    "tools._search.engines",
    "tools._search.engines.bing",
    "tools._search.engines.ddg",
    "tools._search.engines.github",
    "tools._search.engines.stackoverflow",
    "tools._search.engines.bilibili",
    "tools._search.engines.taptap",
    "tools._search.engines.arxiv",
    "tools._search.engines.wiki",
    "tools.venv_install",
    # 微信桥接：launcher.py 在 frozen 模式下 import wechat_bridge 跑线程，
    # 必须显式声明否则 PyInstaller 静态分析不到（launcher 里是字符串 import）
    "wechat_bridge",
    "timing",
    # ChromaDB 后端
    "chromadb.utils",
    "chromadb.utils.embedding_functions",
    "sentence_transformers",
    # 业务库
    "openpyxl.cell._writer",
]

a = Analysis(
    ["launcher.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=_extra_binaries,
    datas=datas + _extra_datas,
    hiddenimports=hiddenimports + _extra_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 大而无用：减小 exe 体积
        "tkinter",
        "matplotlib.tests",
        "numpy.tests",
        "pandas.tests",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── PyInstaller bootloader splash ──────────────────────────────────────
# 在 C 层显示，bootloader 解压 _MEIPASS 之前就出现，覆盖 5-10 秒解压期。
# 仅当 assets/splash.png 存在时启用（Mac 不支持，自动 fallback）。
splash_img = PROJECT_ROOT / "assets" / "splash.png"
if not splash_img.exists():
    splash_img = PROJECT_ROOT / "assets" / "icon.png"  # 兜底用 icon

if sys.platform != "darwin" and splash_img.exists():
    splash = Splash(
        str(splash_img),
        binaries=a.binaries,
        datas=a.datas,
        text_pos=None,            # 不在图上叠文字（保持画面干净）
        text_size=12,
        minify_script=True,
        always_on_top=True,
    )
else:
    splash = None

# Windows / Linux 用 EXE，Mac 用 BUNDLE
if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="yuki",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        icon=str(PROJECT_ROOT / "assets" / "icon.icns") if (PROJECT_ROOT / "assets" / "icon.icns").exists() else None,
    )
    app = BUNDLE(
        exe,
        name="yuki.app",
        icon=str(PROJECT_ROOT / "assets" / "icon.icns") if (PROJECT_ROOT / "assets" / "icon.icns").exists() else None,
        bundle_identifier="com.yuki.desktop",
        info_plist={
            "CFBundleName": "有希",
            "CFBundleDisplayName": "有希",
            "LSUIElement": False,
            "NSHighResolutionCapable": True,
        },
    )
else:
    # Windows / Linux：--onedir 模式
    # 产出 yuki.exe (~3MB) + _internal/ (~390MB)
    # 比 --onefile 启动快 5-10 秒（跳过解压 _MEIPASS 到 %TEMP% 的步骤）
    # 配合 launcher.py 延后 import，HTML 动画 splash 能覆盖真实加载期
    icon_file = None
    if sys.platform == "win32":
        ico = PROJECT_ROOT / "assets" / "icon.ico"
        if ico.exists():
            icon_file = str(ico)

    # onedir 模式 EXE() 只含启动器，依赖留给 COLLECT()
    exe_args = [pyz, a.scripts]
    if splash is not None:
        exe_args.append(splash)
    exe_args.append([])
    exe_args.append(a.dependencies)

    exe = EXE(
        *exe_args,
        exclude_binaries=True,           # 关键：onedir 模式必须 True
        name="yuki",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,                    # --noconsole：不弹黑窗
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_file,
    )

    # 把所有依赖收集到 _internal/ 目录（PyInstaller 6 默认目录名）
    collect_args = [exe]
    if splash is not None:
        collect_args.append(splash.binaries)
    collect_args.extend([a.binaries, a.zipfiles, a.datas])

    coll = COLLECT(
        *collect_args,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="yuki",                      # dist/yuki/ 目录
    )
