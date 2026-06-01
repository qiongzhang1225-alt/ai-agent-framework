"""定期全量快照 —— C3 安全策略。

把 ``.memory/`` 和 ``skills/`` 整个打成 tar.gz，保存最近 7 天。
作用：

- C2 的逐条 trash 解决"删一条/改一条"的撤销
- 这里的全量快照解决"整个库出问题想回滚到昨天"的远期保护

调用时机由 ``server.py`` 在启动时触发一次（同一天只快照一次）。
"""
from __future__ import annotations

import tarfile
from datetime import datetime, timedelta
from pathlib import Path

from paths import MEMORY_DIR, PROJECT_ROOT, SKILLS_DIR

MEMORY_BACKUPS_DIR = PROJECT_ROOT / ".memory_backups"
SKILLS_BACKUPS_DIR = PROJECT_ROOT / ".skills_backups"
KEEP_DAYS = 7


def _backup_dir(src: Path, dst_dir: Path, name: str) -> Path | None:
    """把 src 目录打成 tar.gz 到 dst_dir/<date>__<name>.tar.gz。

    同一天已存在则跳过（返回 None）。失败返回 None 不抛错。
    """
    if not src.exists():
        return None
    dst_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    out_path = dst_dir / f"{today}__{name}.tar.gz"
    if out_path.exists():
        return None  # 今天已快照过
    try:
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(src, arcname=src.name)
        return out_path
    except Exception as e:
        print(f"[backups] {name} 快照失败: {e}")
        # 删除可能写到一半的不完整文件
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def _cleanup_old(dst_dir: Path, keep_days: int = KEEP_DAYS) -> int:
    """清理超过 keep_days 天的 tar.gz。返回删除数。"""
    if not dst_dir.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=keep_days)
    cleaned = 0
    for f in dst_dir.glob("*.tar.gz"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink(missing_ok=True)
                cleaned += 1
        except Exception:
            pass
    return cleaned


def snapshot_all(force: bool = False) -> dict:
    """打快照 + 清理。返回本次操作摘要。

    Args:
        force: True 时即使今天已有快照也会再打一份（文件名加 ``__HHMMSS``）
    """
    summary = {
        "memory_snapshot": None,
        "skills_snapshot": None,
        "memory_cleaned": 0,
        "skills_cleaned": 0,
    }

    if force:
        # 强制模式：文件名加时间戳，绝不冲突
        ts = datetime.now().strftime("%Y-%m-%d__%H%M%S")
        if MEMORY_DIR.exists():
            try:
                MEMORY_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
                p = MEMORY_BACKUPS_DIR / f"{ts}__force.tar.gz"
                with tarfile.open(p, "w:gz") as tar:
                    tar.add(MEMORY_DIR, arcname=MEMORY_DIR.name)
                summary["memory_snapshot"] = str(p)
            except Exception as e:
                print(f"[backups] memory 强制快照失败: {e}")
        if SKILLS_DIR.exists():
            try:
                SKILLS_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
                p = SKILLS_BACKUPS_DIR / f"{ts}__force.tar.gz"
                with tarfile.open(p, "w:gz") as tar:
                    tar.add(SKILLS_DIR, arcname=SKILLS_DIR.name)
                summary["skills_snapshot"] = str(p)
            except Exception as e:
                print(f"[backups] skills 强制快照失败: {e}")
    else:
        mp = _backup_dir(MEMORY_DIR, MEMORY_BACKUPS_DIR, "memory")
        if mp:
            summary["memory_snapshot"] = str(mp)
        sp = _backup_dir(SKILLS_DIR, SKILLS_BACKUPS_DIR, "skills")
        if sp:
            summary["skills_snapshot"] = str(sp)

    summary["memory_cleaned"] = _cleanup_old(MEMORY_BACKUPS_DIR)
    summary["skills_cleaned"] = _cleanup_old(SKILLS_BACKUPS_DIR)
    return summary


def list_backups() -> dict:
    """列出当前留存的所有备份。"""
    out = {"memory": [], "skills": []}
    if MEMORY_BACKUPS_DIR.exists():
        for f in sorted(MEMORY_BACKUPS_DIR.glob("*.tar.gz")):
            out["memory"].append({
                "file": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
            })
    if SKILLS_BACKUPS_DIR.exists():
        for f in sorted(SKILLS_BACKUPS_DIR.glob("*.tar.gz")):
            out["skills"].append({
                "file": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
            })
    return out
