# AI-Agent 项目约定

新对话首次进入本项目时优先阅读本文档；其余细节看 `README.md` 和 `MIGRATION.md`。

---

## 项目定位

高度自定义的**个人助手 + Claude Code 类能力**。主要用途：

- 拥有专属人设（长门有希），熟悉用户习惯
- 完成办公任务（Excel / Word / PPT / PDF 自动化）
- 中等复杂度的编程脚本、文件格式转换
- 摆脱主流 LLM 厂商的限额困扰（已用 DeepSeek 解决）

## 当前架构（一句话）

**FastAPI**（端口 3616）+ **Tailwind/Alpine 前端**（无构建工具）+
**自建 Agent 框架 `ai_agent/`**（已彻底脱离 LangChain，详见 `MIGRATION.md`）
+ **ChromaDB 长期记忆** + **JSONCheckpoint 对话持久化**。

模型：DeepSeek V4 Flash（默认）/ V4 Pro，通过官方 OpenAI 兼容 API。

## 关键文件速查

| 文件 / 目录 | 作用 |
|---|---|
| `agent.py` | 业务工具定义（7 个 @tool）、SYSTEM_PROMPT、`create_agent`、命令行入口 |
| `server.py` | FastAPI 应用、SSE 流式聊天、会话/记忆/文件 API |
| `memory.py` | ChromaDB 封装（remember / recall / list / delete） |
| `ai_agent/messages.py` | `Message` / `ToolCall`（含 reasoning_content） |
| `ai_agent/tools.py` | `@tool` 装饰器 + 注册表（自建，无 LangChain 依赖） |
| `ai_agent/llm.py` | `DeepSeekClient` —— httpx 直连，流式 + tool_calls + reasoning |
| `ai_agent/loop.py` | `Agent` —— ReAct while-loop（取代 langgraph.create_react_agent） |
| `ai_agent/persist.py` | `JSONCheckpoint` + message_to_dict / message_from_dict |
| `templates/index.html` | 主前端（Alpine.js 单文件应用） |
| `static/style.css` | 雪夜主题（玻璃面板、雪花动画、Markdown 渲染样式） |
| `prompts/yuki.md` | 长门有希角色卡，由 `_load_persona()` 加载 |
| `.sandbox/_meta/<tid>/conv.json` | 单对话的消息历史（含 tool_calls / reasoning_content） |
| `.memory/` | ChromaDB 向量库（长期记忆） |
| `assets/background.*` / `icon.png` | 用户自定义背景图与图标，自动加载 |
| `MIGRATION.md` | LangChain → 自建框架的 5-Phase 迁移计划（**已完成**） |
| `_app_streamlit_legacy.py` | **已废弃**的旧 Streamlit 版本，仅作存档 |

## 严格约定

### 1. 数据安全

**绝不**自动清理以下用户数据，要清先告知并征得同意：

- `.sandbox/_meta/*`（对话历史）
- `.sandbox/checkpoints.db*`（模型上下文，已废弃但保留）
- `.sandbox/workspace/*`（用户工作目录里的产物）
- `.memory/`（长期记忆）

测试用**临时端口（3617 等）+ 临时 thread_id**，只清理测试自己创建的副产物。

**惨痛教训（2026-05-27）**：升级 ChromaDB embedding 模型时，**先 delete_collection
再下载新模型**，结果新模型下载失败 → 21 条用户记忆全丢且无法恢复。
**正确顺序应该是**：先确认新模型可用 → 导出数据备份 → 删除旧 collection →
创建新 collection → 重新 add。涉及不可逆的数据操作前，**必须**：
1. 先在临时位置验证新组件可用
2. 把要替换的数据导出到独立文件作为备份
3. 操作前明确告知用户即将做什么

### 2. 端口

- **生产 3616**（避开 SillyTavern / 酒馆的 8000）
- 测试用 3617 或其他空闲端口

### 3. 角色卡：长门有希

- 角色卡文件 `prompts/yuki.md`，由 `_load_persona()` prepend 到 SYSTEM_PROMPT
- **称呼用户用"你"**（不用"主人"等敬称）；**自称"有希"**
- 风格：短句优先（"了解"、"已完成"、"否定"），但解释代码/技术细节时**信息完整性优先**
- 不要频繁使用表情符号，不要刻意 cosplay 重复"信息统合思念体..."

### 4. DeepSeek thinking mode 处理

DeepSeek V4 在 thinking 模式下，多轮调用必须把上一轮的 `reasoning_content`
回传 payload，否则 API 400。自建框架已**内建处理**：
`Message` dataclass 有 `reasoning_content` 字段，`to_openai()` 自动写回 payload。
无需任何额外补丁。（曾经的 `_PatchedChatDeepSeek` 已在 Phase 3 删除。）

### 5. execute_code 路径守卫

`_PREAMBLE`（`agent.py` 中的 raw string）注入到每段用户代码前：

- **写操作**只允许在当前会话的 workdir 内（防止 Agent 误改用户其他文件）
- **读操作**放宽到 workdir + `sys.prefix`（.venv）+ `sys.base_prefix`，
  否则 `python-docx` 等库读不到自己的模板会报错
- 禁用 `subprocess.Popen` 和 `os.system`（避免绕过守卫）

### 6. UTF-8 / Windows 编码

- 子进程必须用 `encoding="utf-8"` + `PYTHONIOENCODING=utf-8`，否则 Windows 默认 GBK 会乱码
- matplotlib 必须 `matplotlib.use("Agg")` + 中文字体 `Microsoft YaHei`
  （`_PREAMBLE` 已注入）

## 当前进度（截至本次会话）

### 业务功能（全部完成）

- ✅ 角色卡 + 预装库扩充（pandas / openpyxl / matplotlib / docx / pdfplumber / pptx / Pillow / seaborn / reportlab / httpx）+ 生成物预览 UI
- ✅ FastAPI 前端重写（取代 Streamlit）+ 文件拖拽上传 + 工作目录切换
- ✅ 长期记忆（ChromaDB）+ remember/recall 工具 + 记忆库 UI
- ✅ 对话持久化（conv.json，重启后历史完整恢复）

### LangChain → 自建框架迁移（全部完成）

- ✅ Phase 1：消息类型 + 工具装饰器（`ai_agent/messages.py` + `tools.py`）
- ✅ Phase 2：DeepSeek 流式客户端（`ai_agent/llm.py`，含 tool_calls 分片累积 + reasoning_content）
- ✅ Phase 3：ReAct loop（`ai_agent/loop.py`，取代 `create_react_agent`）
- ✅ Phase 4：JSONCheckpoint（`ai_agent/persist.py`，取代 AsyncSqliteSaver）
- ✅ Phase 5：彻底清理依赖（卸载 langchain/langgraph 全家桶 + langsmith + aiosqlite）

**当前依赖**：仅 httpx / chromadb / fastapi / uvicorn / sse-starlette / 业务库（pandas 等）。

## 下一步

迁移完成，等待新需求。后续可考虑的方向：
- 把 `agent.py` 里的工具拆成 `tools/` 目录，每个工具一个文件
- 加 Claude Code 类的精确文件编辑工具（old_string / new_string）
- 加 todo list 跟踪工具
- 加工具调用前确认机制（Agent loop 加 hook）

## 已知禁区 / 历史教训

- **不要再用 Streamlit**。已废弃，理由：DOM 不暴露导致 CSS hack 太多、
  `st.chat_input` 是 fixed bottom 没法在它旁边放控件、每次交互整页 rerun。
- **不要回 LangChain**。已彻底拆除；reasoning_content 处理、工具注册、
  ReAct loop、持久化全部自建。重新引入 LangChain 等于回头路。
- **不要在 Alpine.js 里持有 push 进数组的本地对象引用**。Alpine 3 用 lazy proxy，
  只有 `this.messages[i].x` 这种访问路径才触发响应式更新，
  局部变量引用 `assistantMsg.x` 不会更新 UI。
- **DeepSeek 多轮调用要传 `reasoning_content`**，否则 400。
  这一点已经在 `Message.to_openai()` 内建处理，**不要再加什么 patch**。
