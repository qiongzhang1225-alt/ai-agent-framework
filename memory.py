"""长期记忆模块。

封装 ChromaDB 持久化向量库：
- 一条"记忆"是一句话事实（用户偏好 / 行为指令 / 其他信息）
- 用 bge-base-zh-v1.5 做中文语义 embedding（本地加载，详见 models/README.md）
- 元数据带 category（user_profile/agent_directive/other）+ importance（1-10）
- 数据持久化在 .memory/ 目录，server 重启不丢

工具层（agent 调用）：
- remember(fact, category, importance) - 存
- recall(query)                         - 查
管理层（UI / API 调用）：
- list_memories   - 列出全部（按 importance + created_at 排序）
- update_memory   - 改文本 / 分类 / 权重
- delete_memory   - 删除单条
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import chromadb

from paths import PROJECT_ROOT

MEMORY_DIR = PROJECT_ROOT / ".memory"
COLLECTION_NAME = "memory"
SETTINGS_FILE = MEMORY_DIR / "settings.json"
TRASH_DIR = MEMORY_DIR / "trash"   # 删除/合并/更新前的快照，C4 暴露 restore 工具
TRASH_KEEP_DAYS = 7

# 记忆分类：
#   user_profile     - 用户画像（偏好/习惯/个人信息）
#   agent_directive  - 对有希的行为指示
#   other            - 其他跨对话的事实
#   chat_log         - 主对话压缩后的"对话片段记忆"（默认从 recall 过滤掉，
#                      避免污染严肃检索；显式查"上次聊到 X" 时才包含）
VALID_CATEGORIES = ("user_profile", "agent_directive", "other", "chat_log")
DEFAULT_CATEGORY = "other"
DEFAULT_IMPORTANCE = 5  # 1-10

# 默认设置：写权限默认关闭（有希只能 remember 存新，不能 update/delete 老的）
DEFAULT_SETTINGS = {"memory_write_enabled": False}

# 中文优化 embedding：bge-base-zh-v1.5（本地加载，永不联网）
# 模型文件下载到 models/bge-base-zh-v1.5/，详见 README
_BGE_MODEL_PATH = PROJECT_ROOT / "models" / "bge-base-zh-v1.5"

_client: Any = None
_collection: Any = None
_embedding_fn: Any = None


def _get_embedding_fn():
    """获取 bge-base-zh-v1.5 embedding function（本地路径加载，不联网）。

    通过设置 HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE 强制离线模式，
    避免 sentence_transformers / transformers 内部尝试联网校验导致延迟或失败。
    """
    global _embedding_fn
    if _embedding_fn is None:
        if not _BGE_MODEL_PATH.exists():
            raise RuntimeError(
                f"bge embedding 模型不在 {_BGE_MODEL_PATH}。"
                "请按 models/README.md 手动下载 pytorch_model.bin。"
            )
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from chromadb.utils import embedding_functions
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=str(_BGE_MODEL_PATH)
        )
    return _embedding_fn


def _get_collection():
    """懒加载 chroma collection（首次调用时创建客户端 + 集合）。"""
    global _client, _collection
    if _collection is None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(MEMORY_DIR))
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_get_embedding_fn(),
        )
    return _collection


def _normalize_category(category: str | None) -> str:
    """把任意输入压成合法 category。"""
    if not category:
        return DEFAULT_CATEGORY
    cat = category.strip().lower()
    if cat in VALID_CATEGORIES:
        return cat
    return DEFAULT_CATEGORY


def _normalize_importance(importance: Any) -> int:
    """把任意输入压成 1-10 的整数。"""
    try:
        v = int(importance)
    except (TypeError, ValueError):
        return DEFAULT_IMPORTANCE
    if v < 1:
        return 1
    if v > 10:
        return 10
    return v


def add_memory(
    fact: str,
    category: str = DEFAULT_CATEGORY,
    importance: int = DEFAULT_IMPORTANCE,
) -> str:
    """存一条事实，返回 id。

    参数:
        fact: 事实文本
        category: user_profile / agent_directive / other
        importance: 1-10 权重（>=6 算重要）
    """
    fact = fact.strip()
    if not fact:
        raise ValueError("fact 不能为空")
    coll = _get_collection()
    mem_id = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    coll.add(
        ids=[mem_id],
        documents=[fact],
        metadatas=[{
            "created_at": now,
            "category": _normalize_category(category),
            "importance": _normalize_importance(importance),
        }],
    )
    return mem_id


def search_memory(
    query: str,
    top_k: int = 5,
    include_chat_log: bool = False,
) -> list[dict]:
    """语义检索，返回最相关的 top_k 条。

    Args:
        query: 检索文本
        top_k: 返回条数
        include_chat_log: 是否包含 ``chat_log`` 分类（默认不含，避免主对话
            压缩出的"对话片段记忆"污染严肃事实检索；显式查"上次聊到 X" 时设 True）
    """
    coll = _get_collection()
    total = coll.count()
    if total == 0:
        return []
    # 多取一些以便过滤后还有足够数量
    fetch_k = top_k * 3 if not include_chat_log else top_k
    result = coll.query(
        query_texts=[query],
        n_results=min(fetch_k, total),
    )
    out: list[dict] = []
    for i, doc in enumerate(result["documents"][0]):
        meta = result["metadatas"][0][i] or {}
        cat = meta.get("category", DEFAULT_CATEGORY)
        if not include_chat_log and cat == "chat_log":
            continue
        out.append({
            "id": result["ids"][0][i],
            "text": doc,
            "created_at": meta.get("created_at", ""),
            "category": cat,
            "importance": int(meta.get("importance", DEFAULT_IMPORTANCE)),
            "distance": float(result["distances"][0][i]) if result.get("distances") else 0.0,
        })
        if len(out) >= top_k:
            break
    return out


def list_memories(limit: int = 200, include_chat_log: bool = False) -> list[dict]:
    """列出所有记忆（先按 importance 降序，再按 created_at 倒序）。

    ``include_chat_log=False`` 时过滤掉 chat_log 分类（UI 默认不展示，
    避免主对话压缩记录污染主列表；可显式查看 chat_log）。
    """
    coll = _get_collection()
    data = coll.get()
    out = []
    for i, doc in enumerate(data["documents"]):
        meta = data["metadatas"][i] or {}
        cat = meta.get("category", DEFAULT_CATEGORY)
        if not include_chat_log and cat == "chat_log":
            continue
        out.append({
            "id": data["ids"][i],
            "text": doc,
            "created_at": meta.get("created_at", ""),
            "category": cat,
            "importance": int(meta.get("importance", DEFAULT_IMPORTANCE)),
        })
    # importance 降序 + created_at 倒序
    out.sort(key=lambda x: (x["importance"], x["created_at"]), reverse=True)
    return out[:limit]


def _snapshot_to_trash(record: dict, action: str) -> Path:
    """把一条记忆快照写入 trash 目录。

    Args:
        record: 至少含 id / text / category / importance / created_at 的 dict
        action: "forget" / "merge" / "update"，记到 deleted_by_action

    返回写入的文件路径（供测试 / 撤销定位用）。失败时只 print 不抛。
    """
    try:
        day_dir = TRASH_DIR / datetime.now().strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        # 文件名：<id>__<action>__<seq>.json
        # seq 让 update 同一条多次时不会互相覆盖
        seq = 1
        while True:
            fname = f"{record['id']}__{action}__{seq}.json"
            path = day_dir / fname
            if not path.exists():
                break
            seq += 1
        snapshot = {
            **record,
            "trashed_at": datetime.now().isoformat(timespec="seconds"),
            "trashed_action": action,
        }
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 顺手清理过期 trash（轻量，不阻塞）
        _cleanup_old_trash()
        return path
    except Exception as e:
        print(f"[memory.trash] 快照失败（不影响主流程）: {e}")
        return Path()


def _cleanup_old_trash() -> int:
    """清理超过 TRASH_KEEP_DAYS 天的 trash 日目录。返回清理的目录数。"""
    if not TRASH_DIR.exists():
        return 0
    import shutil
    cutoff = datetime.now().timestamp() - TRASH_KEEP_DAYS * 86400
    cleaned = 0
    for day_dir in TRASH_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            if day_dir.stat().st_mtime < cutoff:
                shutil.rmtree(day_dir, ignore_errors=True)
                cleaned += 1
        except Exception:
            pass
    return cleaned


def update_memory(
    mem_id: str,
    *,
    text: str | None = None,
    category: str | None = None,
    importance: int | None = None,
) -> dict:
    """更新一条记忆的文本 / 分类 / 权重（任一字段，传 None 表示不改）。

    更新前自动把旧版本快照到 trash（C2 保护，可被 restore_memory 找回）。
    返回更新后的记忆条目。
    """
    coll = _get_collection()
    existing = coll.get(ids=[mem_id])
    if not existing["ids"]:
        raise KeyError(f"记忆 {mem_id} 不存在")

    old_meta = existing["metadatas"][0] or {}
    old_text = existing["documents"][0]

    # 先快照旧版本到 trash
    _snapshot_to_trash({
        "id": mem_id,
        "text": old_text,
        "created_at": old_meta.get("created_at", ""),
        "category": old_meta.get("category", DEFAULT_CATEGORY),
        "importance": int(old_meta.get("importance", DEFAULT_IMPORTANCE)),
    }, action="update")

    new_text = text.strip() if text and text.strip() else old_text
    new_meta = {
        "created_at": old_meta.get("created_at", datetime.now().isoformat(timespec="seconds")),
        "category": _normalize_category(category) if category is not None else old_meta.get("category", DEFAULT_CATEGORY),
        "importance": _normalize_importance(importance) if importance is not None else int(old_meta.get("importance", DEFAULT_IMPORTANCE)),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    # ChromaDB 支持 update（文本变更会重新 embedding）
    if new_text != old_text:
        coll.update(ids=[mem_id], documents=[new_text], metadatas=[new_meta])
    else:
        coll.update(ids=[mem_id], metadatas=[new_meta])

    return {
        "id": mem_id,
        "text": new_text,
        "created_at": new_meta["created_at"],
        "category": new_meta["category"],
        "importance": new_meta["importance"],
    }


def delete_memory(mem_id: str, _action: str = "forget") -> None:
    """删除记忆。删除前自动快照到 trash。

    Args:
        mem_id: 完整 id
        _action: 内部参数，标记触发原因（forget / merge），用于 trash 元数据
    """
    coll = _get_collection()
    existing = coll.get(ids=[mem_id])
    if existing["ids"]:
        meta = existing["metadatas"][0] or {}
        _snapshot_to_trash({
            "id": mem_id,
            "text": existing["documents"][0],
            "created_at": meta.get("created_at", ""),
            "category": meta.get("category", DEFAULT_CATEGORY),
            "importance": int(meta.get("importance", DEFAULT_IMPORTANCE)),
        }, action=_action)
    coll.delete(ids=[mem_id])


def count_memories() -> int:
    return _get_collection().count()


# ── Trash 浏览 / 恢复 ────────────────────────────────────────────────────────


def list_trash_items(limit: int = 200) -> list[dict]:
    """列出 trash 里的所有快照（最新在前）。

    每条返回：id / text / category / importance / created_at / trashed_at /
              trashed_action / _snapshot_path（用于 restore_from_trash）
    """
    if not TRASH_DIR.exists():
        return []
    items: list[dict] = []
    for day_dir in TRASH_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        for f in day_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["_snapshot_path"] = str(f)
                items.append(data)
            except Exception:
                continue
    items.sort(key=lambda x: x.get("trashed_at", ""), reverse=True)
    return items[:limit]


def restore_from_trash(mem_id_prefix: str) -> dict:
    """从 trash 把一条记忆恢复回主库。

    匹配规则：trash 里 id 以 mem_id_prefix 开头的快照，取最新一条
    （多个同 id 快照取 trashed_at 最大的）。恢复后 trash 中的快照文件**不删**
    （便于多次操作 / 审计），但记忆库里会新增一条（不复用原 id —— 因为
    ChromaDB 允许同 id 但语义上更清晰是"重新加一条新的"）。

    Raises:
        KeyError: 没找到匹配的快照
    """
    prefix = mem_id_prefix.strip().lower()
    if len(prefix) < 4:
        raise ValueError(f"id 前缀至少 4 个字符（当前: {mem_id_prefix!r}）")
    candidates: list[dict] = []
    for item in list_trash_items(limit=10000):
        if str(item.get("id", "")).lower().startswith(prefix):
            candidates.append(item)
    if not candidates:
        raise KeyError(f"trash 里没有 id 以 {prefix!r} 开头的快照")
    # 取最新一条（list_trash_items 已按 trashed_at 倒序）
    target = candidates[0]
    new_id = add_memory(
        fact=target["text"],
        category=target.get("category", DEFAULT_CATEGORY),
        importance=int(target.get("importance", DEFAULT_IMPORTANCE)),
    )
    return {
        "restored_from": target["id"],
        "new_id": new_id,
        "text": target["text"],
        "category": target.get("category", DEFAULT_CATEGORY),
        "importance": int(target.get("importance", DEFAULT_IMPORTANCE)),
        "trashed_at": target.get("trashed_at", ""),
        "trashed_action": target.get("trashed_action", ""),
    }


# ── 设置（写权限开关）─────────────────────────────────────────────────────────


def load_settings() -> dict:
    """加载 settings.json，缺失/损坏时返回默认。"""
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULT_SETTINGS)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> dict:
    """写回 settings.json，返回写后的完整配置。"""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    cur = load_settings()
    # 只接受已知字段，避免脏数据
    if "memory_write_enabled" in settings:
        cur["memory_write_enabled"] = bool(settings["memory_write_enabled"])
    SETTINGS_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur


def is_write_enabled() -> bool:
    return bool(load_settings().get("memory_write_enabled", False))


# ── 工具辅助：按 id 前缀查找单条记忆 ─────────────────────────────────────────


def find_memory_by_prefix(prefix: str) -> dict:
    """按 id 前缀匹配单条记忆。匹配 0 / 多条时抛错。

    返回完整记忆条目（dict）。
    """
    if not prefix or len(prefix.strip()) < 4:
        raise ValueError(f"id 前缀至少 4 个字符（当前: {prefix!r}）")
    prefix = prefix.strip().lower()
    coll = _get_collection()
    data = coll.get()
    matches = []
    for i, mid in enumerate(data["ids"]):
        if mid.lower().startswith(prefix):
            meta = data["metadatas"][i] or {}
            matches.append({
                "id": mid,
                "text": data["documents"][i],
                "created_at": meta.get("created_at", ""),
                "category": meta.get("category", DEFAULT_CATEGORY),
                "importance": int(meta.get("importance", DEFAULT_IMPORTANCE)),
            })
    if not matches:
        raise KeyError(f"没有 id 以 {prefix!r} 开头的记忆")
    if len(matches) > 1:
        ids = ", ".join(m["id"][:12] for m in matches)
        raise ValueError(f"id 前缀 {prefix!r} 匹配到 {len(matches)} 条（{ids}……），请提供更长的前缀")
    return matches[0]


def merge_memories(
    source_ids: list[str],
    new_fact: str,
    category: str = DEFAULT_CATEGORY,
    importance: int = DEFAULT_IMPORTANCE,
) -> dict:
    """把若干条旧记忆合并为一条新记忆：先 add 新的，再 delete 旧的。

    任一旧 id 不存在则中止（不会留下半成品）。
    """
    new_fact = new_fact.strip()
    if not new_fact:
        raise ValueError("合并后的新事实不能为空")
    if not source_ids:
        raise ValueError("source_ids 不能为空")

    coll = _get_collection()
    # 先确认所有 source_ids 都存在
    existing = coll.get(ids=list(source_ids))
    missing = set(source_ids) - set(existing["ids"])
    if missing:
        raise KeyError(f"以下 id 不存在: {sorted(missing)}")

    # add 新条
    new_id = add_memory(new_fact, category=category, importance=importance)
    # 删旧条（带 action="merge" 让 trash 元数据能区分这次是合并触发的）
    for sid in source_ids:
        delete_memory(sid, _action="merge")
    return {
        "new_id": new_id,
        "new_fact": new_fact,
        "category": _normalize_category(category),
        "importance": _normalize_importance(importance),
        "deleted_count": len(source_ids),
    }
