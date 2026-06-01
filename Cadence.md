## Cadence v4：完整架构与部署方案

> v4 变更：窗口内追加升级时临时切回基线系统，使 Worker 可自由重启自检，不干扰使用。

---

### 一、核心概念

#### Worker 与 Reviewer

每次版本迭代中，两个系统有固定角色：

| 角色 | 职责 |
|------|------|
| **Worker** | 实施代码修改、运行自检、生成升级报告 |
| **Reviewer** | 制定量化验收计划（检验方案），审核是否通过 |

角色在**窗口边界**交换。窗口内不变。

---

#### 版本切换时间窗口

`UPGRADE_WINDOW`（默认 5 小时）定义窗口。行为：

- **首次升级**：正常升级 + 切换 + 版本递增。窗口从切换时刻开始。
- **窗口内追加**：临时切回基线 → Worker 升级 → 切换回来。版本号不变。窗口不重置。
- **窗口结束后**：角色交换，回填 + 补丁应用 + 升级新版本 + 切换 + 版本递增。

---

### 二、保护体系总览（四层防御）

| 层级 | 机制 | 职责 | 触发者 |
|------|------|------|--------|
| **第一层** | 备用系统沙盒测试 | 阻止有问题的修改进入 Active | Agent 自动 |
| **第二层** | 用户软验收 | 实际使用中隐式确认，发现问题随时回滚 | 你（隐式） |
| **第三层** | 关键操作前自动快照 | 切换/回填前保存可回滚状态 | 脚本自动 |
| **第四层** | 定时快照 | 兜底保护，覆盖一切意外 | cron 自动 |

---

### 三、完整目录结构

```
/opt/agent/                     # === 工作目录 ===
├── code/                       # 核心代码层（Agent 可读写）
│   ├── a/                      # 系统 A
│   │   ├── VERSION
│   │   ├── src/
│   │   ├── tools/
│   │   ├── tests/
│   │   └── requirements.txt
│   └── b/                      # 系统 B（结构同 A）
│       ├── VERSION
│       ├── src/
│       ├── tools/
│       ├── tests/
│       └── requirements.txt
│
├── config/                     # 配置层（Agent 只读）
│   ├── a.env
│   ├── b.env
│   └── cadence.conf
│
├── data/                       # 共享数据层（A/B 共用）
│   ├── memory.db
│   ├── knowledge_base/
│   └── reports/
│       ├── index.md
│       └── patches/            # 窗口内追加补丁暂存
│
├── gateway.sh                  # 管理脚本（rollback + 状态查看）
└── agent-admin.sh             # Agent 唯一管理入口


/backups/agent/                 # === 快照目录（独立于工作目录）===
├── pre-switch/                 # 保留最近 5 个
├── pre-backfill/               # 保留最近 5 个
├── hourly/                     # 保留最近 24 个
├── daily/                      # 保留最近 7 个
└── weekly/                     # 保留最近 4 个
```

**权限设定：**

| 路径 | Agent 权限 | 说明 |
|------|-----------|------|
| `code/a/` | 读写执行 | 系统 A 代码 |
| `code/b/` | 读写执行 | 系统 B 代码 |
| `config/` | 只读 | 写权限仅归你 |
| `data/` | 读写 | 共享数据 |
| `/backups/agent/` | **无权限** | 仅你和 root |
| `agent-admin.sh` | 执行 | 有限管理接口 |

---

### 四、全局配置：cadence.conf

```bash
UPGRADE_WINDOW=5         # 版本切换时间窗口（小时）
MAX_RETRIES=3            # 单个需求最大重试次数
ROLLBACK_EVENT_FILE="/opt/agent/data/reports/rollback-event.md"
```

---

### 五、Agent 权限边界：agent-admin.sh

```bash
#!/bin/bash
# agent-admin.sh - Agent 唯一入口

AGENT_HOME="/opt/agent"
SNAPSHOT_BASE="/backups/agent/pre-switch"
ACTIVE_CONF="/etc/nginx/conf.d/agent.conf"

source "$AGENT_HOME/config/cadence.conf" 2>/dev/null

case "$1" in
  start-standby)
    CURRENT=$(grep -oP 'proxy_pass http://\K[^;]+' "$ACTIVE_CONF")
    if [ "$CURRENT" == "127.0.0.1:9000" ]; then
      STANDBY="b"; STANDBY_PORT=9001
    else
      STANDBY="a"; STANDBY_PORT=9000
    fi
    echo "正在启动备用系统 $STANDBY (端口 $STANDBY_PORT)..."
    cd "$AGENT_HOME/code/$STANDBY" && \
      nohup python -m src.main --port "$STANDBY_PORT" \
      --env "$AGENT_HOME/config/$STANDBY.env" > /tmp/agent-$STANDBY.log 2>&1 &
    echo "PID: $!"
    ;;

  stop-standby)
    CURRENT=$(grep -oP 'proxy_pass http://\K[^;]+' "$ACTIVE_CONF")
    if [ "$CURRENT" == "127.0.0.1:9000" ]; then
      STANDBY="b"; STANDBY_PORT=9001
    else
      STANDBY="a"; STANDBY_PORT=9000
    fi
    echo "正在停止备用系统 $STANDBY..."
    lsof -ti:$STANDBY_PORT | xargs kill 2>/dev/null
    echo "已停止。"
    ;;

  test-standby)
    CURRENT=$(grep -oP 'proxy_pass http://\K[^;]+' "$ACTIVE_CONF")
    if [ "$CURRENT" == "127.0.0.1:9000" ]; then
      STANDBY="b"
    else
      STANDBY="a"
    fi
    echo "正在运行 $STANDBY 的自检脚本..."
    cd "$AGENT_HOME/code/$STANDBY" && python -m pytest tests/ -v
    ;;

  switch-standby)
    # 正式切换：快照 + 切换 + 写窗口时间戳
    CURRENT=$(grep -oP 'proxy_pass http://\K[^;]+' "$ACTIVE_CONF")
    if [ "$CURRENT" == "127.0.0.1:9000" ]; then
      NEW_ACTIVE="b"; NEW_PORT=9001
    else
      NEW_ACTIVE="a"; NEW_PORT=9000
    fi

    echo "正在创建切换前快照..."
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    SNAPSHOT_DIR="$SNAPSHOT_BASE/$TIMESTAMP"
    mkdir -p "$SNAPSHOT_DIR"/{code,data,config}
    rsync -a "$AGENT_HOME/code/" "$SNAPSHOT_DIR/code/"
    rsync -a "$AGENT_HOME/data/" "$SNAPSHOT_DIR/data/"
    rsync -a "$AGENT_HOME/config/" "$SNAPSHOT_DIR/config/"
    echo "快照完成: $TIMESTAMP"

    echo "正在切换到系统 $NEW_ACTIVE (端口 $NEW_PORT)..."
    sed -i "s/proxy_pass http:\/\/[^;]*/proxy_pass http:\/\/127.0.0.1:$NEW_PORT/" "$ACTIVE_CONF"
    nginx -s reload

    date +%s > "$AGENT_HOME/data/.last_switch_ts"
    echo "已切换。当前 Active: 系统 $NEW_ACTIVE"
    ;;

  temp-switch)
    # 临时切换：快照 + 切换，但不更新窗口时间戳
    CURRENT=$(grep -oP 'proxy_pass http://\K[^;]+' "$ACTIVE_CONF")
    if [ "$CURRENT" == "127.0.0.1:9000" ]; then
      NEW_ACTIVE="b"; NEW_PORT=9001
    else
      NEW_ACTIVE="a"; NEW_PORT=9000
    fi

    echo "正在创建临时切换前快照..."
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    SNAPSHOT_DIR="$SNAPSHOT_BASE/$TIMESTAMP"
    mkdir -p "$SNAPSHOT_DIR"/{code,data,config}
    rsync -a "$AGENT_HOME/code/" "$SNAPSHOT_DIR/code/"
    rsync -a "$AGENT_HOME/data/" "$SNAPSHOT_DIR/data/"
    rsync -a "$AGENT_HOME/config/" "$SNAPSHOT_DIR/config/"
    echo "快照完成: $TIMESTAMP"

    echo "正在临时切换到系统 $NEW_ACTIVE (端口 $NEW_PORT)..."
    sed -i "s/proxy_pass http:\/\/[^;]*/proxy_pass http:\/\/127.0.0.1:$NEW_PORT/" "$ACTIVE_CONF"
    nginx -s reload

    # 不写 .last_switch_ts —— 窗口计时不受影响
    echo "临时切换完成。窗口计时不受影响。"
    ;;

  snapshot-backfill)
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    mkdir -p "/backups/agent/pre-backfill/$TIMESTAMP/code"
    rsync -a "$AGENT_HOME/code/" "/backups/agent/pre-backfill/$TIMESTAMP/code/"
    echo "回填前快照已完成: $TIMESTAMP"
    ;;

  *)
    echo "用法:"
    echo "  $0 start-standby       启动备用系统"
    echo "  $0 stop-standby        停止备用系统"
    echo "  $0 test-standby        运行备用系统自检"
    echo "  $0 switch-standby      正式切换（更新窗口时间戳）"
    echo "  $0 temp-switch         临时切换（不更新窗口时间戳）"
    echo "  $0 snapshot-backfill   创建回填前快照"
    ;;
esac
```

---

### 六、自检脚本规范

每个系统自行维护 `tests/`。Agent 调用不编写。

---

### 七、Agent 自建量化检验标准

升级报告中必须包含检验方案。基线从 Active 采集，检验脚本 `tests/upgrade_check.py`。

---

### 八、完整升级流程

#### 阶段一：首次升级（窗口开启）

初始：**A = Active (v1.0)**，**B = Standby (v1.0)**。

**角色分配**：B = Worker，A = Reviewer。

1. 你："升级搜索工具。"
2. **Worker (B)**：在 `code/b/` 实施修改，VERSION → v2.0，编写 `upgrade_check.py`。
3. **Reviewer (A)**：制定验收计划（量化检验标准），存入 `data/reports/upgrade-v2.0.md`。
4. Worker: `start-standby` → `test-standby`。
5. **自检失败** → 失败处理（见第九节）。
6. **自检通过**：
   - Worker: `stop-standby`，完成升级报告。
   - Worker 调用 `switch-standby`（正式切换，写 `.last_switch_ts`，**窗口开始**）。
   - 通知你："v2.0 已上线。Worker=B, Reviewer=A。"

现在 **B = Active (v2.0)**，**A = Standby (v1.0)**。窗口计时中。

---

#### 阶段二：窗口内追加升级（临时切换模式）

你在 B (v2.0) 上使用中，提出小需求。

**角色不变**：B = Worker，A = Reviewer。

7. Agent 检查：窗口内。
8. **临时切换**：调 `temp-switch`，A（v1.0）变为临时 Active。
   - 你短暂回到 v1.0 体验，暂时失去 v2.0 新增功能。
   - 窗口计时不受影响。
9. **Worker (B，现在是 Standby)**：
   - 回填 B 到包含之前所有窗口内补丁（首次追加时 B 已是 v2.0，无需回填）
   - 实施追加改动（v2.0+追加）
   - 可自由重启进程、跑自检
   - `start-standby` → `test-standby`
10. **Reviewer (A)**：制定本次追加的验收计划，追加到 `upgrade-v2.0.md`。
11. **自检失败** → 丢弃 B 的修改，恢复。`temp-switch` 切回 B（v2.0 原始版）。失败处理。
12. **自检通过**：
    - Worker: `stop-standby`
    - 生成补丁 `data/reports/patches/patch-v2.0-N.diff`
    - **切回来**：调 `temp-switch`，B（v2.0+追加）回到 Active。
    - 通知你："追加完成，已切回 v2.0+。"
    - 窗口不重置。

窗口内可循环多次。每次：临时切到 A → B 升级自检 → 切回 B。

---

#### 阶段三：窗口结束，版本更迭

窗口结束后你再次提需求。

13. Agent 检查：超出窗口 → **版本更迭**。
14. **角色交换**：A 成为新 Worker，B 成为新 Reviewer。
15. 新 Worker (A)：
    - `snapshot-backfill`
    - 回填 A：v1.0 → v2.0 → 依次应用所有补丁（`patch-v2.0-1.diff`, `patch-v2.0-2.diff`...）
    - 在 A 上升级 v3.0
16. 新 Reviewer (B)：制定验收计划。
17. `start-standby` → `test-standby`。
18. **成功** → VERSION → v3.0 → `switch-standby` → 新窗口开启。
19. **失败** → 失败处理。

---

#### 升级记录索引格式

```markdown
# 升级记录索引
| 版本 | 日期 | Worker | 升级摘要 | 数据迁移 | 窗口内追加 |
|------|------|--------|---------|---------|-----------|
| v2.0 | 05-29 | B | 搜索重构 + 排序优化（追加×2） | 否 | 2 次 |
| v3.0 | 06-02 | A | 记忆模块升级 | 是 | 0 次 |
```

---

### 九、失败处理策略

| 失败次数 | 行为 |
|---------|------|
| **第 1 次** | 分析原因，调整策略，重新实施 |
| **第 2 次** | 换根本不同的技术路线 |
| **第 3 次** | 最后尝试，再败→回滚 |

**3 次全败后**：

1. 恢复修改前状态（窗口内追加：丢弃修改，`temp-switch` 切回工作版本）。
2. 生成 `data/reports/failure-vX.Y.md`。
3. 人工介入。

窗口内追加失败只影响当前补丁，已生成的补丁不受影响。

---

### 十、回滚感知

gateway.sh rollback 写入 `rollback-event.md`。Agent 操作前检查：

- 跳过导致回滚的版本
- `index.md` 标记 `❌ 已回滚`
- 未应用补丁一并废弃

---

### 十一、数据兼容性与回滚

改 schema 必须附带迁移脚本和降级脚本。rollback 先跑降级再回滚代码。

---

### 十二、版本标记规范

每个实例 `VERSION` 文件（如 `v1.0`）。窗口内 Standby 的 VERSION 不变。窗口结束正式切换时更新。

---

### 十三、定时快照机制（cron）

```bash
0 * * * * rsync -a --delete /opt/agent/data/ /backups/agent/hourly/$(date +\%Y\%m\%d-\%H\%M)/
0 2 * * * rsync -a --delete /opt/agent/data/ /backups/agent/daily/$(date +\%Y\%m\%d)/ && rsync -a --delete /opt/agent/config/ /backups/agent/daily/$(date +\%Y\%m\%d)/config/
0 3 * * 0 rsync -a --delete /opt/agent/ /backups/agent/weekly/$(date +\%Y\%m\%d)/
```

---

### 十四、Active 健康监控（cron）

```bash
* * * * * curl -sf -o /dev/null -m 5 http://127.0.0.1:9000/health || \
  echo "[$(date)] 端口 9000 无响应" >> /var/log/cadence/health.log
* * * * * curl -sf -o /dev/null -m 5 http://127.0.0.1:9001/health || \
  echo "[$(date)] 端口 9001 无响应" >> /var/log/cadence/health.log
```

---

### 十五、快照清理策略（cron）

```bash
0 4 * * * ls -t /backups/agent/pre-switch/ | tail -n +6 | xargs -I {} rm -rf /backups/agent/pre-switch/{}
0 4 * * * ls -t /backups/agent/pre-backfill/ | tail -n +6 | xargs -I {} rm -rf /backups/agent/pre-backfill/{}
0 4 * * * ls -t /backups/agent/hourly/ | tail -n +25 | xargs -I {} rm -rf /backups/agent/hourly/{}
0 4 * * * ls -t /backups/agent/daily/ | tail -n +8 | xargs -I {} rm -rf /backups/agent/daily/{}
0 4 * * * ls -t /backups/agent/weekly/ | tail -n +5 | xargs -I {} rm -rf /backups/agent/weekly/{}
```

---

### 十六、完整 gateway.sh 脚本

```bash
#!/bin/bash
AGENT_HOME="/opt/agent"
SNAPSHOT_BASE="/backups/agent/pre-switch"
ACTIVE_CONF="/etc/nginx/conf.d/agent.conf"
ROLLBACK_EVENT="$AGENT_HOME/data/reports/rollback-event.md"

case "$1" in
  status)
    CURRENT=$(grep -oP 'proxy_pass http://\K[^;]+' "$ACTIVE_CONF")
    [ "$CURRENT" == "127.0.0.1:9000" ] && echo "当前 Active: 系统 A"
    [ "$CURRENT" == "127.0.0.1:9001" ] && echo "当前 Active: 系统 B"
    ;;

  rollback)
    if [ "$2" == "list" ]; then
      ls -t "$SNAPSHOT_BASE/"
    elif [ -n "$2" ]; then
      ROLLBACK_FROM=$(cat "$AGENT_HOME/code/a/VERSION" 2>/dev/null || \
                      cat "$AGENT_HOME/code/b/VERSION")
      ROLLBACK_VERSION=$(cat "$SNAPSHOT_BASE/$2/code/a/VERSION" 2>/dev/null || \
                         cat "$SNAPSHOT_BASE/$2/code/b/VERSION")
      echo "回滚前: $ROLLBACK_FROM → 目标: $ROLLBACK_VERSION"

      DOWNGRADE_GLOB="$AGENT_HOME/data/reports/downgrade-*-to-$ROLLBACK_VERSION.sql"
      if ls $DOWNGRADE_GLOB 1>/dev/null 2>&1; then
        sqlite3 "$AGENT_HOME/data/memory.db" < $(ls -t $DOWNGRADE_GLOB | head -1)
        echo "数据降级完成。"
      fi

      rsync -a --delete "$SNAPSHOT_BASE/$2/code/" "$AGENT_HOME/code/"
      rsync -a --delete "$SNAPSHOT_BASE/$2/config/" "$AGENT_HOME/config/"
      echo "回滚完成。"

      cat > "$ROLLBACK_EVENT" << EOF
## 回滚事件
- 时间：$(date '+%Y-%m-%d %H:%M')
- 快照：$2
- 回滚前版本：$ROLLBACK_FROM
- 回滚后版本：$ROLLBACK_VERSION
EOF
    else
      echo "用法: $0 rollback list | rollback <name>"
    fi
    ;;

  *)
    echo "用法: $0 status | rollback list | rollback <name>"
    ;;
esac
```

---

### 十七、核心原则总结

| 原则 | 说明 |
|------|------|
| **Worker/Reviewer 分离** | Worker 执行修改，Reviewer 制定验收。角色在窗口边界交换 |
| **永远只在备用系统上改** | 窗口内通过 temp-switch 确保 Worker 始终在 Standby |
| **Agent 无 shell 权限** | 六个接口：start/stop/test/switch/temp-switch/snapshot |
| **窗口内：临时切换模式** | temp-switch 到基线 → Worker 升级自检（可重启） → temp-switch 回来 |
| **窗口外：版本更迭** | 角色交换 → 回填 + 应用补丁 → 升级 → 正式切换 |
| **temp-switch 不重置窗口** | 不写 .last_switch_ts，窗口计时连续 |
| **超出窗口提需求即验收** | 你的新需求 = 对当前版本的肯定 |
| **发现问题随时回滚** | gateway.sh rollback，Agent 无权干预 |
| **回滚即感知** | rollback 写入事件文件 |
| **失败不重样** | 最多 3 次，每次换策略 |
| **万物皆版本，万物皆报告** | 升级/失败/回滚/补丁全部可追溯 |
| **改 schema 必带降级脚本** | 迁移 + 降级 |
| **切换即快照** | switch-standby 和 temp-switch 都自动打快照 |
| **快照独立于 Agent** | Agent 无权限访问 |
| **VERSION 即锚点** | 判定回填、匹配降级、窗口内锁定 |
