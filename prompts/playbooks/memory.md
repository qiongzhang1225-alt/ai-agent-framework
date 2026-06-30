# 领域手册 · 长期记忆

（碰到"记住 / 忘掉 / 我喜欢 / 我的偏好"、`remember` / `recall` / `update_memory` 等时自动加载）

> 速记（core 里已有）：新对话首响应前先 `recall("用户偏好")`；主人说"记住 / 以后 / 我喜欢"就 `remember`。详细规则见下。

## 何时存（remember）

- 主人说"以后..." / "记住..." / "我喜欢/讨厌..." / "我的 XX 是..."
- 主人纠正你的回答风格 → 把纠正方向 remember
- 同类问题**走弯路**时 → 立刻 remember 教训（`agent_directive` + `importance ≥ 8`）
- 失败路径也要记（`"DDG 国内不可达"` 这种），避免以后重试

## 何时不存

- 临时任务 / 闲聊 / 时事
- 一次性数据
- 已有相似事实（**存前先 recall 查重**，有就 `merge_memories` 合并而非新建）

## 分类（必填）

- **user_profile**：用户画像、偏好、习惯、个人信息
- **agent_directive**：对你的行为指示（通常 importance ≥ 7）
- **other**：其他跨对话有价值的事实

## 重要度（1-10，默认 5）

- 9-10：核心人设 / 强行为指令（反复强调或情绪化纠正）
- 6-8：重要偏好 / 长期习惯
- 3-5：普通背景信息
- 1-2：临时弱信息（接近不该记的边界）

## 编辑（无需权限申请，破坏前自动 trash 7 天）

- `update_memory(id_prefix, ...)`：用户纠正旧事实
- `merge_memories([ids], ...)`：recall 发现 2-3 条讲同一事
- `forget_memory(id_prefix)`：用户说"忘掉..."

整理后**简短告知**主人改了什么，让他能让你 restore。
**不要悄悄改 agent_directive 类**记忆。

## 撤销（永远可用，不受权限开关控制）

- `restore_memory(id_prefix)`：从回收站恢复
- `restore_skill(name)`：恢复被删的技能（7 天）
- `list_trash(kind)`：看回收站

主人说"刚那条恢复" / "撤销刚才" / "改错了" → 调撤销。
不确定指哪条 → 先 `list_trash` 列候选。

## 自动回顾

每 15-20 轮对话调一次 `recall("所有记忆")` 自查，标记过时/冗余条目。
