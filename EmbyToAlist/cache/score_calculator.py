# 修复循环导入的独立评分模块
import time
import math
from typing import Dict, Any
from loguru import logger

def calculate_cache_score(cache_record: Dict[str, Any]) -> float:
    """
    独立的分数计算函数，避免循环导入
    
    Args:
        cache_record: 缓存记录字典
        
    Returns:
        float: 计算得出的分数 (100-1000)
    """
    try:
        # 媒体类型因子
        item_type = cache_record.get('item_type', 'movie')
        if item_type == 'episode':
            media_factor = 1.4
            tvshows_info = cache_record.get('tvshows_info')
            if tvshows_info and isinstance(tvshows_info, dict):
                media_factor += 0.3
        else:
            media_factor = 1.0
        
        media_factor = max(0.8, min(1.8, media_factor))
        
        # 时间新鲜度因子
        current_time = time.time()
        last_read_time = cache_record.get('last_read_time', current_time)
        created_at = cache_record.get('created_at', current_time)
        
        hours_since_access = (current_time - last_read_time) / 3600
        days_since_creation = (current_time - created_at) / (24 * 3600)
        
        if item_type == 'episode':
            if hours_since_access <= 72:
                access_factor = max(0.8, 1.2 - hours_since_access / 180)
            else:
                access_factor = max(0.3, 0.8 - (hours_since_access - 72) / 600)
        else:
            if hours_since_access <= 48:
                access_factor = max(0.7, 1.1 - hours_since_access / 120)
            else:
                access_factor = max(0.2, 0.7 - (hours_since_access - 48) / 400)
        
        if days_since_creation <= 30:
            creation_factor = 1.0
        else:
            creation_factor = max(0.6, 1.0 - (days_since_creation - 30) / 120)
        
        freshness_factor = (access_factor * 0.75) + (creation_factor * 0.25)
        freshness_factor = max(0.2, min(1.8, freshness_factor))
        
        # 热度因子
        access_count = cache_record.get('access_count', 1)
        days_alive = max(0.1, days_since_creation)
        frequency = access_count / days_alive
        
        if item_type == 'episode':
            if frequency >= 2.0:
                popularity_factor = 1.6
            elif frequency >= 1.0:
                popularity_factor = 1.3
            elif frequency >= 0.3:
                popularity_factor = 1.0 + frequency * 1.5
            else:
                popularity_factor = max(0.4, 0.8 + frequency * 2.0)
        else:
            if frequency >= 0.5:
                popularity_factor = 1.4
            elif frequency >= 0.2:
                popularity_factor = 1.0 + frequency * 2.0
            elif frequency >= 0.05:
                popularity_factor = 0.7 + frequency * 4.0
            else:
                popularity_factor = max(0.3, 0.6 + frequency * 6.0)
        
        popularity_factor = max(0.3, min(1.8, popularity_factor))
        
        # 播放上下文因子
        in_progress = cache_record.get('in_progress', False)
        if in_progress:
            if item_type == 'episode':
                context_factor = 1.3
            else:
                context_factor = 1.1
        else:
            if item_type == 'episode':
                context_factor = 1.0
            else:
                context_factor = 0.9
        
        context_factor = max(0.7, min(1.4, context_factor))
        
        # 计算最终分数
        base_score = 400
        factor_product = media_factor * freshness_factor * popularity_factor * context_factor
        final_score = base_score * factor_product
        
        # 限制分数范围
        final_score = max(100, min(1000, final_score))
        
        return round(final_score, 1)
        
    except Exception as e:
        logger.error(f"Error calculating cache score: {e}")
        return 400.0  # 返回默认分数