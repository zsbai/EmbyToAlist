import asyncio
import time

from loguru import logger

from typing import Optional, TYPE_CHECKING, Any, Union, Type
if TYPE_CHECKING:
    from .system import CacheSystem

class TaskManager():
    """任务管理器
    用于管理ChunksWriter实例，避免创建多个写入task
    每个文件包含头尾两个任务, head和tail
    {file_id: [ChunksWriter, ChunksWriter]}
    """
    def __init__(self, default_ttl: Optional[int] = 300, cleanup_interval: int = 1):
        """
        Args:
            default_ttl (Optional[int], optional): 任务默认过期时间，单位秒
            cleanup_interval (int, optional): 清理间隔时间，单位秒. Defaults to 1.
        """
        self.default_ttl = default_ttl
        self.cleanup_interval = cleanup_interval
        self.tasks: dict[str, dict[str, list]] = {}
        self.lock = asyncio.Lock()
        
        # 启动清理任务
        asyncio.create_task(self._cleanup_task())
        
    def _get_type_key(self, task_type: Union[str, Type]) -> str:
        if isinstance(task_type, str):
            return task_type
        else:
            return task_type.__name__

    async def _cleanup_task(self):
        """
        定时清理过期的任务
        """
        while True:
            now = time.time()
            expired = []
            async with self.lock:
                for type_key, file_map in list(self.tasks.items()):
                    for file_id, sub_map in list(file_map.items()):
                        for sub_key, (task, expired_at) in list(sub_map.items()):
                            if expired_at is not None and expired_at <= now:
                                logger.debug(f"Cleaning up expired task for {file_id} with sub key '{sub_key}'")
                                expired.append((type_key, file_id, sub_key))
                                
            for type_key, file_id, sub_key in expired:
                await self.remove_task(type_key, file_id, sub_key)
            await asyncio.sleep(self.cleanup_interval)


    async def get_task(
        self, 
        task_type: Union[str, Type], 
        file_id: str, 
        sub_key: Any = None
    ) -> Optional[Any]:
        """获取任务，没有任务则返回None
        
        Args:
            task_type (str): 任务类型
            file_id (str): 文件ID
            sub_key (Any): 子键，默认为None
        Returns:
            Optional[Any]: 任务实例或None
        """
        type_key = self._get_type_key(task_type)
        async with self.lock:
            # ignore ttl here, just return the task if exists
            task_tuple = self.tasks.get(type_key, {}).get(file_id, {}).get(sub_key, ())
            return task_tuple[0] if task_tuple else None
            
    async def create_task(
        self, 
        task_type: Union[str, Type], 
        file_id: str, 
        task_instance: Any, 
        sub_key: Any = None,
        ttl: Optional[int] = None
    ):
        """添加任务1
        
        Args:
            task_type (str): 任务类型
            file_id (str): 文件ID
            task_instance (Any): 任务实例
            sub_key (Any): 子键，默认为None   
        """
        if ttl is None:
            ttl = self.default_ttl
        
        type_key = self._get_type_key(task_type)
        expired_at = time.time() + ttl
        async with self.lock:
            self.tasks.setdefault(type_key, {}).setdefault(file_id, {})[sub_key] = (task_instance, expired_at)
        logger.debug(f"Task created for {file_id} with sub key '{sub_key}', ttl={ttl}")
            
    async def remove_task(
        self, 
        task_type: Union[str, Type],
        file_id: str,
        sub_key: Any = None
    ):
        """移除任务
        
        Args:
            task_type (str): 任务类型
            file_id (str): 文件ID
            sub_key (Any): 子键，默认为None
        """
        type_key = self._get_type_key(task_type)
        async with self.lock:
            task_group = self.tasks.get(type_key, {}).get(file_id, {})
            if sub_key in task_group:
                del task_group[sub_key]
                logger.debug(f"Task removed for {file_id} with sub key '{sub_key}'")

            else:
                logger.debug(f"Task not found for {file_id} with sub key '{sub_key}'")
            
            # 清理空结构
            if not task_group:
                file_map = self.tasks.get(type_key)
                if file_map:
                    file_map.pop(file_id, None)
                    if not file_map:
                        self.tasks.pop(type_key, None) 
            logger.debug(f"Task list: {self.tasks}")
            
        
class AppContext():
    _cache_system: 'CacheSystem' = None
    _task_manager: TaskManager = None
    _scheduler = None
    _cleanup_manager = None
    
    @classmethod
    def init(cls, root_dir: str):
        cls.init_cache_system(root_dir)
    
    @classmethod
    def init_cache_system(cls, root_dir: str):
        from .system import CacheSystem
        cls._cache_system = CacheSystem(root_dir)
        
    @classmethod
    async def start_scheduler(cls):
        """启动调度器和清理管理器"""
        try:
            if cls._scheduler is None:
                from .scheduler import CacheScheduler
                from .cleanup import CacheCleanupManager
                
                cls._scheduler = CacheScheduler()
                cls._cleanup_manager = CacheCleanupManager(
                    cls._cache_system.storage, 
                    cls._scheduler
                )
                
                # 注册清理任务
                cls._cleanup_manager.register_cleanup_tasks()
                
                # 启动调度器
                await cls._scheduler.start()
                logger.info("Cache scheduler and cleanup manager started")
        except Exception as e:
            logger.error(f"Failed to start cache scheduler: {e}")
            # 清理部分初始化的资源
            if cls._scheduler:
                try:
                    await cls._scheduler.stop()
                except Exception:
                    pass
                cls._scheduler = None
                cls._cleanup_manager = None
            raise
    
    @classmethod
    async def stop_scheduler(cls):
        """停止调度器"""
        if cls._scheduler:
            await cls._scheduler.stop()
            logger.info("Cache scheduler stopped")
        
    @classmethod
    def get_cache_system(cls) -> 'CacheSystem':
        return cls._cache_system
    
    @classmethod
    def get_task_manager(cls) -> TaskManager:
        if cls._task_manager is None:
            cls._task_manager = TaskManager()
        return cls._task_manager
    
    @classmethod
    def get_scheduler(cls):
        return cls._scheduler
    
    @classmethod
    def get_cleanup_manager(cls):
        return cls._cleanup_manager