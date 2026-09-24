"""后台任务：扫描 / 回测 / 校准这类要跑几十秒到几分钟的活。

HTTP 请求不能挂着等回测跑完，所以提交后立刻返回任务号，前端轮询进度。
只开一个工作线程：这是单人本地工具，计算是 CPU 密集的，并发跑两个回测
只会让两个都变慢；排队更可预期。
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

KEEP_JOBS = 40


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"          # queued / running / done / error
    progress: float = 0.0
    text: str = ""
    error: str = ""
    result: Any = None              # 给前端的 JSON
    private: Dict[str, Any] = field(default_factory=dict)   # 留在服务端的对象（K线、配置）
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0

    def report(self, done: float, total: float, text: str = "") -> None:
        self.progress = min(max(done / max(total, 1), 0.0), 1.0)
        if text:
            self.text = text

    def public(self) -> dict:
        end = self.finished or time.time()
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "progress": self.progress, "text": self.text, "error": self.error,
            "elapsed": round(end - self.started, 1) if self.started else 0.0,
            "result": self.result if self.status == "done" else None,
        }


class JobManager:
    def __init__(self, workers: int = 1):
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

    def submit(self, kind: str, fn: Callable[[Job], Any]) -> Job:
        """fn(job) 返回给前端的结果；要留在服务端的东西写进 job.private。"""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, text="排队中")
        with self._lock:
            self._jobs[job.id] = job
            self._trim()
        self._pool.submit(self._run, job, fn)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job, fn: Callable[[Job], Any]) -> None:
        job.status, job.started, job.text = "running", time.time(), "运行中"
        try:
            job.result = fn(job)
            job.progress, job.status = 1.0, "done"
        except Exception as exc:        # 任务失败要把原因带回界面，不能只在终端里
            job.error = f"{type(exc).__name__}: {exc}"
            job.private["traceback"] = traceback.format_exc()
            job.status = "error"
        finally:
            job.finished = time.time()

    def _trim(self) -> None:
        finished = [k for k, j in self._jobs.items() if j.status in ("done", "error")]
        while len(self._jobs) > KEEP_JOBS and finished:
            self._jobs.pop(finished.pop(0), None)
