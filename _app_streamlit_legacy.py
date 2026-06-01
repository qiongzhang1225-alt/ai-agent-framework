import uuid
import warnings
from pathlib import Path
warnings.filterwarnings("ignore", category=DeprecationWarning)

import streamlit as st
from langchain_core.messages import HumanMessage

_PROJECT_ROOT = Path(__file__).parent.resolve()
_WORKSPACE_ROOT = _PROJECT_ROOT / ".sandbox" / "workspace"


# ── 生成物预览辅助函数 ────────────────────────────────────────────────────────

def _list_workdir_files(workdir: str) -> set:
    """递归列出工作目录下所有文件（绝对路径）。"""
    root = Path(workdir)
    if not root.exists():
        return set()
    return {p for p in root.rglob("*") if p.is_file()}


def _human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def _render_file_preview(file_path: Path) -> None:
    """按扩展名自动预览：图片/表格/文本直显，其他给下载按钮。"""
    name = file_path.name
    suffix = file_path.suffix.lower()

    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        st.image(str(file_path), caption=name, use_container_width=True)
        return

    if suffix in {".csv", ".tsv"}:
        try:
            import pandas as pd
            sep = "\t" if suffix == ".tsv" else ","
            df = pd.read_csv(file_path, sep=sep, nrows=20)
            st.markdown(f"**📊 {name}**（前 20 行）")
            st.dataframe(df, use_container_width=True)
        except Exception as e:
            st.error(f"{name} 预览失败：{e}")
        return

    if suffix in {".xlsx", ".xls"}:
        try:
            import pandas as pd
            df = pd.read_excel(file_path, nrows=20)
            st.markdown(f"**📊 {name}**（前 20 行）")
            st.dataframe(df, use_container_width=True)
        except Exception as e:
            st.error(f"{name} 预览失败：{e}")
        return

    if suffix in {".txt", ".md", ".log", ".json", ".yaml", ".yml", ".py"}:
        try:
            content = file_path.read_text(encoding="utf-8")
            truncated = content if len(content) <= 2000 else content[:2000] + "\n...(已截断)"
            st.markdown(f"**📄 {name}**")
            if suffix == ".md":
                st.markdown(truncated)
            else:
                lang_map = {
                    ".py": "python", ".json": "json",
                    ".yaml": "yaml", ".yml": "yaml",
                }
                st.code(truncated, language=lang_map.get(suffix))
        except Exception as e:
            st.error(f"{name} 预览失败：{e}")
        return

    # docx / pdf / pptx / 其他二进制 → 给下载按钮
    try:
        size_str = _human_size(file_path.stat().st_size)
        with open(file_path, "rb") as f:
            st.download_button(
                label=f"💾 {name}（{size_str}）",
                data=f.read(),
                file_name=name,
                key=f"dl_{file_path}",
                use_container_width=True,
            )
    except Exception as e:
        st.error(f"{name} 无法读取：{e}")


# ── 自定义样式 / 图标（自动检测 assets/）──────────────────────────────────────

def _resolve_page_icon():
    """assets/icon.png 存在则用作浏览器标签图标，否则用 emoji 兜底。"""
    icon_path = _PROJECT_ROOT / "assets" / "icon.png"
    return str(icon_path) if icon_path.exists() else "🛰️"


def _inject_custom_style() -> None:
    """从 assets/ 自动加载背景与样式（文件不存在则跳过）。

    背景图行为：
    - **保持原始像素**，不拉伸、不放大
    - **平铺铺满**整个视口（视口比图小则裁剪，比图大则重复）
    - 超过 2048px 的大图自动用 Pillow 缩小，避免 base64 传输过慢
    """
    import base64
    css_parts = []

    def _load_bg_data(path):
        """读取图片为 (mime, base64)。超过 2048px 自动缩小。"""
        try:
            from PIL import Image
            import io
            MAX_DIM = 2048
            with Image.open(path) as img:
                if max(img.size) > MAX_DIM:
                    img.thumbnail((MAX_DIM, MAX_DIM))
                    buf = io.BytesIO()
                    if path.suffix.lower() == ".png":
                        img.save(buf, format="PNG", optimize=True)
                        return "image/png", base64.b64encode(buf.getvalue()).decode()
                    if img.mode in ("RGBA", "LA", "P"):
                        img = img.convert("RGB")
                    img.save(buf, format="JPEG", quality=85, optimize=True)
                    return "image/jpeg", base64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass  # Pillow 失败时直接原文件返回
        ext = path.suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        return mime, base64.b64encode(path.read_bytes()).decode()

    def _load_bg(filename: str, css_selector: str) -> bool:
        for ext in ("png", "jpg", "jpeg", "webp"):
            path = _PROJECT_ROOT / "assets" / f"{filename}.{ext}"
            if path.exists():
                mime, b64 = _load_bg_data(path)
                css_parts.append(f"""
                {css_selector} {{
                    background-image: url("data:{mime};base64,{b64}");
                    background-size: auto;
                    background-repeat: repeat;
                    background-position: top left;
                    background-attachment: fixed;
                }}
                """)
                return True
        return False

    has_main_bg = _load_bg("background", ".stApp")
    has_sidebar_bg = _load_bg(
        "sidebar_bg", '[data-testid="stSidebar"] > div:first-child'
    )

    # 主背景启用时，让 Streamlit 各容器透明 / 半透明，保证背景图覆盖整个视口
    if has_main_bg:
        css_parts.append("""
        /* === 容器透明化 === */
        [data-testid="stHeader"] {
            background-color: transparent !important;
        }
        [data-testid="stMain"],
        .main .block-container {
            background-color: transparent !important;
        }
        /* 给主容器底部留空间，防止消息被 popover + chat_input 遮挡 */
        .main .block-container {
            padding-bottom: 220px !important;
        }

        /* === 文字可读性 === */
        .main h1, .main h2, .main h3 {
            color: #f5f5f5 !important;
            text-shadow: 0 2px 8px rgba(0, 0, 0, 0.7);
        }
        .main [data-testid="stCaptionContainer"],
        .main [data-testid="stCaptionContainer"] * {
            color: #e0e0e0 !important;
            text-shadow: 0 1px 4px rgba(0, 0, 0, 0.6);
        }

        /* === 聊天气泡：保持白色半透明（深色背景上更易读） === */
        [data-testid="stChatMessage"] {
            background-color: rgba(255, 255, 255, 0.88);
            border-radius: 10px;
            padding: 1rem;
            backdrop-filter: blur(3px);
        }

        /* === chat_input 深色毛玻璃 === */
        [data-testid="stChatInput"] {
            background-color: rgba(20, 20, 30, 0.45) !important;
            backdrop-filter: blur(18px) !important;
            -webkit-backdrop-filter: blur(18px) !important;
            border-top: 1px solid rgba(255, 255, 255, 0.12) !important;
        }
        /* 内层 div 全部透明，让外层毛玻璃显示出来 */
        [data-testid="stChatInput"] > div,
        [data-testid="stChatInput"] > div > div,
        [data-testid="stChatInput"] [data-baseweb="textarea"],
        [data-testid="stChatInput"] [data-baseweb="base-input"] {
            background-color: transparent !important;
        }
        /* textarea：透明 + 浅色字 + 浅色光标 */
        [data-testid="stChatInput"] textarea {
            background-color: transparent !important;
            color: #f0f0f0 !important;
            caret-color: #f0f0f0 !important;
        }
        [data-testid="stChatInput"] textarea::placeholder {
            color: rgba(240, 240, 240, 0.5) !important;
        }
        /* 发送按钮跟 chat_input 风格一致 */
        [data-testid="stChatInput"] button {
            color: #f0f0f0 !important;
        }

        /* === 工作目录 popover：fixed 到 chat_input 上方 === */
        div[data-testid="stPopover"] {
            position: fixed !important;
            bottom: 100px;
            left: 50%;
            transform: translateX(-50%);
            width: min(calc(100vw - 4rem), 700px);
            z-index: 99;
        }
        /* popover 按钮：深色毛玻璃，跟 chat_input 风格一致 */
        div[data-testid="stPopover"] button {
            background-color: rgba(20, 20, 30, 0.5) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            border: 1px solid rgba(255, 255, 255, 0.18) !important;
            color: #f0f0f0 !important;
            transition: background-color 0.2s ease;
        }
        div[data-testid="stPopover"] button:hover {
            background-color: rgba(35, 35, 55, 0.65) !important;
        }
        """)

        # Sidebar：若用户没单独提供 sidebar_bg，则用半透明深色 + 白字（深色风格统一）
        if not has_sidebar_bg:
            css_parts.append("""
            [data-testid="stSidebar"] > div:first-child {
                background-color: rgba(20, 20, 30, 0.75) !important;
                backdrop-filter: blur(4px);
            }
            [data-testid="stSidebar"] h1,
            [data-testid="stSidebar"] h2,
            [data-testid="stSidebar"] h3,
            [data-testid="stSidebar"] label,
            [data-testid="stSidebar"] p,
            [data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {
                color: #f0f0f0 !important;
            }
            """)

    if css_parts:
        st.markdown(f"<style>{''.join(css_parts)}</style>", unsafe_allow_html=True)


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="信息统合思念体",
    page_icon=_resolve_page_icon(),
    layout="centered",
)

_inject_custom_style()

# ── 模型选项 ──────────────────────────────────────────────────────────────────

MODELS = {
    "DeepSeek V4 Flash（快速·省钱）": "deepseek-v4-flash",
    "DeepSeek V4 Pro（旗舰·强大）":   "deepseek-v4-pro",
}

# ── Agent（按模型缓存）────────────────────────────────────────────────────────

@st.cache_resource
def load_agent(model_id: str):
    from agent import create_agent
    return create_agent(model_id)

# ── Session state ─────────────────────────────────────────────────────────────

def new_conversation():
    thread_id = str(uuid.uuid4())
    workdir = _WORKSPACE_ROOT / thread_id
    workdir.mkdir(parents=True, exist_ok=True)
    st.session_state.conversations[thread_id] = {
        "name": "新对话",
        "messages": [],
        "workdir": str(workdir),
    }
    st.session_state.current_thread_id = thread_id

if "conversations" not in st.session_state:
    st.session_state.conversations = {}
    new_conversation()

if "current_thread_id" not in st.session_state:
    new_conversation()

if "model_name" not in st.session_state:
    st.session_state.model_name = "DeepSeek V4 Flash（快速·省钱）"

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("信息统合思念体")

    # 模型选择
    selected_name = st.selectbox(
        "模型",
        options=list(MODELS.keys()),
        index=list(MODELS.keys()).index(st.session_state.model_name) if st.session_state.model_name in MODELS else 0,
    )
    st.session_state.model_name = selected_name

    if selected_name == "DeepSeek V4 Pro（旗舰·强大）":
        st.caption("旗舰模型，适合复杂推理、编程等任务")

    st.divider()

    if st.button("➕ 新对话", use_container_width=True, type="primary"):
        new_conversation()
        st.rerun()

    st.divider()

    for thread_id in reversed(list(st.session_state.conversations)):
        conv = st.session_state.conversations[thread_id]
        is_active = thread_id == st.session_state.current_thread_id
        col_name, col_del = st.columns([5, 1])
        with col_name:
            label = f"**{conv['name']}**" if is_active else conv["name"]
            if st.button(label, key=f"sel_{thread_id}", use_container_width=True):
                st.session_state.current_thread_id = thread_id
                st.rerun()
        with col_del:
            if st.button("🗑", key=f"del_{thread_id}"):
                del st.session_state.conversations[thread_id]
                remaining = list(st.session_state.conversations)
                if remaining:
                    st.session_state.current_thread_id = remaining[-1]
                else:
                    new_conversation()
                st.rerun()


# ── Main chat ─────────────────────────────────────────────────────────────────

current_thread_id = st.session_state.current_thread_id
current_conv = st.session_state.conversations[current_thread_id]
model_id = MODELS[st.session_state.model_name]

# 兼容：早期会话可能没有 workdir 字段
if "workdir" not in current_conv:
    _wd = _WORKSPACE_ROOT / current_thread_id
    _wd.mkdir(parents=True, exist_ok=True)
    current_conv["workdir"] = str(_wd)

st.header(current_conv["name"])

for msg in current_conv["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 工作目录按钮：整条紧贴 chat_input 上方，点击展开修改面板
with st.popover(
    f"📂 工作目录：{current_conv['workdir']}",
    use_container_width=True,
    help="点击修改",
):
    st.markdown("**当前对话工作目录**")
    st.caption("Agent 只能在该目录内读写文件，越界会被拒绝。")
    _new_wd = st.text_input(
        "路径",
        value=current_conv["workdir"],
        key=f"wd_input_{current_thread_id}",
        label_visibility="collapsed",
    )
    if st.button(
        "应用",
        key=f"wd_apply_{current_thread_id}",
        type="primary",
        use_container_width=True,
    ):
        try:
            _wd_path = Path(_new_wd).expanduser().resolve()
            _wd_path.mkdir(parents=True, exist_ok=True)
            current_conv["workdir"] = str(_wd_path)
            st.success(f"已切换到 {_wd_path}")
            st.rerun()
        except Exception as e:
            st.error(f"无法使用该目录: {e}")

if prompt := st.chat_input("输入你的问题..."):
    if current_conv["name"] == "新对话":
        current_conv["name"] = prompt[:18] + ("…" if len(prompt) > 18 else "")

    current_conv["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 快照执行前的工作目录文件，便于稍后展示新增产物
    files_before = _list_workdir_files(current_conv["workdir"])

    with st.chat_message("assistant"):
        tool_status = st.empty()
        placeholder = st.empty()
        full_response = ""

        try:
            for chunk, metadata in load_agent(model_id).stream(
                {"messages": [HumanMessage(content=prompt)]},
                config={
                    "configurable": {
                        "thread_id": current_thread_id,
                        "workdir": current_conv["workdir"],
                    }
                },
                stream_mode="messages",
            ):
                node = metadata.get("langgraph_node", "")

                if node == "tools":
                    tool_name = getattr(chunk, "name", None)
                    if tool_name:
                        tool_status.caption(f"🔧 正在调用工具：`{tool_name}`")

                elif node == "agent":
                    content = getattr(chunk, "content", "")
                    if isinstance(content, str) and content:
                        full_response += content
                        placeholder.markdown(full_response + "▌")

            tool_status.empty()
            placeholder.markdown(full_response or "（无响应）")

        except Exception as e:
            full_response = f"出错了：{e}"
            placeholder.markdown(full_response)

        # 展示本次新生成的文件
        new_files = sorted(
            _list_workdir_files(current_conv["workdir"]) - files_before
        )
        if new_files:
            with st.expander(f"📎 本次生成 {len(new_files)} 个文件", expanded=True):
                for f in new_files:
                    _render_file_preview(f)

    current_conv["messages"].append({"role": "assistant", "content": full_response})
