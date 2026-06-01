# AI Agent

基于 DeepSeek + LangGraph 的个人助手 Agent，定位是**编程辅助 + 办公自动化**，跟着实际需求迭代扩展。

---

## 技术栈

| 项目 | 选择 | 说明 |
| --- | --- | --- |
| 模型 | DeepSeek V4（Flash / Pro 可切换） | 性价比优先，复杂任务上 Pro |
| Agent 框架 | **自建 `ai_agent/`** | ReAct loop + 工具注册 + 流式 + checkpoint，零 LangChain 依赖 |
| LLM 客户端 | httpx 直连 DeepSeek 官方 API | OpenAI 兼容，流式 + tool calling + reasoning_content |
| 前端 | FastAPI + Tailwind + Alpine.js（CDN） | 多会话、SSE 流式、工具调用提示、毛玻璃雪夜主题 |
| 对话持久化 | `ai_agent.JSONCheckpoint` | 每对话一个 conv.json，含完整 tool_calls / reasoning_content |
| 长期记忆 | ChromaDB | 本地向量库，跨会话 remember / recall |

---

## 快速开始

> **重要**：本项目**所有依赖必须安装到项目根目录下的 `.venv` 虚拟环境**，不要装到系统 Python。
>
> **Agent 不会自动安装 Python 包。** 如果它在工作过程中发现缺库，会停下来告诉你需要装什么，由你决定是否安装 —— 这样可以避免恶意包、拼写错误包（typosquatting）、静默环境污染等风险。

```bash
# 1. 创建并激活虚拟环境（必做）
python -m venv .venv
.venv\Scripts\activate              # Windows
# source .venv/bin/activate         # macOS / Linux

# 2. 装项目依赖（装到 .venv 里）
pip install -r requirements.txt

# 3. 配置 API Key
copy .env.example .env              # Windows
# cp .env.example .env              # macOS / Linux
# 然后编辑 .env，填入 DEEPSEEK_API_KEY

# 4. 启动（确保仍在已激活的 .venv 中）
start.bat                           # Windows 一键启动 Streamlit（已绑定 .venv）
# 或：
streamlit run app.py                # Web UI
python agent.py                     # 命令行模式
```

### 环境约定

- **所有依赖统一装到 `.venv`**：手动 `pip install` 前先确认命令行提示符前面出现 `(.venv)`。
- **Agent 不会自动装包**：缺库时它会停下来告诉你装什么，由你决定。这是有意识的安全选择，避免恶意包 / typosquatting / 静默环境污染。
- **常用办公自动化库已预装**：`pandas`、`openpyxl`、`numpy`、`matplotlib`、`python-docx`、`pdfplumber` 都在 `requirements.txt`，一次 `pip install -r requirements.txt` 就齐了。
- **新装的长期依赖记得加进 `requirements.txt`**，避免下次重建环境时遗漏。

---

## 当前进度

### 已实现

**核心**

- 接入 DeepSeek V4（Flash 默认 / Pro 可选）
- LangGraph ReAct Agent Loop，工具自动调度
- 进程内对话记忆（多会话独立 `thread_id`）

**交互**

- Streamlit Web UI：多会话管理、模型切换、流式输出、工具调用实时提示
- 命令行 REPL（`python agent.py`）

**工具**

| 工具 | 作用 |
| --- | --- |
| `web_search` | DuckDuckGo 联网搜索 |
| `fetch_webpage` | 抓取网页正文（自动剥离 script/nav/footer） |
| `calculate` | AST 安全求值（仅 `+ - * / ** %`，无注入风险） |
| `get_current_datetime` | 当前日期时间（含星期） |
| `execute_code` | 在受限工作目录执行 Python 代码（subprocess 隔离 + 路径守卫） |

---

### 阶段 1：人设 + 工具补强 ✅

- [x] **角色卡：长门有希** — 第二人称"你"，自称"有希"，短句风格 + 信息完整模式例外
- [x] **预装库扩充** — pandas / openpyxl / matplotlib / seaborn / python-docx / python-pptx / pdfplumber / reportlab / Pillow
- [x] **生成物预览 UI** — `execute_code` 后自动展示图片、表格、文件下载

### 阶段 2：前端重写 + 办公路径 ✅

- [x] **FastAPI + Tailwind + Alpine** 替换 Streamlit（端口 3616）
- [x] **文件拖拽上传** — chat input 区域，自动复制到当前 workdir
- [x] **工作目录切换** UI

### 阶段 3：长期记忆 ✅

- [x] **ChromaDB 集成** — `.memory/` 持久化向量库
- [x] **`remember(fact)` / `recall(topic)` 工具** — 显式记忆读写
- [x] **记忆 UI** — sidebar "📚 记忆库" 入口 → modal 弹层
- [x] **新对话自动 recall** — Prompt 引导

### 阶段 4：对话持久化 ✅

- [x] **每对话独立 conv.json**，含完整 tool_calls / reasoning_content
- [x] **启动时自动加载** — 重启后历史完整恢复

### 阶段 5：LangChain → 自建框架 ✅

- [x] **Phase 1** — `ai_agent.messages` + `ai_agent.tools`
- [x] **Phase 2** — `ai_agent.llm.DeepSeekClient`（httpx 直连，含 reasoning_content）
- [x] **Phase 3** — `ai_agent.loop.Agent`（ReAct while-loop）
- [x] **Phase 4** — `ai_agent.persist.JSONCheckpoint`
- [x] **Phase 5** — 卸载所有 langchain/langgraph 包，零依赖

详见 [MIGRATION.md](MIGRATION.md)。

---

### 暂时搁置

- **视觉模型独立接入** — V4 Pro 自带视觉能力，需要时切换即可，不单独做
- **自动事实抽取** — 等显式记忆用熟、有明确取舍标准后再考虑
- **沙箱按需放开**（subprocess 白名单、git / ffmpeg 调用） — 遇到具体需求再决定
- **多 Agent 协作** — 等单 Agent 稳定后再拆
- **本地模型混用** — 等硬件升级后
- **现成的 OpenInterpreter 类工具** — 安全与可控性优先，宁可自己慢慢搭

---

## 安全模型：execute_code 的工作目录隔离

`execute_code` 是 Agent 唯一能写代码、跑代码的工具，所以是最大的攻击面。安全靠以下几层防御：

| 防御层 | 实现 |
| --- | --- |
| **进程隔离** | 每次执行起独立 subprocess，崩溃不影响 Agent 主进程 |
| **超时杀进程** | 默认 60 秒上限，避免死循环占住资源 |
| **会话级工作目录** | 每个对话独立 workdir，子进程的 CWD 锁定在此 |
| **路径守卫** | preamble 注入 monkey-patch，拦截 `builtins.open` 越界访问，越界抛 `PermissionError` |
| **禁止再起子进程** | 拦截 `subprocess.Popen` 和 `os.system`，避免绕过守卫 |
| **UTF-8 强制** | 子进程 stdout/stderr 强制 UTF-8，避免 Windows GBK 乱码 |
| **matplotlib 静默** | 注入 Agg 后端 + 中文字体，禁止弹窗 |

### 修改工作目录

- 默认每个对话自动分配 `.sandbox/workspace/<thread_id>/`
- 主界面 chat input **左下角的 📂 图标**可查看并修改
- 修改后 Agent 立刻在新目录下工作，无需重启

### 边界（诚实说明）

- 这是**"防误操作 + 防 Agent 走神"** 级别的隔离，**不是绝对沙箱**
- 真正强隔离（防御主动恶意代码）需要 Docker / 远程沙箱，本项目暂不打算做
- 单用户本地场景下，当前防御足够

---

## 设计原则

1. **执行前确认** — 写文件、改文件、执行代码这类有副作用的操作，先告知意图再执行
2. **权限最小化** — 每个工具只开它必须的能力，能限制路径就限制路径
3. **迭代扩展** — 先把最小闭环跑通，再一个个加技能，拒绝过度设计
4. **可观察** — Web UI 实时显示工具调用，方便排错和复盘

---

## 项目结构

```
AI-Agent/
├── agent.py           # 工具定义、execute_code、System Prompt、Agent 工厂、命令行入口
├── app.py             # Streamlit Web UI（多会话 + workdir 切换）
├── requirements.txt
├── start.bat          # Windows 启动脚本
├── .env.example       # API Key 模板
├── .gitignore
├── .sandbox/          # 运行时生成（每对话独立 workspace + 脚本/日志元数据）
└── README.md
```
