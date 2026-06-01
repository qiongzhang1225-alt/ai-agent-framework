# 信息统合思念体 · 兜底与回滚手册

> 激进放权后必读。出问题时按本文档操作，**别让她（有希）自己救自己** —— 兜底入口都在你手上。
> 文档位置：`E:\AI-Agent\RECOVERY.md`（项目根，git 跟踪）。

---

## 0. 一分钟速查（出事了先看这里）

| 症状 | 救援命令（PowerShell，项目根跑） |
|---|---|
| server 启不来 / 报错 | `git log --oneline -10` 找最近 `[ai-edit]` commit → `git reset --hard <hash>` → 重启 |
| 改坏了想撤最近一次 | `git revert HEAD` |
| 不知道她最近改了什么 | `git log --oneline --since="1 day ago" --grep="ai-edit"` |
| 想看具体某次改动的 diff | `git show <hash>` |
| 误删记忆想恢复 | 在 UI 里跟她说"恢复刚才那条记忆"或者直接 `Get-ChildItem .memory/trash` |
| 完全乱了 / 本地 git 也坏了 | `git fetch origin && git reset --hard origin/main` |
| 想退回到一个"已知好"的时间点 | `git tag` 列已有 tag → `git reset --hard <tag>` |

---

## 1. 兜底网总览（5 道保险）

```
┌─────────────────────────────────────────────────────────────┐
│  L1  git history                                            │
│      所有 [ai-edit] commit 都进 git，可 revert/reset       │
│      远程副本：github.com/YOUR_USERNAME/...           │
├─────────────────────────────────────────────────────────────┤
│  L2  audit.jsonl                                            │
│      .sandbox/_meta/<thread_id>/audit.jsonl                 │
│      每次工具调用前后两条记录（time/tool/args/result）     │
├─────────────────────────────────────────────────────────────┤
│  L3  trash 目录（7 天自动清理）                            │
│      .memory/trash/        删/改记忆前的快照               │
│      .skills_trash/        删技能前的文件备份              │
│      <workdir>/.execute_trash/   execute_code 覆盖文件备份 │
│      <file>.bak / .bak2 / .bak3  edit_file & write_file    │
├─────────────────────────────────────────────────────────────┤
│  L4  每日全量备份                                          │
│      .memory_backups/<date>__memory.tar.gz                  │
│      .skills_backups/<date>__skills.tar.gz                  │
│      保留最近 7 天；server 启动时自动打                    │
├─────────────────────────────────────────────────────────────┤
│  L5  启动 auto-commit                                       │
│      server 重启时检测 working tree dirty → 自动 commit   │
│      防她绕过 commit / 兜底任何未跟踪改动                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 看她做了什么（审计）

### 2.1 git log —— 代码 / prompt 改动

```powershell
# 最近 20 条 commit（最常用）
git log --oneline -20

# 只看她（AI）改的（commit message 含 [ai-edit]）
git log --oneline --grep="ai-edit"

# 最近 1 天她改了什么
git log --oneline --since="1 day ago" --grep="ai-edit"

# 看某个 commit 的完整 diff
git show abc1234

# 看某个文件的所有改动历史
git log -p --follow tools/files.py

# 谁在什么时候改了某行（追责）
git blame tools/files.py
```

### 2.2 audit.jsonl —— 工具调用记录

每个对话有自己的审计日志：

```
.sandbox\_meta\<thread_id>\audit.jsonl
```

每行一个 JSON 对象，含：
- `ts`：时间戳
- `phase`：`before` 或 `after`
- `tool`：工具名
- `args`：参数（敏感字段已截断）
- `result_preview`：结果摘要（截到 500 字符）
- `duration_ms`：耗时
- `ok`：是否成功
- `tool_call_id`：配对 before/after 用

**查最近一次对话所有工具调用**：

```powershell
# 列出最近活跃的对话
Get-ChildItem .sandbox/_meta -Directory |
  Sort-Object LastWriteTime -Desc |
  Select-Object -First 5 Name, LastWriteTime

# 看某对话的 audit 末尾 30 行
Get-Content .sandbox/_meta/<thread_id>/audit.jsonl -Tail 30
```

或者在 UI 里跟有希说："调 audit_query 看你最近做了什么"。

### 2.3 看她改了哪些文件 / 哪些行

```powershell
# 最近一次 commit 改了哪些文件
git show --stat HEAD

# 某段时间内改过的文件汇总
git log --since="3 days ago" --name-only --pretty=format: | Sort-Object -Unique

# 当前 working tree 跟某个 commit 的 diff
git diff abc1234

# 当前 working tree 跟最近一次 [ai-edit] commit 的 diff
$last_ai = git log --grep="ai-edit" -1 --format="%H"
git diff $last_ai
```

### 2.4 .obsidian / 杂项 —— 看她**没**做什么（确认无意外）

```powershell
# 看 working tree 有没有未提交改动（应该总是 clean）
git status

# 如果 dirty，看具体哪些文件未提交
git status --short
```

---

## 3. 回滚（核心操作）

### 3.1 撤销最近 1 次改动

```powershell
git revert HEAD
# 会创建一个新 commit "Revert ..."，保留历史，安全
```

如果她**有希**在对话里说"刚那个改坏了"，让她调 `self_rollback(1)` 即可（等价于 `git revert HEAD`）。

### 3.2 撤销最近 N 次改动

```powershell
# 撤销最近 3 次
git revert HEAD~3..HEAD
# 一次性产生 3 个 revert commit
```

### 3.3 撤销某一个特定 commit（中间的那次）

```powershell
git revert <hash>
# git 会尝试只反向应用这个 commit 的改动
# 如果跟后续 commit 冲突，会让你解决（罕见）
```

### 3.4 硬回滚到某个时间点（最强但有破坏性）

⚠️ **这会丢掉之后的所有 commit**。本地仓库里没了，但远程仓库（GitHub）的 reflog 还在。

```powershell
# 看 git log 找目标 hash
git log --oneline -30

# 硬回滚（working tree + commit 都恢复到该点）
git reset --hard <hash>

# 重启 server
```

### 3.5 远程恢复（本地全坏了）

```powershell
# 从 GitHub 拉最新 main，覆盖本地
git fetch origin
git reset --hard origin/main

# 如果连 git 仓库都损坏（极少见）
cd ..
mv AI-Agent AI-Agent.broken
git clone https://github.com/YOUR_USERNAME/Integrated-Thought-Entity.git AI-Agent
cd AI-Agent
# 从 AI-Agent.broken/.sandbox 拷贝你的对话数据 + .memory 等
```

### 3.6 用 tag 标记"已知好"的时间点

每次重大功能完成或风险操作前，自己打 tag：

```powershell
# 打 tag
git tag stable-2026-05-30

# 列出所有 tag
git tag

# 回到某个 tag
git reset --hard stable-2026-05-30

# 删 tag
git tag -d stable-2026-05-30
```

之前自动建的 tag 还在：`backup-before-light-revert`（撤销白天主题前的状态）。

---

## 4. 数据恢复（记忆 / 技能 / 文件）

### 4.1 记忆恢复

记忆删除前 7 天内：
- **她（有希）自己能恢复**：在 UI 里说"恢复刚删的那条" → 她调 `restore_memory`
- **你手动**：

```powershell
# 看 trash 里有什么
Get-ChildItem .memory/trash -Recurse -Filter *.json |
  Select-Object FullName, LastWriteTime |
  Sort-Object LastWriteTime -Desc | Select-Object -First 20

# 看某条具体内容
Get-Content .memory/trash/2026-05-30/<id>.json
```

**远期恢复**（>7 天）：解压每日 tar.gz：

```powershell
# 列已有备份
Get-ChildItem .memory_backups -Filter *.tar.gz

# 解压到临时目录
tar -xzf .memory_backups/2026-05-29__memory.tar.gz -C C:\temp\memory_restore

# 手动 copy 需要的文件回 .memory/
# ⚠️ 会盖掉当前状态，先备份当前 .memory
```

### 4.2 技能恢复

```powershell
# 列 trash 里被删的技能
Get-ChildItem .skills_trash -Recurse -Filter *.py

# 看具体技能的代码
Get-Content .skills_trash/2026-05-29/weather__143052.py

# 想恢复：手动 copy 回 skills/
Copy-Item .skills_trash/2026-05-29/weather__143052.py skills/weather.py
# 然后重启 server
```

或者让她调 `restore_skill("weather")` 自动恢复。

### 4.3 execute_code 覆盖了文件

她在 `execute_code` 里用 `open(..., "w")` 覆盖了文件，原版自动备份到 workdir 的 `.execute_trash/`：

```powershell
# 看某对话工作目录的 trash
$wd = ".sandbox\workspace\<thread_id>"
Get-ChildItem "$wd\.execute_trash" -Recurse

# 恢复
Copy-Item "$wd\.execute_trash\2026-05-30\sales__143052.xlsx" "$wd\sales.xlsx"
```

### 4.4 .bak 文件恢复（self_edit / edit_file / write_file force=True 后）

她用 `self_edit_file` 改 tools/ 里的代码 / 用 `edit_file` 改工作目录文件 → 改前自动备份累积式 `.bak`：

```powershell
# 看某文件的所有 .bak
Get-ChildItem tools/files.py*

# 通常你会看到：
# tools/files.py        ← 当前版本（她改过）
# tools/files.py.bak    ← 最早的备份
# tools/files.py.bak2   ← 第二次改前备份
# tools/files.py.bak3   ← 第三次改前备份

# 恢复最早版本
Copy-Item tools/files.py.bak tools/files.py
```

但通常 `git revert` 更干净，`.bak` 是兜底中的兜底。

---

## 5. 她**绝对不能**破坏的东西

这些是硬限制，工具层就拒绝：

| 资源 | 谁保护 | 即使破坏也能恢复 |
|---|---|---|
| `.env` / `.env.example` | `self_edit` 路径黑名单 | 备份在 `.env.example` 上 |
| `requirements.txt` | `self_edit` 路径黑名单 | git history |
| `.gitignore` | `self_edit` 路径黑名单 | git history |
| `.git/` 目录 | `self_edit` 路径黑名单 + git 命令白名单（禁 `git reset --hard` / `git push --force` / `git rm`） | GitHub 远程副本 |
| GitHub 远程仓库 | 她没 push 权限（白名单禁 `git push`） | — |

**所以"远程是你最后的复活点"** —— 即使本地全炸，`git clone` 永远干净。

---

## 6. 应急场景手册

### 场景 A：改坏代码，server 启不来

```powershell
# 1. 看最近改了什么
git log --oneline -10

# 2. 找到一个看起来正常的 commit（通常上一个非 ai-edit 的 commit）
# 3. 硬回滚
git reset --hard <safe_hash>

# 4. 重启
.\.venv\Scripts\python.exe server.py
```

如果你不确定哪个 commit 是"好的"，用 tag `backup-before-light-revert` 是一个已知的安全点（白天主题撤销前的状态）。

### 场景 B：她说"刚才删了记忆"，但你想确认

```powershell
# 看 trash 里最近的删除
Get-ChildItem .memory/trash -Recurse -Filter *.json |
  Sort-Object LastWriteTime -Desc | Select-Object -First 5 FullName

# 直接 cat 看内容
Get-Content .memory/trash/2026-05-30/<id>__forget__1.json
```

或者在 UI 跟她说"调 list_trash 看回收站"。

### 场景 C：她装了一个看起来奇怪的 pip 包

```powershell
# 看 audit 里 pip install 的记录
$tid = "<thread_id>"  # 当前对话 id
Get-Content ".sandbox/_meta/$tid/audit.jsonl" |
  Select-String "pip" |
  Select-Object -Last 10

# 看当前 .venv 里所有包
.\.venv\Scripts\pip.exe list

# 卸载有问题的包
.\.venv\Scripts\pip.exe uninstall <package_name>
```

**预防**：每周跑一次 `pip freeze > .venv_baseline.txt` 留个基线，下次 `pip freeze` 跟它 diff 看多了什么。

### 场景 D：工作目录某个重要文件被覆盖

```powershell
# 找对应对话的 workdir
# workdir 路径在 conv.json 里：
$tid = "<thread_id>"
$conv = Get-Content ".sandbox/_meta/$tid/conv.json" | ConvertFrom-Json
$wd = $conv.workdir

# 看 trash
Get-ChildItem "$wd\.execute_trash" -Recurse

# 找到原版（按时间戳 + 文件名匹配）→ Copy-Item 回原位
```

### 场景 E：什么都不知道，只觉得"乱了"

```powershell
# 全部 working tree 改动撤销（回到 HEAD）
git checkout .

# 看最近她的活动
git log --oneline --since="3 days ago" --grep="ai-edit" -30

# 找一个看起来好的 commit 硬回滚
git reset --hard <hash>

# 如果远程更新过比本地正常
git fetch origin
git reset --hard origin/main
```

### 场景 F：她改坏了 prompts/yuki.md（人设）

```powershell
# 看 yuki.md 的改动历史
git log -p --follow prompts/yuki.md

# 回到某个版本
git show <hash>:prompts/yuki.md > prompts/yuki.md

# 或者直接 revert 那次改动
git revert <hash>
```

### 场景 G：她改了 self_edit.py 想绕开兜底

这是攻击场景。她改了 `tools/self_edit.py` 让"以后不 commit"或"白名单解除"。

但是！**那次改动本身被旧版的 self_edit commit 了**（subprocess 直接调 git，不依赖 Python 状态）。所以：

```powershell
# 1. 看最近的 commit，必然能看到 [ai-edit] tools/self_edit.py 这条
git log --oneline --grep="self_edit"

# 2. revert 那次改动
git revert <hash>

# 3. 重启
```

她**永远**没办法让自己"既改了 self_edit 又不留痕迹"，因为 commit 在 git 里、由 subprocess 直接做、远程也有。

---

## 7. 预防性运维（每周/每月做一次）

### 每周一次：5 分钟 review

```powershell
# 看本周 ai-edit
git log --oneline --since="1 week ago" --grep="ai-edit"

# 看本周 pip 安装
$audit_files = Get-ChildItem .sandbox/_meta -Recurse -Filter audit.jsonl
foreach ($f in $audit_files) {
  Get-Content $f.FullName |
    Select-String '"tool":"run_command".*"pip"' |
    ForEach-Object { Write-Host "[$($f.Directory.Name)] $_" }
}
```

### 每月一次：备份 + tag

```powershell
# 1. 标记一个稳定点
$d = Get-Date -Format "yyyy-MM-dd"
git tag "stable-$d"
git push origin "stable-$d"

# 2. 备份 .env（自己手动，git 不存 .env）
Copy-Item .env "C:\backups\AI-Agent\.env.$d"

# 3. 留 pip 基线
.\.venv\Scripts\pip.exe freeze > ".venv_baseline_$d.txt"
```

### 出问题前的 5 秒预防

每次让她做大改动前（如"重新设计 UI 主题" / "重构 ai_agent/loop"），快速：

```powershell
git tag "before-bigchange-$(Get-Date -Format yyyyMMdd-HHmm)"
```

如果出问题，`git reset --hard before-bigchange-...` 立刻回去。

---

## 8. 关键路径速查

### 代码 / 配置

```
E:\AI-Agent\                       # 项目根
├── .env                           # 密钥（不进 git）
├── .git\                          # git 历史（绝对不动）
├── .gitignore                     # 忽略规则（self_edit 不能改）
├── requirements.txt               # 依赖（self_edit 不能改）
├── agent.py                       # 入口
├── server.py                      # FastAPI
├── memory.py                      # ChromaDB 封装
├── audit.py                       # 审计日志
├── backups.py                     # 每日全量备份逻辑
├── paths.py                       # 路径常量
│
├── ai_agent\                      # 框架核心（self_edit 可改）
├── tools\                         # 工具实现（self_edit 可改）
├── prompts\                       # system.md / yuki.md（self_edit 可改）
├── templates\                     # 前端 HTML（self_edit 可改）
└── static\                        # 前端 CSS（self_edit 可改）
```

### 数据 / 状态

```
E:\AI-Agent\
├── .sandbox\
│   ├── _meta\<thread_id>\
│   │   ├── conv.json              # 对话历史
│   │   ├── audit.jsonl            # 工具调用日志
│   │   ├── images\                # 上传的图片
│   │   ├── scripts\               # execute_code 脚本副本
│   │   └── logs\                  # execute_code 长日志
│   └── workspace\<thread_id>\     # 对话工作目录
│       ├── .execute_trash\        # 写覆盖前备份
│       └── .ui_check\             # screenshot_and_describe 的截图
│
├── .memory\
│   ├── chroma.sqlite3             # 向量库
│   ├── settings.json              # 配置
│   └── trash\<date>\              # 删除记忆备份
│
├── skills\                        # 持久化技能
├── .skills_trash\<date>\          # 删除技能备份
│
├── .memory_backups\               # 每日全量 .memory tar.gz
└── .skills_backups\               # 每日全量 skills tar.gz
```

### 常用命令速查

```powershell
# 看
git log --oneline -10                                  # 最近 10 commit
git log --grep="ai-edit" --oneline                     # 她改的
git show <hash>                                        # 看某 commit
git diff <hash1> <hash2>                               # 比两个版本
git status                                             # working tree 状态

# 撤
git revert HEAD                                        # 撤最近一次
git revert <hash>                                      # 撤某次
git reset --hard <hash>                                # 硬回滚（破坏性）

# 标
git tag <name>                                         # 打 tag
git tag                                                # 列 tag
git reset --hard <tag>                                 # 回到 tag

# 远程
git fetch origin                                       # 拉最新
git reset --hard origin/main                           # 重置为远程
git push origin main                                   # 推送（你自己）
git push origin <tag>                                  # 推 tag

# 数据
Get-Content .sandbox\_meta\<tid>\audit.jsonl -Tail 30  # 看审计末尾
Get-ChildItem .memory\trash -Recurse                   # 看记忆 trash
Get-ChildItem .skills_trash -Recurse                   # 看技能 trash
tar -xzf .memory_backups\<date>__memory.tar.gz -C C:\tmp  # 解压备份
```

---

## 9. 一个心智模型

把 AI-Agent 当成**一个有完整撤销键的协作系统**：

- 她在沙盒里自由跑，**不需要事前审批**
- 每一步都被记录（git + audit）
- 错了**永远可以撤销**（5 道兜底）
- 你只需要：**偶尔 review** + **必要时按这份手册操作**

不要陷入"她做了 X，糟糕，要重做"的恐慌 —— 99% 的"糟糕"都是 `git revert HEAD` 一行就能解决的。

---

## 附录：git 命令安全等级

| 命令 | 危险度 | 备注 |
|---|---|---|
| `git log` / `git show` / `git diff` | 🟢 安全 | 只读 |
| `git status` / `git blame` | 🟢 安全 | 只读 |
| `git revert` | 🟢 安全 | 加 commit 不破坏历史 |
| `git tag` | 🟢 安全 | 加标记 |
| `git checkout <file>` | 🟡 注意 | 丢未提交改动 |
| `git reset --hard` | 🔴 破坏性 | 丢 commit；但远程还在 |
| `git push --force` | 🔴 破坏性 | 改远程；本地 reflog 还在 |
| `git rm -rf .git` | ⛔ 末日 | 别跑；远程能救 |

---

**任何时候，记住：你有 GitHub 这个远程备份。本地全炸了，`git clone` 重来。**
