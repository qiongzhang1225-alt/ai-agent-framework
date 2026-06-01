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

修改后重启 server 生效。不需要训练模型。

---

## 内置能力

- SSE 流式对话（可中断）
- 文件读写（自动 .bak 备份）
- 系统命令（白名单制）
- 长期记忆（ChromaDB，跨对话持久）
- 代码感知（tree-sitter 索引，搜索/引用/调用图）
- 后台任务（关页面不断连）
- 读图（MiMo 视觉模型）
- 黑夜/白天主题切换

---

## 内置技能

skills/ 目录下包含开箱即用的技能：

| 技能 | 功能 |
|------|------|
| get_weather | 查询当前天气 |
| weather_forecast | 未来几天天气预报 |
| smart_search | 智能搜索 |
| fetch_multiple | 批量拓取网页 |
| github_search | 搜索 GitHub |
| stackoverflow_search | 搜索 Stack Overflow |
| skill_health_check | 技能健康检查 |

---

## 自定义工具

tools/ 下新建 .py 文件：

```python
from ai_agent import tool

@tool
def hello(name: str) -> str:
    return f"你好, {name}!"
```

重启后 AI 自动发现并可使用。

---

## 项目结构

server.py        # 后端
agent.py         # 入口
memory.py        # 长期记忆
ai_agent/        # Agent 引擎
tools/           # 工具
skills/          # 技能（也是 @tool）
prompts/
  yuki.md        <-- 你的角色卡
  system.md      # 系统指令
templates/       # 前端
static/          # CSS
assets/          # 头像/背景图
