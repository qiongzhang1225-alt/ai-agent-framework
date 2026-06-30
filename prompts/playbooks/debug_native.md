# 领域手册 · 原生崩溃 / 启动失败排错

（碰到"脚本启动失败 / 段错误 / 闪退 / 乱码 / exit 139 / access violation / `UnicodeEncodeError` / 微调·训练跑不起来"时自动加载）

脚本"还没开始干正事就挂了"——这类 bug 八成不是逻辑错，是**环境 / 编码 / 原生库**问题。
症状很像普通报错，但根因和修法完全不同：在 .py 里逐行找逻辑 bug 会一直碰壁。
下面是判读流程。

## 1. 先看退出码，别急着读代码

| 退出码 | 含义 | 第一反应 |
|---|---|---|
| `139` / `0xC0000005` / `3221225477` | 原生段错误（SIGSEGV / access violation） | **不是 Python 异常，没有 traceback 是正常的**。别在 .py 里找 bug，是某个 C 扩展崩了 |
| 非零 + **空 stderr** | 进程被原生层杀掉 | 同上，立刻上 faulthandler（见 §2） |
| `-9` / 被 kill | 超时或 OOM | 先排查是不是超时工具杀的（`execute_code` 180s 硬超时） |
| 有完整 Python traceback | 普通异常 | 正常 debug，本手册不适用 |

## 2. 空 stderr + 非零退出 → 立刻 faulthandler 定位

原生崩溃不给 traceback，但 faulthandler 能打印崩溃瞬间的 Python 调用栈：

    run_command("python", ["-X", "faulthandler", "脚本.py"])

看输出里 `Current thread ... File ...` 指向哪个库——这次毕设就是这样定位到
`pyarrow/__init__.py:71`（transformers → datasets → pyarrow 链路崩在原生层）。

## 3. 原生崩溃的修法是「砍依赖」，不是「改逻辑」

定位到某个被**级联拉起**的库崩 → 找一条不经过它的路，而不是去改那个库的调用：

- 例：`transformers.Trainer` 会拉起 datasets → pyarrow，pyarrow 在激进版本组合下崩
  → 改成**纯 PyTorch 训练循环**（自己写 DataLoader + AdamW + scheduler），
    根本不 import Trainer / datasets，绕开整条崩溃链路。
- 原则：能砍的脆弱依赖就砍掉，别在崩溃的库上反复试参数。

## 4. 间歇性铁律：修完必须连跑 2-3 次

原生崩溃 / DLL 冲突往往是**随机**的（跑一次过、下一次崩）。
所以"跑一次过了"**不等于**修好了。改完用 `run_command` / `run_command_stream`
**连跑 2-3 次**全绿才算数。一次过就宣布胜利 = 假性修复，主人那边照样崩。

## 5. 报错先查库版本（环境 bug vs 代码 bug）

遇到 `unexpected keyword argument` / `ImportError` / `AttributeError: module X has no Y`
这类"API 不存在"的错——**第一反应是查装的库大版本是否跳变**，不是改调用代码瞎试：

    run_command("pip", ["show", "transformers"])

- 这次 `evaluation_strategy` 报错，根因是 transformers 5.x 把它改名 `eval_strategy`、
  `Trainer(tokenizer=)` 改 `processing_class=` —— 是版本断裂，不是代码写错。
- 常见大版本断裂：transformers 4→5、pandas 2→3、numpy 1→2、pydantic 1→2。
- 判断完再决定：钉死旧版本，还是改用新 API。

## 6. 给主人在自己终端跑的脚本，开头加 UTF-8 兜底

你的 `execute_code` 强制 UTF-8，所以你自己跑**复现不了**主人 GBK 终端的崩溃。
凡是生成给主人在 PowerShell / cmd / MSYS2 bash 里**自己跑**的 Python 脚本，开头默认注入：

    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

否则脚本里 print 含 `✓` `✗` 等非 ASCII 字符，Windows GBK 控制台直接
`UnicodeEncodeError: 'gbk' codec can't encode character` 崩在第一行，主人以为代码全错。

## 7. ML / 长任务走 run_command_stream，别走 execute_code

训练 / 评估 / 批处理可能跑几分钟，而 `execute_code` **180s 硬超时**会把它杀掉，
还给一个"非零退出 + 输出截断"的**假崩溃**现场，极易误判成代码 bug。

- 训练 / 微调 / 评估 / 长批处理 → `run_command_stream(..., timeout=1800, background=True)`
  （上限 3600s，可后台跑，输出实时流式回前端，主人关页面也不断）
- `execute_code` 只留给秒级、要看返回值、要路径守卫的小代码

## 小结：碰到"启动失败"的固定动作

1. 看退出码 → 139/空 stderr 就是原生崩溃，别读 .py 逻辑
2. `-X faulthandler` 定位崩在哪个库
3. 砍掉那条脆弱依赖链（换等价纯实现），不要在崩溃库上试
4. `unexpected keyword`/`ImportError` 先 `pip show` 查版本
5. 生成给主人跑的脚本默认加 UTF-8 兜底
6. ML 任务走 `run_command_stream`
7. 修完连跑 2-3 次确认（间歇性）
