# 领域手册 · Unity 操控

（碰到 Unity / 场景 / GameObject / Prefab / C# / `mcp_unity_*` 工具时自动加载）

主人开了 Unity 项目想让你操控时，你有两类工具：

**A. 项目级（yuki 内置，中文 description）—— 接入 + 探测，给"哪个项目"用：**

| 工具 | 何时用 |
|---|---|
| `detect_current_unity_project()` | 主人说"我现在开的 Unity 项目" / "当前项目" → 先调它拿路径，**别每次问主人路径** |
| `list_unity_projects()` | "我有哪些 Unity 项目" / "哪些接了 MCP" → 扫盘列出，标 `[mcp]` 已接入 |
| `setup_unity_mcp_bridge(path)` | "把 X 项目接入 MCP" / "新建项目想让你连" → 改 manifest.json，让主人 Start Session |
| `remove_unity_mcp_bridge(path)` | 反向，主人想关掉 |
| `mcp_reload()` | unity 工具突然失败 / Unity Editor 关了再开 / 状态小灯变红 → 先调它重连，再重启 yuki |

**B. 场景级（42 个 mcp_unity_* 工具，description 是"中文摘要 + 原英文"，按 `【类别】` 选）：**

- `mcp_unity_manage_scene` 【场景】CRUD
- `mcp_unity_manage_gameobject` 【GameObject】CRUD
- `mcp_unity_manage_components` 【组件】CRUD
- `mcp_unity_manage_prefabs` 【Prefab】CRUD
- `mcp_unity_manage_script` / `apply_text_edits` / `script_apply_edits` 【脚本】
- `mcp_unity_read_console` 【Console】读日志（**Unity 没反应先调它看错误，不要瞎猜**）
- `mcp_unity_manage_editor` 【Editor】Play/Pause/Undo
- 其它按 description 里的 `【类别】` 前缀挑

**典型工作流：**

1. **首次接入新 Unity 项目**：
   ```
   detect_current_unity_project()    # 拿当前打开的 Unity 项目路径
   → setup_unity_mcp_bridge(path)    # 写 manifest.json
   → 告诉主人去 Unity Start Session
   → mcp_reload()                    # 主人 Start 后，主动重连让工具上线
   → mcp_unity_manage_scene(get_active)  # 验证连上
   ```

2. **改场景里的东西**：
   ```
   mcp_unity_manage_scene(get_hierarchy)        # 先看场景结构
   → mcp_unity_find_gameobjects(...)            # 定位目标对象
   → mcp_unity_manage_gameobject(...)           # 改属性
   ```

3. **写 / 改脚本**：
   ```
   mcp_unity_validate_script(...)        # 先校验语法（不直接落文件）
   → mcp_unity_create_script(...) / apply_text_edits(...)  # 通过校验再写
   → mcp_unity_read_console()            # Unity 编译完看 Console 有无报错
   ```

4. **跑游戏看实际效果**（主人说"测试一下"、"跑起来看看"）：
   ```
   manage_editor(action="play")               # 进 Play 模式
   → execute_code(code="System.Threading.Thread.Sleep(2000);")  # 等游戏跑 2 秒
   → manage_camera(action="screenshot", include_image=True, max_resolution=800)
   → vision_describe(image_ref="Assets/Screenshots/xxx.png", question="飞机在天上吗？UI 正常吗？")
   → read_console(count=20, types=["log","warning","error"])     # 看脚本日志/报错
   → manage_gameobject(action="get_info", target="Airplane")     # 查关键对象 runtime 状态
   → manage_editor(action="stop")             # 退出 Play 模式
   → 汇报观察（视觉 + 日志 + 状态三方对照）
   ```
   **能干**：验证场景不崩、关键对象位置/状态符合预期、视觉看起来对。
   **不能干**：模拟玩家键鼠操作（MCP 没"按 WASD"工具）。要测"按一下空格跳"
   → 用 `execute_code` 直接调脚本的对应方法（绕过输入层），或者用 `run_tests`
   跑预先写好的 PlayMode 测试脚本。

5. **Unity 工具突然全失败**：
   先调 `mcp_reload()`。还不行 → 看是不是主人关了 Unity Editor / 没 Start Session。
   最后才考虑让主人重启 yuki。

**警示：**

- `mcp_unity_execute_code` 能在 Unity 里跑任意 C#，等同 IDE Immediate Window —— 写之前**先在心里跑一遍**，会乱改场景的话先告知主人
- `mcp_unity_manage_build` 会触发构建生成 GB 级 artifacts，确认主人意图再调
- Unity 在 Play 模式时部分工具被禁，遇到 "operation forbidden in Play Mode" 类报错先 `manage_editor(stop)`
