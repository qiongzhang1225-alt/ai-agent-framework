# 有希 桌面化方案（pywebview + 系统托盘 + 单 exe）

把"打开浏览器 → 输入 localhost:3616"换成"双击图标 → 独立窗口"，跨 Windows/Mac/Linux。

---

## 架构

```
yuki.exe (PyInstaller 单文件)
├─ Python 解释器（嵌入式）
├─ launcher.py 主入口
├─ server.py + agent.py + tools/ ... 全部代码
├─ templates/ + static/             （只读资源，从 sys._MEIPASS 加载）
└─ prompts/ + assets/               （首次启动解压到 exe 旁，可读可改）

启动后 exe 旁会生成:
├─ prompts/        ← 首次解压（yuki 可改）
├─ assets/         ← 首次解压（图标 / 背景图）
├─ .sandbox/       ← 对话历史 + 工作目录
├─ .memory/        ← ChromaDB 长期记忆
├─ skills/         ← 持久化技能
├─ .yuki.lock      ← 单实例锁（运行中存在，退出删除）
└─ .env            ← API key 配置（用户手动放置）
```

### 启动流程

1. **单实例锁**：`.yuki.lock` 已存在且 PID 还活着 → 弹"已运行"对话框退出
2. **种子化**：打包模式首次启动 → 把 `prompts/` `assets/` 从 bundle 解压到 exe 旁
3. **端口选择**：默认 3616，被占就 3617/3618 往后找
4. **后台启动 uvicorn**：子线程跑 server，主线程探活等就绪
5. **创建窗口**：pywebview 装载 `http://127.0.0.1:<port>`
6. **系统托盘**：pystray 子线程，菜单 [显示窗口] / [退出]
7. **关窗钩子**：默认 → 隐藏到托盘；托盘"退出" → 真退出 + 释放锁

---

## 源码模式运行（开发 / 调试）

```bash
# 已建好 .venv 且装好依赖（含新增的 pywebview / pystray）
.venv\Scripts\python.exe launcher.py        # Windows
.venv/bin/python launcher.py                # Mac / Linux
```

跟原本 `python server.py + 浏览器手动打开` 等价，区别只是：
- 用 pywebview 开窗口而非浏览器
- 多了系统托盘 + 单实例锁

---

## 打包成单 exe

### Windows

```cmd
build.bat
```

产出 `dist\yuki.exe`（~80-120 MB，含 Python 解释器 + 所有依赖）。

### macOS

```bash
./build.sh
```

产出 `dist/yuki.app`（拖到 Applications/ 即可）。

> Mac 图标：把 `assets/icon.icns` 放进去会自动用。生成方法：
> ```bash
> mkdir icon.iconset && sips -z 256 256 assets/icon.png --out icon.iconset/icon_256x256.png \
>   && iconutil -c icns icon.iconset && mv icon.icns assets/
> ```

### Linux

```bash
./build.sh
```

产出 `dist/yuki`（单二进制）。

> ⚠️ Linux 需先装系统 webview 依赖：
> - Ubuntu/Debian: `sudo apt install python3-gi gir1.2-webkit2-4.0`
> - Fedora: `sudo dnf install python3-webkitgtk4.0`

---

## 跨平台注意

| 平台 | webview 后端 | 托盘后端 | 单实例锁机制 |
|---|---|---|---|
| Windows 10+ | WebView2（Edge 内核） | pystray win32 | OpenProcess pid 探活 |
| Windows 7 | 不支持（WebView2 要 10+） | — | — |
| macOS 10.13+ | WKWebView | pystray cocoa | os.kill(pid, 0) |
| Linux | WebKitGTK | pystray xorg / appindicator | os.kill(pid, 0) |

### 已知坑

1. **pywebview macOS 必须主线程跑** — launcher 已经把 webview.start() 放主线程
2. **pystray macOS 也要主线程** — 跟 pywebview 冲突，Mac 上托盘可能无法跟窗口同时存在。
   已知问题，临时方案：Mac 用户可去掉托盘（编辑 `launcher.py` 注释 `setup_tray` 一行）
3. **Linux 上 pywebview 默认 GTK 后端** — 缺 GTK 时报错，去 launcher 的 `webview.start()` 加 `gui="qt"` 切 Qt 后端（需要装 PyQt5）

### 体积优化（可选）

PyInstaller 打出来的 exe 大头是 Python 标准库 + chromadb + sentence-transformers。
- chromadb / sentence-transformers 是长期记忆必需，不能砍
- 去掉 `tkinter` / `matplotlib.tests` 等已经在 yuki.spec 的 `excludes` 里
- 用 `--upx-dir` 配合 UPX 压缩能压一半但启动慢；默认未开启

---

## 自我修改在打包模式下的限制

⚠️ **打包后 yuki 调 self_edit 改 tools/ai_agent/agent.py 等代码 _不会_ 生效**。
   - 代码嵌入在 exe 内部，PyInstaller 临时目录的修改重启就丢失
   - **prompts/ 例外**：launcher 已把 prompts 解压到 exe 旁，yuki 改 `yuki.md` / `core.md` 会持久化

如果你需要"yuki 边用边改自己代码"的能力：
- 用源码模式跑（`python launcher.py`）
- 或者发布 `--onedir` 而非 `--onefile`（修改 yuki.spec）

---

## 卸载 / 清理

打包模式没有"安装程序"，直接删 `yuki.exe` 及其旁边的目录即可。
源码模式 `git pull && pip install -r requirements.txt` 更新。

---

## 故障排查

### 双击 exe 没反应

1. 任务管理器看有没有 yuki.exe 进程（可能在跑但没窗口）
2. exe 旁是否生成了 `.yuki.lock` —— 如果有但你确定没在跑：手动删掉
3. exe 旁建个 `debug.bat` 运行 `yuki.exe`，看控制台报错

### 窗口空白 / 加载失败

- 浏览器打开 `http://127.0.0.1:3616` 看 server 是否真起来了
- 看 exe 旁的 `.sandbox/_meta/` 是否被创建（server 启动成功的标志）

### 托盘图标点没反应

- pystray Windows 偶发不响应；右键退出再重启
- 永久解决：从 launcher.py 删除托盘，关窗 = 退出

### 端口冲突

launcher 会自动 3616 → 3617 → 3618 找端口。窗口标题栏不会显示端口，但 `http://127.0.0.1:<port>` 可在浏览器另开一份用同样数据。
