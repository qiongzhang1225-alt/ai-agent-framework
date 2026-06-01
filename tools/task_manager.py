"""后台任务管理器（D3）。

让 run_command_stream 支持 background=True 模式——进程独立于 chat SSE，
关页面不断连，重开页面可查历史输出。

架构：
- TaskManager 单例持有所有任务的 dict
- 每任务存 asyncio.subprocess.Process + 输出行列表
- 自动清理超过 TTL 的已完成任务
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAX_OUTPUT_LINES = 5000          # 内存保留最大行数（防泄漏）
CLEANUP_INTERVAL = 1800          # 清理间隔 30 分钟
TASK_TTL = 86400                 # 完成后保留 24 小时
LOG_DIR_NAME = "tasks"           # .sandbox/_meta/tasks/{task_id}.log


@dataclass
class Task:
    """一个后台运行的任务。"""
    id: str
    cmd: str
    args: list[str]
    cwd: str
    status: str = "running"           # running / done / failed / cancelled
    pid: int = 0
    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    exit_code: int | None = None
    start_time: float = 0.0
    end_time: float | None = None
    _process: asyncio.subprocess.Process | None = None

    @property
    def elapsed(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def name(self) -> str:
        """显示用的简短名字（cmd + 前两个参数）。"""
        parts = [self.cmd] + self.args[:2]
        s = " ".join(parts)
        return s[:60]

    def to_dict(self) -> dict[str, Any]:
        """给 API 用的精简表示。"""
        return {
            "id": self.id,
            "name": self.name,
            "cmd": self.cmd,
            "args": self.args,
            "cwd": self.cwd,
            "status": self.status,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "elapsed": round(self.elapsed, 1),
            "stdout_count": len(self.stdout_lines),
            "stderr_count": len(self.stderr_lines),
        }

    def to_dict_full(self) -> dict[str, Any]:
        """含完整输出的详情。"""
        d = self.to_dict()
        d["stdout"] = "\n".join(self.stdout_lines)
        d["stderr"] = "\n".join(self.stderr_lines)
        return d


class TaskManager:
    """后台任务管理器单例。"""

    _instance: "TaskManager | None" = None

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

    async def start(
        self,
        cmd: str,
        args: list[str],
        cwd: str | Path,
        task_id: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Task:
        """启动新任务。返回 Task 对象（status=running）。"""
        import uuid
        tid = task_id or f"task_{uuid.uuid4().hex[:8]}"
        cwd = str(Path(cwd).resolve())

        task = Task(
            id=tid,
            cmd=cmd,
            args=args,
            cwd=cwd,
            start_time=time.time(),
        )

        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        task.pid = proc.pid
        task._process = proc

        async with self._lock:
            self._tasks[tid] = task

        # 启动后台读取任务（不阻塞 start 返回）
        asyncio.create_task(self._read_output(task, proc))

        # 确保清理任务在跑
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        return task

    async def _read_output(self, task: Task, proc: asyncio.subprocess.Process) -> None:
        """后台读 stdout/stderr，直到进程结束。"""
        from paths import META_DIR

        log_dir = META_DIR / LOG_DIR_NAME
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{task.id}.log"
        log_fh = None

        try:
            log_fh = open(log_path, "a", encoding="utf-8")
        except Exception:
            pass

        async def _read(stream, lines_list, tag):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                lines_list.append(text)
                if len(lines_list) > MAX_OUTPUT_LINES:
                    lines_list.pop(0)
                if log_fh:
                    try:
                        log_fh.write(f"[{tag}] {text}\n")
                        log_fh.flush()
                    except Exception:
                        pass

        await asyncio.gather(
            _read(proc.stdout, task.stdout_lines, "stdout"),
            _read(proc.stderr, task.stderr_lines, "stderr"),
        )

        if log_fh:
            try:
                log_fh.close()
            except Exception:
                pass

        retcode = await proc.wait()
        task.exit_code = retcode
        task.status = "done" if retcode == 0 else "failed"
        task.end_time = time.time()
        task._process = None

    def get(self, task_id: str) -> Task | None:
        """按 ID 查任务。"""
        return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        """返回所有任务（最新的在前）。"""
        tasks = sorted(self._tasks.values(), key=lambda t: t.start_time, reverse=True)
        return tasks

    def cancel(self, task_id: str) -> bool:
        """取消任务。返回 True 表示成功杀死进程。"""
        task = self._tasks.get(task_id)
        if not task:
            return False
        proc = task._process
        if proc and proc.returncode is None:
            try:
                proc.kill()
                task.status = "cancelled"
                task.end_time = time.time()
                return True
            except Exception:
                pass
        return False

    def remove(self, task_id: str) -> bool:
        """从 registry 移除任务（不删日志文件）。"""
        if task_id in self._tasks:
            if self._tasks[task_id].status == "running":
                self.cancel(task_id)
            del self._tasks[task_id]
            return True
        return False

    async def _cleanup_loop(self) -> None:
        """定期清理超 TTL 的已完成任务。"""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            now = time.time()
            to_remove = []
            async with self._lock:
                for tid, task in self._tasks.items():
                    if task.status != "running" and task.end_time:
                        if now - task.end_time > TASK_TTL:
                            to_remove.append(tid)
                for tid in to_remove:
                    del self._tasks[tid]
            if to_remove:
                print(f"[TaskManager] 清理 {len(to_remove)} 个过期任务")


# 全局单例
_manager = TaskManager()


def get_manager() -> TaskManager:
    return _manager
