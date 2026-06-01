# AI Agent Framework

一个可自定义角色和能力的 AI 助手框架。

---

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入 API Key
python server.py
```

---

## 设定角色卡

编辑 prompts/yuki.md，这个文件会被注入 AI 的 system prompt。

建议包含：名称、身份、性格、说话风格、规则。

示例：

```markdown
**名称**: 小助手
**身份**: 经验丰富的编程助手
**性格**: 冷静、逻辑清晰、不废话
**说话风格**: 简洁，用中文，称呼用户"你"
**规则**: 不确定就说"不确定"，不编造
```

修改后重启 server 生效。不需要训练模型。

---

## 内置能力

- SSE 流式对话（可中断）
- 文件读写（自动 .bak 备份）
- 系统命令（白名单制）
- 长期记忆（ChromaDB，跨对话）
- 代码感知（tree-sitter 精确索引）
- 后台任务（关页面不断连）
- 读图（MiMo 视觉模型）
- 黑夜/白天主题

---

## 自定义工具

tools/ 下新建 .py 文件：

```python
from ai_agent import tool

@tool
def hello(name: str) -> str:
    return f"你好, {name}!"
```

重启后 AI 自动发现。

---

## 项目结构

```
server.py        # 后端
agent.py         # 入口
memory.py        # 长期记忆
ai_agent/        # Agent 引擎
tools/           # 工具
prompts/
  yuki.md        <-- 你的角色卡
  system.md      # 系统指令
templates/       # 前端
static/          # CSS
assets/          # 头像/背景
```
