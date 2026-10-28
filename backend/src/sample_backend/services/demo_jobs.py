"""可取消的示例任务：HTTP 存取状态，WebSocket 只广播实时变化。"""

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable
from uuid import uuid4

from loguru import logger

Publish = Callable[[str, dict], Awaitable[None]]


@dataclass
class DemoJob:
    id: str
    status: str = "running"
    progress: int = 0


class DemoJobs:
    """A job ID lets HTTP requests and WebSocket events refer to the same task."""

    def __init__(self, publish: Publish):
        self._publish = publish
        self._jobs: dict[str, DemoJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self) -> DemoJob:
        job = DemoJob(id=uuid4().hex)
        # _jobs 持久保留本次进程内的任务状态；_tasks 仅持有活跃协程。
        self._jobs[job.id] = job
        logger.info('demo_job.started task_id={}', job.id)
        task = asyncio.create_task(self._run(job))
        self._tasks[job.id] = task
        # 协程结束后清理引用，但继续允许前端通过任务 ID 查询最终状态。
        task.add_done_callback(lambda _: self._tasks.pop(job.id, None))
        return job

    def get(self, job_id: str) -> DemoJob | None:
        return self._jobs.get(job_id)

    async def cancel(self, job_id: str) -> DemoJob | None:
        """Cancellation waits until the worker publishes its final state."""
        job = self.get(job_id)
        if job is None:
            return None
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            # 等待取消事件广播完成后，才把最终状态返回给调用方。
            await asyncio.gather(task, return_exceptions=True)
        return job

    async def _run(self, job: DemoJob) -> None:
        try:
            for progress in range(0, 101, 10):
                job.progress = progress
                await self._publish("task.progress", {"task_id": job.id, "progress": progress})
                if progress < 100:
                    await asyncio.sleep(0.4)
            job.status = "completed"
            await self._publish("task.completed", {"task_id": job.id})
            logger.info('demo_job.completed task_id={}', job.id)
        except asyncio.CancelledError:
            # 取消也要写入最终状态并广播，然后继续传播取消异常。
            job.status = "cancelled"
            await self._publish("task.cancelled", {"task_id": job.id})
            logger.info('demo_job.cancelled task_id={}', job.id)
            raise

    async def close(self) -> None:
        """All owned tasks are cancelled during FastAPI shutdown."""
        # 复制任务快照；完成回调可能异步修改原字典。
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
