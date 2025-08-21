from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import time
import math
from loguru import logger

class ScoreFactor(ABC):
    """评分因子基类"""
    
    def __init__(self, name: str, weight: float = 1.0):
        self.name = name
        self.weight = weight
    
    @abstractmethod
    def calculate(self, cache_record: Dict[str, Any]) -> float:
        """计算因子分数 (0.1 - 2.0)"""
        pass
    
    def clamp(self, value: float, min_val: float = 0.1, max_val: float = 2.0) -> float:
        """限制值在有效范围内"""
        return max(min_val, min(max_val, value))

class MediaTypeFactor(ScoreFactor):
    """媒体类型因子 - 剧集优于电影"""
    
    def __init__(self, weight: float = 1.0):
        super().__init__("media_type", weight)
        
    def calculate(self, cache_record: Dict[str, Any]) -> float:
        item_type = cache_record.get('item_type', 'movie')
        
        if item_type == 'episode':
            base_score = 1.4  # 剧集基础权重更高
            
            # 连续剧集加成
            tvshows_info = cache_record.get('tvshows_info')
            if tvshows_info and isinstance(tvshows_info, dict):
                # 如果有剧集信息，说明是连续观看，命中率更高
                base_score += 0.3
                
        else:  # movie
            base_score = 1.0
        
        return self.clamp(base_score, 0.8, 1.8)

class FreshnessFactor(ScoreFactor):
    """时间新鲜度因子"""
    
    def __init__(self, weight: float = 1.0):
        super().__init__("freshness", weight)
        
    def calculate(self, cache_record: Dict[str, Any]) -> float:
        current_time = time.time()
        last_read_time = cache_record.get('last_read_time', current_time)
        created_at = cache_record.get('created_at', current_time)
        
        hours_since_access = (current_time - last_read_time) / 3600
        days_since_creation = (current_time - created_at) / (24 * 3600)
        
        item_type = cache_record.get('item_type', 'movie')
        
        if item_type == 'episode':
            # 剧集：连续观看模式，衰减较慢
            if hours_since_access <= 72:  # 3天内
                access_factor = max(0.8, 1.2 - hours_since_access / 180)
            else:
                access_factor = max(0.3, 0.8 - (hours_since_access - 72) / 600)
        else:
            # 电影：观看间隔更长，但衰减更快
            if hours_since_access <= 48:  # 2天内
                access_factor = max(0.7, 1.1 - hours_since_access / 120)
            else:
                access_factor = max(0.2, 0.7 - (hours_since_access - 48) / 400)
        
        # 创建时间因子（避免过度累积老旧缓存）
        if days_since_creation <= 30:
            creation_factor = 1.0
        else:
            creation_factor = max(0.6, 1.0 - (days_since_creation - 30) / 120)
        
        combined_score = (access_factor * 0.75) + (creation_factor * 0.25)
        return self.clamp(combined_score, 0.2, 1.8)

class PopularityFactor(ScoreFactor):
    """热度因子 - 基于访问频率"""
    
    def __init__(self, weight: float = 1.0):
        super().__init__("popularity", weight)
        
    def calculate(self, cache_record: Dict[str, Any]) -> float:
        access_count = cache_record.get('access_count', 1)
        created_at = cache_record.get('created_at', time.time())
        
        days_alive = max(0.1, (time.time() - created_at) / (24 * 3600))
        frequency = access_count / days_alive
        
        item_type = cache_record.get('item_type', 'movie')
        
        if item_type == 'episode':
            # 剧集访问模式：短期内多次访问
            if frequency >= 2.0:  # 每天2+次
                popularity = 1.6
            elif frequency >= 1.0:  # 每天1次
                popularity = 1.3
            elif frequency >= 0.3:  # 每3天1次
                popularity = 1.0 + frequency * 1.5
            else:
                popularity = max(0.4, 0.8 + frequency * 2.0)
        else:
            # 电影访问模式：频率较低但有重看价值
            if frequency >= 0.5:  # 每2天1次算很高
                popularity = 1.4
            elif frequency >= 0.2:  # 每5天1次
                popularity = 1.0 + frequency * 2.0
            elif frequency >= 0.05:  # 每20天1次
                popularity = 0.7 + frequency * 4.0
            else:
                popularity = max(0.3, 0.6 + frequency * 6.0)
        
        return self.clamp(popularity, 0.3, 1.8)

class PlaybackContextFactor(ScoreFactor):
    """播放上下文因子"""
    
    def __init__(self, weight: float = 1.0):
        super().__init__("playback_context", weight)
        
    def calculate(self, cache_record: Dict[str, Any]) -> float:
        in_progress = cache_record.get('in_progress', False)
        item_type = cache_record.get('item_type', 'movie')
        
        base_score = 1.0
        
        if in_progress:
            # 正在观看的内容
            if item_type == 'episode':
                base_score = 1.3  # 剧集进行中，后续集数概率高
            else:
                base_score = 1.1  # 电影进行中，重看概率中等
        else:
            # 未开始或已完成
            if item_type == 'episode':
                base_score = 1.0  # 剧集有连续性
            else:
                base_score = 0.9  # 电影重看概率稍低
        
        return self.clamp(base_score, 0.7, 1.4)

class CacheScoreCalculator:
    """缓存评分计算器"""
    
    def __init__(self):
        self.factors = [
            MediaTypeFactor(weight=1.0),
            FreshnessFactor(weight=1.0),
            PopularityFactor(weight=1.0),
            PlaybackContextFactor(weight=1.0)
        ]
        self.base_score = 400  # 所有因子为1.0时的基准分数
        
    def calculate_score(self, cache_record: Dict[str, Any]) -> float:
        """计算缓存目录的综合分数"""
        factor_product = 1.0
        
        for factor in self.factors:
            try:
                factor_score = factor.calculate(cache_record)
                factor_product *= factor_score
                logger.debug(f"Factor {factor.name}: {factor_score}")
            except Exception as e:
                logger.warning(f"Error calculating {factor.name}: {e}")
                factor_product *= 1.0  # 使用默认值
        
        final_score = self.base_score * factor_product
        
        # 限制分数范围：100-1000
        final_score = max(100, min(1000, final_score))
        
        logger.debug(f"Cache score calculated: {final_score} (factor_product: {factor_product})")
        return round(final_score, 1)
    
    def explain_score(self, cache_record: Dict[str, Any]) -> Dict[str, Any]:
        """解释分数计算过程"""
        explanation = {
            'total_score': 0,
            'base_score': self.base_score,
            'factors': {}
        }
        
        factor_product = 1.0
        
        for factor in self.factors:
            factor_score = factor.calculate(cache_record)
            factor_product *= factor_score
            explanation['factors'][factor.name] = {
                'score': factor_score,
                'weight': factor.weight
            }
        
        explanation['total_score'] = round(self.base_score * factor_product, 1)
        explanation['factor_product'] = round(factor_product, 3)
        
        return explanation

class CacheScoreManager:
    """缓存评分管理器"""
    
    def __init__(self, storage):
        self.calculator = CacheScoreCalculator()
        self.storage = storage  # FileStorage实例
        
    async def calculate_and_update_score(self, cache_record: Dict[str, Any]) -> float:
        """计算并更新分数到数据库"""
        new_score = self.calculator.calculate_score(cache_record)
        
        # 更新数据库中的分数
        self.storage.db.set_table('cache_files')
        self.storage.db.update(
            fields={'score': new_score},
            condition=lambda q: q.path == cache_record['path']
        )
        
        return new_score
    
    async def recalculate_all_scores(self):
        """重新计算所有缓存的分数"""
        logger.info("Starting to recalculate all cache scores")
        
        try:
            self.storage.db.set_table('cache_files')
            all_records = self.storage.db.get_all()
            
            if not all_records:
                logger.info("No cache records found to recalculate")
                return 0
            
            updated_count = 0
            failed_count = 0
            
            # 使用独立计算函数避免循环引用
            from .score_calculator import calculate_cache_score
            
            for record in all_records:
                try:
                    # 验证记录完整性
                    if not record.get('path'):
                        logger.warning(f"Invalid record found: missing path")
                        failed_count += 1
                        continue
                        
                    new_score = calculate_cache_score(record)
                    
                    # 更新数据库
                    updated_rows = self.storage.db.update(
                        fields={'score': new_score},
                        condition=lambda q: q.path == record['path']
                    )
                    
                    if updated_rows:
                        logger.debug(f"Updated score for {record['path']}: {new_score}")
                        updated_count += 1
                    else:
                        logger.warning(f"Failed to update score for {record['path']}: record not found")
                        failed_count += 1
                        
                except Exception as e:
                    logger.error(f"Error updating score for {record.get('path', 'unknown')}: {e}")
                    failed_count += 1
            
            logger.info(f"Recalculated scores for {updated_count} cache entries, {failed_count} failed")
            return updated_count
            
        except Exception as e:
            logger.error(f"Critical error in recalculate_all_scores: {e}")
            return 0
    
    def get_cleanup_candidates(self, 
                             min_score_threshold: float = 200,
                             max_candidates: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取清理候选项"""
        self.storage.db.set_table('cache_files')
        all_records = self.storage.db.get_all()
        
        # 筛选低分缓存
        candidates = [
            record for record in all_records 
            if record.get('score', 0) < min_score_threshold
        ]
        
        # 按分数排序（最低分先清理）
        candidates.sort(key=lambda x: x.get('score', 0))
        
        if max_candidates:
            candidates = candidates[:max_candidates]
            
        logger.info(f"Found {len(candidates)} cleanup candidates with score < {min_score_threshold}")
        return candidates