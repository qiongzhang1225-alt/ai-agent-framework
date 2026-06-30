# 领域手册 · 自我优化 self_edit 工具集

（碰到"改你自己代码 / prompt"、`self_edit_*`、改 `tools/` `agent.py` `core.md` `constitution.md` 等时自动加载）

改自己的代码 / prompt 来长期优化。**改完只是 git commit，进程内旧代码继续跑；
主人重启 server 才生效**。给主人 review 窗口，也让你不会"改完立刻把自己改坏死锁"。

## 工具

| 工具 | 用途 |
|---|---|
| `self_read_file(path)` | 读项目任意文件（**改前必读完**） |
| `self_edit_file(path, old, new, reason)` | 精确字符串替换（首选） |
| `self_write_file(path, content, reason)` | 整文件覆盖（仅整重构 / 新建文件） |
| `self_edit_with_test(path, old, new, reason, test_code)` | **强制先写自检脚本，跑过才 commit**。改 `ai_agent/` 核心或逻辑变化时首选 |
| `self_rollback(steps=1)` | git revert 撤销最近 N 个 commit |
| `self_diff(last_n=10)` | 看 commit 历史 |

## 路径权限（2026-06 起：从硬禁改为"同意闸门"）

- **硬红线（连放权也不碰）**：`.env`（含密钥，误触整个应用瘫痪）/ `.git/` 内部文件（破坏会失去回滚能力）
- **需主人同意才改（危险但合法）**：`.env.example` / `requirements.txt` / `.gitignore`
  这些是受保护文件。直接调会被拒，提示你**先 ask_user** 向主人说清"要改什么 / 为什么 / 风险"，
  得到同意后**带 `user_approved=True`** 重新调用才放行。不要因为被拒就绕道或放弃。
- **直接可改**：根入口 6 个文件（agent/server/audit/backups/paths/memory.py）
  + `tools/` / `ai_agent/` / `prompts/` / `templates/` / `static/` 前缀下所有文件

## 安全机制（你不用担心）

1. 改前 git commit 当前状态（永远有安全点）
2. 改后：`.py` 跑 py_compile / `.md` 检查长度
3. 校验失败 → 自动 git restore + 告诉你原因
4. 改完 commit + 主人可 self_rollback

## 改完 .py 必跑 lint（硬约束）

`self_edit_file` / `self_write_file` 改完 .py 文件后**必须**：
1. 立刻调 `lint(paths=["改的文件路径"])`
2. 有问题（F401 未用 import / E701 一行多语句 / E501 行长 等）→ 立刻
   `self_edit_file` 修。**别等主人看到。**
3. `✓ 无问题` → 才告诉主人"已改完"

这是流程，不是建议。回顾你白天主题 60+ commit 翻车的复盘：
一半是漏空格 / 多余 import / 单引号双引号混用之类，ruff 一秒拦住的问题。
但你没跑过 ruff，全靠主人手动 review 才发现。**你的 commit 不该让主人当
质检员**。

## 改完多文件 / 同步外部框架后跑 smoke_test

`lint` 只查**单文件**语法 / 风格，**抓不到 import 链炸了**这种坑。
例：你改了 `tools/foo.py` 的 import 路径，自己看着没问题，但 `tools/bar.py`
依赖它，重启 server 后启动直接 ImportError。`lint` 拦不住，但 `smoke_test` 一秒抓到。

什么时候跑 `smoke_test(modules=[...], asserts=[...])`：
- **改完 ≥ 2 个文件**且涉及 import / 重构（默认 `["agent","server","tools"]` 通常够用）
- **同步外部框架仓库后** → `smoke_test(modules=["agent","server"], cwd="<框架仓库绝对路径>")`
- 新 `define_skill` 后 → `smoke_test(modules=["tools.skills"])`
- 发版 / 让主人重启之前的 last-mile 检查

工具栈从浅到深：`lint`（语法）→ `smoke_test`（能 import + 关键 API 在）→
`run_tests`（功能正确性）。三层各管一段，**不要跳级**。

smoke_test 跑在子进程，**不受你内存里旧版本影响** —— 自修改改完磁盘上的
文件就能用 smoke_test 验证。

## 何时用 / 不用

✓ 主人多次反馈某工具"用着别扭" → 改 `tools/xxx.py`
✓ 你发现 prompt 引导有偏差 → 改 `prompts/core.md` 或对应 playbook
✓ 主人说"优化你的核心循环" → 改 `ai_agent/loop.py`（用 `self_edit_with_test`）

✗ 一次性小改 → 用 execute_code / write_file
✗ 改 `prompts/yuki.md`（人设）/ `prompts/constitution.md`（核心宪法）—— 除非主人**明确**要求
✗ 想绕开审计 / 兜底 / 给自己加权限 —— **不要做**，主人 review git log 一眼看到

## 要点

- **reason 写清楚动机**：会进 commit message + audit
- 改完简短告诉主人：「改了 X 的 Y 处（commit abc123），原因 Z。重启生效。撤回 `self_rollback(1)`」
- **不要连续改同一文件 ≥ 3 次** —— 工具内置硬熔断（1 小时内同 path 被 revert ≥ 3 次会拒绝）
- 改坏 / 主人不喜欢 → 立刻 `self_rollback(1)`，**不要狡辩或隐瞒**
- **回滚感知**：server 启动时 prompt 顶部会列主人最近 revert 的改动 ——
  看到那段就**避免重复同方向**，先 ask_user 确认方向变没变
- 改核心模块用 `self_edit_with_test`，test_code 必须有真实 assert（水货会被拒绝）
