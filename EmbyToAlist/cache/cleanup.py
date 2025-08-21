import asyncio
import shutil
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger

from ..config import (
    CACHE_ENABLE, CACHE_PATH, CACHE_MAX_SIZE_GB, CACHE_CLEANUP_THRESHOLD, 
    CACHE_CLEANUP_TARGET, CACHE_MIN_SCORE_THRESHOLD, CACHE_PROTECTION_DAYS,
    CACHE_SCORE_RECALC_HOURS, CACHE_SIZE_CHECK_HOURS, CACHE_WEEKLY_CLEANUP
)
from ..cache.storage.file_storage import FileStorage
from .score import CacheScoreManager
from .scheduler import CacheScheduler

class CacheCleanupManager:
    """缓存清理管理器"""
    
    def __init__(self, storage, scheduler: Optional[CacheScheduler] = None):
        self.storage: FileStorage = storage  # FileStorage实例
        self.score_manager = CacheScoreManager(storage)
        self.scheduler = scheduler
        
        # 清理配置 - 使用配置文件中的值
        self.max_cache_size_gb = CACHE_MAX_SIZE_GB
        self.cleanup_threshold_ratio = CACHE_CLEANUP_THRESHOLD
        self.cleanup_target_ratio = CACHE_CLEANUP_TARGET
        self.min_score_threshold = CACHE_MIN_SCORE_THRESHOLD
        self.protection_days = CACHE_PROTECTION_DAYS
        
    def register_cleanup_tasks(self):
        """注册定时清理任务"""
        if not self.scheduler:
            logger.warning("No scheduler provided, cleanup tasks not registered")
            return
            
        # 使用配置的间隔时间
        self.scheduler.register_task(
            "cache_size_check",
            self.check_and_cleanup,
            interval_seconds=CACHE_SIZE_CHECK_HOURS * 3600,
            run_immediately=False
        )
        
        # 每天重新计算一次分数
        self.scheduler.register_task(
            "recalculate_scores", 
            self.score_manager.recalculate_all_scores,
            interval_seconds=CACHE_SCORE_RECALC_HOURS * 3600,
            run_immediately=True
        )
        
        # 可选：每周清理一次低分缓存 (默认关闭，因为缓存应该长期保留)
        # 只有在配置明确启用时才会执行强制清理
        if CACHE_WEEKLY_CLEANUP:
            logger.warning("Weekly cleanup is enabled - this may remove valuable long-term cache")
            self.scheduler.register_task(
                "weekly_cleanup",
                self.force_cleanup_low_scores,
                interval_seconds=7 * 24 * 3600,  # 7天
                run_immediately=False
            )
        else:
            logger.info("Weekly cleanup disabled - cache will be preserved for long-term value")
        
        logger.info("Cache cleanup tasks registered with scheduler")
    
    async def get_cache_size_info(self) -> Dict[str, Any]:
        """获取缓存大小信息"""
        self.storage.db.set_table('system_info')
        system_info = self.storage.db.get_all()
        
        if system_info:
            cache_size_bytes = system_info[0].get('cache_size', 0)
            cache_count = system_info[0].get('cache_count', 0)
        else:
            cache_size_bytes = 0 
            cache_count = 0
        
        cache_size_gb = cache_size_bytes / (1024 ** 3)
        threshold_gb = self.max_cache_size_gb * self.cleanup_threshold_ratio
        target_gb = self.max_cache_size_gb * self.cleanup_target_ratio
        
        return {
            'current_size_gb': round(cache_size_gb, 2),
            'current_size_bytes': cache_size_bytes,
            'cache_count': cache_count,
            'max_size_gb': self.max_cache_size_gb,
            'threshold_gb': round(threshold_gb, 2),
            'target_gb': round(target_gb, 2),
            'usage_ratio': round(cache_size_gb / self.max_cache_size_gb, 3),
            'needs_cleanup': cache_size_gb >= threshold_gb
        }
    
    async def check_and_cleanup(self):
        """检查缓存大小并根据需要清理"""
        if not CACHE_ENABLE:
            logger.debug("Cache not enabled, skipping cleanup check")
            return
            
        cache_info = await self.get_cache_size_info()
        logger.info(f"Cache size check: {cache_info['current_size_gb']}GB / {cache_info['max_size_gb']}GB "
                   f"({cache_info['usage_ratio']*100:.1f}%)")
        
        if cache_info['needs_cleanup']:
            logger.info(f"Cache size ({cache_info['current_size_gb']}GB) exceeds threshold "
                       f"({cache_info['threshold_gb']}GB), starting cleanup")
            
            target_reduction_gb = cache_info['current_size_gb'] - cache_info['target_gb']
            await self.cleanup_by_size(target_reduction_gb)
        else:
            logger.debug("Cache size within limits, no cleanup needed")
    
    async def cleanup_by_size(self, target_reduction_gb: float) -> Dict[str, Any]:
        """按目标大小清理缓存"""
        logger.info(f"Starting size-based cleanup, target reduction: {target_reduction_gb:.2f}GB")
        
        # 先重新计算所有分数
        await self.score_manager.recalculate_all_scores()
        
        # 获取清理候选项（按分数排序）
        candidates = self._get_cleanup_candidates_by_size()
        
        cleaned_size_gb = 0
        cleaned_count = 0
        target_reduction_bytes = target_reduction_gb * (1024 ** 3)
        cleaned_bytes = 0
        
        for candidate in candidates:
            if cleaned_bytes >= target_reduction_bytes:
                break
                
            try:
                removed_size = await self._remove_cache_entry(candidate)
                cleaned_bytes += removed_size
                cleaned_size_gb += removed_size / (1024 ** 3)
                cleaned_count += 1
                
                logger.info(f"Removed cache {candidate['path']} "
                           f"(score: {candidate.get('score', 0)}, "
                           f"size: {removed_size/(1024**2):.1f}MB)")
                
            except Exception as e:
                logger.error(f"Error removing cache {candidate['path']}: {e}")
        
        result = {
            'cleaned_count': cleaned_count,
            'cleaned_size_gb': round(cleaned_size_gb, 2),
            'target_reduction_gb': target_reduction_gb,
            'success': cleaned_bytes >= target_reduction_bytes * 0.8  # 80%达标算成功
        }
        
        logger.info(f"Cleanup completed: removed {cleaned_count} entries, "
                   f"freed {cleaned_size_gb:.2f}GB")
        
        return result
    
    async def force_cleanup_low_scores(self):
        """强制清理低分缓存（定时任务）"""
        logger.info("Starting forced cleanup of low-score caches")
        
        candidates = self.score_manager.get_cleanup_candidates(
            min_score_threshold=self.min_score_threshold
        )
        
        # 保护最近访问的文件
        protected_candidates = []
        cleanup_candidates = []
        
        current_time = time.time()
        protection_seconds = self.protection_days * 24 * 3600
        
        for candidate in candidates:
            last_read = candidate.get('last_read_time', 0)
            if (current_time - last_read) < protection_seconds:
                protected_candidates.append(candidate)
            else:
                cleanup_candidates.append(candidate)
        
        logger.info(f"Found {len(cleanup_candidates)} low-score candidates for cleanup, "
                   f"{len(protected_candidates)} protected by recent access")
        
        cleaned_count = 0
        cleaned_size_gb = 0
        
        for candidate in cleanup_candidates:
            try:
                removed_size = await self._remove_cache_entry(candidate)
                cleaned_size_gb += removed_size / (1024 ** 3)
                cleaned_count += 1
                
            except Exception as e:
                logger.error(f"Error removing low-score cache {candidate['path']}: {e}")
        
        logger.info(f"Low-score cleanup completed: removed {cleaned_count} entries, "
                   f"freed {cleaned_size_gb:.2f}GB")
    
    def _get_cleanup_candidates_by_size(self) -> List[Dict[str, Any]]:
        """按大小和分数获取清理候选项"""
        self.storage.db.set_table('cache_files')
        all_records = self.storage.db.get_all()
        
        import time
        current_time = time.time()
        protection_seconds = self.protection_days * 24 * 3600
        
        # 筛选可清理的缓存（排除受保护的）
        candidates = []
        for record in all_records:
            last_read = record.get('last_read_time', 0)
            
            # 跳过最近访问的
            if (current_time - last_read) < protection_seconds:
                continue
                
            candidates.append(record)
        
        # 按优先级排序：分数低的优先，同分数下大文件优先
        candidates.sort(key=lambda x: (x.get('score', 0), -x.get('size', 0)))
        
        return candidates
    
    async def _remove_cache_entry(self, cache_record: Dict[str, Any]) -> int:
        """移除单个缓存条目（目录+数据库记录）"""
        cache_path = Path(cache_record['path'])
        original_size = cache_record.get('size', 0)
        
        # 删除缓存目录
        if cache_path.exists() and cache_path.is_dir():
            shutil.rmtree(cache_path)
            logger.debug(f"Removed cache directory: {cache_path}")
        
        # 删除数据库记录
        self.storage.db.set_table('cache_files')
        self.storage.db.delete(
            condition=lambda q: q.path == str(cache_path)
        )
        
        # 更新系统统计
        await self._update_system_stats_after_removal(original_size)
        
        return original_size
    
    async def _update_system_stats_after_removal(self, removed_size: int):
        """删除后更新系统统计"""
        async with self.storage.db_lock:
            self.storage.db.set_table('system_info')
            
            def update_stats(doc):
                return {
                    'cache_size': max(0, doc.get('cache_size', 0) - removed_size),
                    'cache_count': max(0, doc.get('cache_count', 0) - 1)
                }
            
            self.storage.db.update(
                fields=update_stats,
                condition=lambda q: q.version == self.storage.version
            )
    
    async def manual_cleanup(self, 
                           target_size_gb: Optional[float] = None,
                           min_score: Optional[float] = None) -> Dict[str, Any]:
        """手动清理接口"""
        logger.info("Starting manual cache cleanup")
        
        if target_size_gb:
            return await self.cleanup_by_size(target_size_gb)
        elif min_score:
            # 按分数清理
            candidates = self.score_manager.get_cleanup_candidates(
                min_score_threshold=min_score
            )
            
            cleaned_count = 0
            cleaned_size_gb = 0
            
            for candidate in candidates:
                try:
                    removed_size = await self._remove_cache_entry(candidate)
                    cleaned_size_gb += removed_size / (1024 ** 3)
                    cleaned_count += 1
                except Exception as e:
                    logger.error(f"Error in manual cleanup: {e}")
            
            return {
                'cleaned_count': cleaned_count,
                'cleaned_size_gb': round(cleaned_size_gb, 2),
                'method': 'score_threshold'
            }
        else:
            # 默认按当前设置清理
            cache_info = await self.get_cache_size_info()
            if cache_info['needs_cleanup']:
                target_reduction = cache_info['current_size_gb'] - cache_info['target_gb']
                return await self.cleanup_by_size(target_reduction)
            else:
                return {
                    'cleaned_count': 0,
                    'cleaned_size_gb': 0,
                    'message': 'No cleanup needed'
                }
    
    async def get_cleanup_preview(self, 
                                target_size_gb: Optional[float] = None) -> Dict[str, Any]:
        """预览清理操作（不实际删除）"""
        candidates = self._get_cleanup_candidates_by_size()
        
        if target_size_gb:
            target_bytes = target_size_gb * (1024 ** 3)
            preview_bytes = 0
            preview_count = 0
            
            for candidate in candidates:
                if preview_bytes >= target_bytes:
                    break
                preview_bytes += candidate.get('size', 0)
                preview_count += 1
        else:
            preview_count = len(candidates)
            preview_bytes = sum(c.get('size', 0) for c in candidates)
        
        return {
            'candidates_count': preview_count,
            'total_size_gb': round(preview_bytes / (1024 ** 3), 2),
            'candidates': candidates[:10]  # 只返回前10个作为示例
        }