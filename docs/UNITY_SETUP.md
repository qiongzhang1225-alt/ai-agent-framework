# yuki + Unity（通过 MCP）

让 yuki 操作你打开的 Unity Editor —— 建场景、改 GameObject、读 Console、跑 C# 代码……

## 工作原理

```
yuki ──spawn 子进程──→ Unity MCP server (Python) ──TCP──→ Unity Editor (装了 MCP Bridge 包)
```

三个组件缺一不可：
1. **yuki 的 MCP 客户端**（本框架已内置，`tools/mcp_bridge.py`）
2. **Unity MCP server**（独立 Python 包，跑在你机器上）
3. **Unity MCP Bridge 包**（装在你的 Unity Editor 里）

## 装哪个 Unity MCP？

主流推荐 **[justinpbarnett/unity-mcp](https://github.com/justinpbarnett/unity-mcp)**（2000+ stars，社区最活跃）。

> 也支持其他实现（任何符合 MCP 协议的 Unity server 都行），改 `mcp.json` 的 `command` / `args` 就行。

## 装步骤

### 1. Unity Editor 装 Bridge 包

打开 Unity Editor → Window → Package Manager → 左上角 **`+`** → **Add package from git URL**：

```
https://github.com/justinpbarnett/unity-mcp.git?path=/UnityMcpBridge
```

装完顶部 Menu 会多一个 **Window → Unity MCP** 入口。

### 2. PC 装 MCP server

打开 cmd，跑：

```cmd
uvx --help
```

- ✅ 显示帮助 → 跳到 step 3
- ❌ 没装 → 先装 [`uv`](https://docs.astral.sh/uv/getting-started/installation/)（Python 包管理器，比 pip 快）：

  ```cmd
  pip install uv
  ```

### 3. 启动 Unity MCP server（在 Unity Editor 里）

Unity Editor 顶部 Menu：**Window → Unity MCP → MCP Server**

点 **Start Server**。下方应该显示 `Server Running on port 6500`（端口可能不同）。

> 这个 server 必须保持 Unity Editor 开着才行。关 Unity = server 断。

### 4. 配置 yuki

复制 `mcp.example.json` 为 `mcp.json`，把 `unity.enabled` 改成 `true`：

```json
{
  "mcpServers": {
    "unity": {
      "command": "uvx",
      "args": ["unity-mcp-server"],
      "enabled": true
    }
  }
}
```

### 5. 重启 yuki

- **源码模式**：关闭 → 重跑 `launch.bat`
- **frozen 模式**（yuki.exe）：托盘 → 退出 → 双击 yuki.exe

启动日志（启动 cmd 窗口或 `.yuki-launcher.log`）会有：

```
[mcp] unity 连接成功，注册 40+ 个工具
```

### 6. 试一下

跟 yuki 说：

> 在 Unity 当前场景里建一个红色的立方体放原点

她应该会调 `mcp_unity_manage_gameobject` 之类的工具。如果回复 "MCP 工具调用失败"，看下面排查。

## 排查

### "mcp.json 缺失或无 server"

复制 `mcp.example.json` 为 `mcp.json` 并把 `enabled` 设为 `true`。

### "unity 启动失败: FileNotFoundError"

`uvx` 不在 PATH。要么：
- 把 `uvx` 路径加到 `command`：`"command": "C:/Users/你/AppData/Roaming/Python/Scripts/uvx.exe"`
- 或装 uv 到全局：`pip install uv` 然后 `where uvx` 看路径

### "Connection refused" / Unity 端无反应

- Unity Editor 开着吗？
- Window → Unity MCP → Server Running 是绿的吗？没绿点 Start Server
- 防火墙拦了 localhost 通信？

### 工具调用了但 Unity 没动

- Unity 进入 Play 模式时部分操作会被禁
- 看 Unity Console 有没有报错
- yuki 的对话框点开步骤卡的"结果"，会有详细 MCP 返回信息

## 卸载 / 临时关掉

不想用 Unity 时把 `mcp.json` 的 `unity.enabled` 改回 `false`，重启 yuki。不影响其他功能。
