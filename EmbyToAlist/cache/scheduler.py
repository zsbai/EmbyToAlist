import asyncio
import time
from typing import Callable, Dict, Any, Optional, List
from dataclasses import dataclass
from enum import StrEnum
from loguru import logger

class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running" 
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class ScheduledTask:
    """定时任务"""
    name: str
    func: Callable
    interval_seconds: int
    next_run_time: float
    status: TaskStatus = TaskStatus.PENDING
    last_run_time: Optional[float] = None
    error_count: int = 0
    max_retries: int = 3
    
class CacheScheduler:
    """缓存系统定时任务调度器"""
    
    def __init__(self):
        self.tasks: Dict[str, ScheduledTask] = {}
        self.running = False
        self.scheduler_task: Optional[asyncio.Task] = None
    
    def register_task(self, 
                     name: str,
                     func: Callable,
                     interval_seconds: int,
                     run_immediately: bool = False):
        """注册定时任务
        
        Args:
            name: 任务名称
            func: 异步函数
            interval_seconds: 执行间隔（秒）
            run_immediately: 是否立即执行一次
        """
        next_run = time.time() if run_immediately else time.time() + interval_seconds
        
        task = ScheduledTask(
            name=name,
            func=func,
            interval_seconds=interval_seconds,
            next_run_time=next_run
        )
        
        self.tasks[name] = task
        logger.info(f"Registered task '{name}' with {interval_seconds}s interval")
    
    async def start(self):
        """启动调度器"""
        if self.running:
            logger.warning("Scheduler is already running")
            return
            
        self.running = True
        self.scheduler_task = asyncio.create_task(self._run_scheduler())
        logger.info("Cache scheduler started")
    
    async def stop(self):
        """停止调度器"""
        self.running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
        logger.info("Cache scheduler stopped")
    
    async def _run_scheduler(self):
        """调度器主循环"""
        while self.running:
            current_time = time.time()
            
            for task in self.tasks.values():
                if current_time >= task.next_run_time and task.status != TaskStatus.RUNNING:
                    asyncio.create_task(self._execute_task(task))
            
            # 每5秒检查一次
            await asyncio.sleep(5)
    
    async def _execute_task(self, task: ScheduledTask):
        """执行单个任务"""
        task.status = TaskStatus.RUNNING
        task.last_run_time = time.time()
        
        try:
            logger.debug(f"Executing task: {task.name}")
            
            if asyncio.iscoroutinefunction(task.func):
                await task.func()
            else:
                task.func()
                
            task.status = TaskStatus.COMPLETED
            task.error_count = 0
            logger.debug(f"Task '{task.name}' completed successfully")
            
        except Exception as e:
            task.error_count += 1
            task.status = TaskStatus.FAILED
            logger.error(f"Task '{task.name}' failed (attempt {task.error_count}/{task.max_retries}): {e}")
            
            if task.error_count >= task.max_retries:
                logger.error(f"Task '{task.name}' exceeded max retries, will retry next cycle")
                task.error_count = 0  # 重置错误计数，下个周期重试
        
        # 设置下次执行时间
        task.next_run_time = time.time() + task.interval_seconds
        task.status = TaskStatus.PENDING
    
    def get_task_status(self, name: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        if name not in self.tasks:
            return None
            
        task = self.tasks[name]
        return {
            'name': task.name,
            'status': task.status,
            'last_run_time': task.last_run_time,
            'next_run_time': task.next_run_time,
            'error_count': task.error_count,
            'interval_seconds': task.interval_seconds
        }
    
    def list_all_tasks(self) -> List[Dict[str, Any]]:
        """列出所有任务状态"""
        return [self.get_task_status(name) for name in self.tasks.keys()]