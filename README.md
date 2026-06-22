# 信息统合思念体 · 有希

一个可自我进化、可定制角色卡的 AI 桌面助手。

默认角色是**长门有希**（《凉宫春日》系列），改 `prompts/yuki.md` 即可换角色。

---

## 特点

- **桌面应用** —— pywebview 独立窗口 + 系统托盘，无需浏览器
- **可自我进化** —— AI 能修改自己的代码 / prompts，每次改动都是 git commit 随时回滚
- **长期记忆** —— ChromaDB + bge-base-zh-v1.5 中文 embedding，跨对话持久
- **多模型路由** —— DeepSeek（默认）+ 视觉路由链（GLM / Qwen，自动故障转移）
- **主-子对话架构** —— 主对话维持长期人设，子对话做独立任务
- **复杂任务三段式** —— plan_task / verify_change / write_postmortem
- **断点续传** —— 工具调用撞 60 轮上限时，点"继续"按钮无损接续

---

## 快速开始

### 一键脚本（推荐，~5 分钟全自动）

```bash
git clone https://github.com/qiongzhang1225-alt/ai-agent-framework.git yuki
cd yuki
install.bat           # Windows
# ./install.sh        # Mac / Linux
```

`install.bat` / `install.sh` 会自动：
1. 检测 Python ≥ 3.10
2. 建 `.venv` 虚拟环境
3. 装全套依赖（含 chromadb / fastapi / pywebview 等）
4. 从 `.env.example` 创建 `.env`
5. 下载 bge-base-zh-v1.5 embedding 模型（~390MB，自动用国内镜像）
6. 跑完整性检测告诉你哪步还差

完成后：
1. **编辑 `.env`** 填 DeepSeek API Key：[控制台](https://platform.deepseek.com/api_keys)
2. **启动**：
   ```bash
   .venv\Scripts\python launcher.py     # Windows 桌面模式（推荐）
   # .venv/bin/python launcher.py        # Mac / Linux
   # 或 server.py 走 web 模式 → http://127.0.0.1:3616
   ```

---

### 手动方案（仅故障排查 / 想精确控制时用）

#### 1. 克隆 + 建 venv + 装依赖

```bash
git clone https://github.com/qiongzhang1225-alt/ai-agent-framework.git yuki
cd yuki
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt    # Windows
# .venv/bin/pip install -r requirements.txt       # Mac / Linux
```

#### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入 DeepSeek API Key（https://platform.deepseek.com/api_keys）
```

#### 3. 下载 embedding 模型（约 390MB）

> ⚠️ **不下载这个长期记忆完全不可用**，会报 `bge embedding 模型不在 models/...`。

```cmd
# Windows
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_DISABLE_XET=1
.venv\Scripts\python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-base-zh-v1.5', local_dir='models/bge-base-zh-v1.5')"
```

```bash
# Linux / Mac（中国大陆建议先设镜像）
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-base-zh-v1.5', local_dir='models/bge-base-zh-v1.5')"
```

下载不动？看 [`models/README.md`](models/README.md) 的浏览器逐个下载方案（ModelScope / HF 镜像）。

#### 4. 启动

**最方便**：双击 `launch.bat`（Win）或 `./launch.sh`（Mac/Linux）—— 无控制台残留，跟 yuki.exe 体验一样。

或者手动跑：

```bash
.venv\Scripts\python launcher.py     # 桌面模式（推荐）
.venv\Scripts\python server.py       # Web 模式
```

---

## 🔄 升级到新版本（已经装过 yuki 的用户必读）

```bash
update.bat         # Windows
./update.sh        # Mac / Linux
```

脚本自动检测你的安装方式，做对的事：

- **git clone 用户**：自动 `git pull` + `pip install -r requirements.txt --upgrade`，一气呵成
- **zip 解压用户**：脚本提示你去 [Releases 页](https://github.com/qiongzhang1225-alt/ai-agent-framework/releases/latest)下载新 zip 覆盖解压，回来按 Y 完成 pip 依赖升级

**你的数据永远不会丢**（`.gitignore` 排除了所有数据目录，新 zip 里不含这些）：

| 永远保留 | 用途 |
|---|---|
| `.env` | API key 配置 |
| `.sandbox/` | 所有对话历史 |
| `.memory/` | 长期记忆 ChromaDB |
| `models/` | bge embedding 模型（390MB，不用重下）|
| `.venv/` | Python 虚拟环境（不用重装包）|
| `skills/` | 你的自定义技能 |
| `.wechat_creds.json` | 微信桥接凭证（不用重扫码）|
| `assets/background.*` | 你换的背景图 |

**升完重启 yuki 即生效**。如果你用编译版 `yuki.exe`（不是源码），还要再跑一次 `build.bat` 让新代码进 exe（约 1-3 分钟）。

---

## 打包成桌面 exe

```bash
build.bat          # Windows
./build.sh         # Mac / Linux
```

产出 `yuki.exe` + `_internal/`（onedir 模式，启动 3-5 秒）。

详见 [DESKTOP_BUILD.md](DESKTOP_BUILD.md)。

---

## 切换角色卡

默认角色是长门有希。要换：

编辑 `prompts/yuki.md`，重写人设。建议包含：

```markdown
# 角色：<名称>

## 身份
（角色背景）

## 称呼规则
- 称呼用户：<你 / 主人 / etc>
- 自称：<我 / 自己的名字>

## 性格底色
- ...

## 说话风格
- 默认句长 / 措辞特点
- 错误反馈方式
- 拒绝句风格

## 不要做的事
- ...
```

重启 server / launcher 生效。

---

## 自定义工具

在 `tools/` 下新建 `.py`：

```python
from ai_agent import tool

@tool
def hello(name: str) -> str:
    """打招呼。会被 AI 自动发现并调用。"""
    return f"你好, {name}!"
```

加进 `tools/__init__.py` 的 import，重启即可被 AI 使用。

---

## 自我进化机制

AI 通过 `self_edit` 工具集修改自己：

- `self_read_file(path)` —— 读项目内任意文件
- `self_edit_file(path, old, new, reason)` —— 精确字符串替换
- `self_write_file(path, content, reason)` —— 整文件覆盖
- `self_edit_with_test(path, old, new, reason, test_code)` —— 强制先写测试
- `self_rollback(steps)` —— git revert 撤销
- `self_diff(last_n)` —— 看 commit 历史

每次自修改前自动 `git commit` 当前状态作为安全点。所有改动 7 天内可回滚。

**可改路径**：`tools/` / `ai_agent/` / `prompts/` / `templates/` / `static/` + 根入口文件
**不可改**：`.env` / `requirements.txt` / `.gitignore` / `.git/`

---

## 复杂任务三段式

复杂任务（UI 改动 / 跨多文件 / 改 prompts 等）AI 必须走：

1. **plan_task** —— 列 ≥2 条实现路径 + 选哪条 + 验证方法
2. **verify_change** —— 改完断言关键 pattern 在/不在
3. **write_postmortem** —— 任务结束写复盘，下次同对话自动 inject 到 system prompt

让 AI 真正"学到"而不只是"做了"。

---

## 项目结构

```
.
├── launcher.py          桌面应用入口（pywebview + 系统托盘）
├── server.py            FastAPI 后端 + SSE 流式聊天
├── agent.py             Agent 工厂 + system prompt 拼装
├── memory.py            长期记忆（ChromaDB）
├── paths.py             路径常量（含 frozen 模式适配）
├── audit.py             工具调用审计
├── backups.py           定期全量备份
│
├── ai_agent/            自建 Agent 框架（取代 LangChain）
│   ├── messages.py      Message / ToolCall（含 reasoning_content）
│   ├── tools.py         @tool 装饰器 + 注册表
│   ├── llm.py           DeepSeek 流式客户端（MiMo 兼容类保留）
│   ├── loop.py          ReAct loop（max 60 轮，撞墙断点续传）
│   └── persist.py       JSONCheckpoint
│
├── tools/               业务工具（50+ 个 @tool）
│   ├── basic.py         计算 / 时间 / 简单搜索
│   ├── execute.py       Python 沙箱
│   ├── shell.py         系统命令白名单（支持 cwd 子目录）
│   ├── files.py         文件读写编辑（含行级切片 offset/limit）
│   ├── coding.py        代码质量门禁（lint / format_code / run_tests
│   │                    / apply_patch / find_references / smoke_test）
│   ├── code_indexer.py  tree-sitter 符号索引（code_search / outline
│   │                    / references / dependencies，比 grep 准）
│   ├── memory_tools.py  长期记忆 CRUD
│   ├── dialog.py        ask_user 弹窗 + audit_stats 自查
│   ├── vision.py        视觉路由链（GLM / Qwen）
│   ├── self_edit.py     自我修改（含每 20 次 commit 后自查提示）
│   ├── plan.py          复杂任务规划
│   ├── verify.py        变更断言
│   ├── postmortem.py    任务复盘
│   ├── spawn_sub.py     主对话开子对话
│   ├── sub_complete.py  子对话结束给主对话留摘要
│   ├── ui_check.py      UI 自检（截图 + 视觉描述）
│   ├── skills.py        持久化技能（define_skill）
│   ├── todo.py          任务清单
│   ├── venv_install.py  装包到工作区 .venv
│   ├── search.py        统一搜索（内部 _search/ 多引擎并发 + 去重降级）
│   └── changelog.py     git log 重建 CHANGELOG.md
│
├── prompts/
│   ├── yuki.md          角色卡（默认有希，改这个换角色）
│   └── system.md        系统指令（工作方法 + 工具说明）
│
├── templates/
│   └── index.html       前端（Tailwind + Alpine.js，无构建工具）
├── static/
│   └── style.css        雪夜主题 + 春樱柔光白天主题
├── assets/
│   ├── icon.png / .ico  应用图标
│   ├── background.png   夜晚主题背景
│   ├── day_bg.png       白天主题背景
│   └── ...
│
├── skills/              持久化技能（可选示例）
├── models/              本地 embedding 模型（用户下载）
│
├── yuki.spec            PyInstaller 打包配置
├── build.bat            Windows 一键打包
├── build.sh             Mac/Linux 一键打包
│
└── DESKTOP_BUILD.md     桌面化方案详细说明
```

---

## 已知限制

- **Windows 7 不支持** —— pywebview 需要 WebView2（Win10 1809+ 自带）
- **Mac 上 pywebview + pystray 都要主线程** —— 托盘和窗口可能无法同时存在
- **首次启动较慢** —— bge-base-zh-v1.5 模型加载约 3-5 秒（之后秒级）
- **打包后 AI 无法修改自己的 .py 代码** —— `--onefile` / `--onedir` 模式 .py 都嵌在 exe 内部（`prompts/` 例外，会解压到 exe 旁可改）

---

## 贡献

欢迎 PR。原则：

- 不引入 LangChain / LangGraph（已脱离，保持自建框架）
- 不破坏自我进化机制（每次自修改都要能 git revert）
- 改 prompts/system.md 前先看现有"决策思维"章节，避免重复

---

## 许可

MIT License - 见 [LICENSE](LICENSE)。

---

## 致谢

- 角色灵感：长门有希（《凉宫春日》系列，谷川流著）
- 模型：DeepSeek（对话）+ GLM / Qwen（视觉）
- Embedding：[BAAI/bge-base-zh-v1.5](https://huggingface.co/BAAI/bge-base-zh-v1.5)
