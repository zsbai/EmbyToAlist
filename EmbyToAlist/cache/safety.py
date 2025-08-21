"""
缓存系统的安全检查和边界条件处理
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

def validate_cache_record(record: Dict[str, Any]) -> bool:
    """
    验证缓存记录的完整性
    
    Args:
        record: 缓存记录字典
        
    Returns:
        bool: 记录是否有效
    """
    required_fields = ['path', 'size', 'created_at', 'last_read_time']
    
    for field in required_fields:
        if field not in record:
            logger.warning(f"Missing required field: {field}")
            return False
    
    # 验证路径存在性
    cache_path = Path(record['path'])
    if not cache_path.exists():
        logger.warning(f"Cache path does not exist: {cache_path}")
        return False
    
    # 验证数值合理性
    if record['size'] < 0:
        logger.warning(f"Invalid cache size: {record['size']}")
        return False
    
    if record['created_at'] <= 0 or record['last_read_time'] <= 0:
        logger.warning(f"Invalid timestamp in record")
        return False
    
    return True

def safe_path_operation(cache_path: Path, operation: str) -> bool:
    """
    安全的路径操作，防止路径遍历攻击
    
    Args:
        cache_path: 缓存路径
        operation: 操作类型 ('read', 'write', 'delete')
        
    Returns:
        bool: 操作是否安全
    """
    try:
        # 解析绝对路径
        abs_path = cache_path.resolve()
        
        # 检查是否在允许的缓存目录内
        from ..config import CACHE_PATH
        cache_root = Path(CACHE_PATH).resolve()
        
        if not str(abs_path).startswith(str(cache_root)):
            logger.error(f"Path traversal attempt detected: {abs_path}")
            return False
        
        # 检查路径长度（防止过长路径攻击）
        if len(str(abs_path)) > 1000:
            logger.error(f"Path too long: {len(str(abs_path))} chars")
            return False
        
        # 检查文件名中的危险字符
        dangerous_chars = ['..', '<', '>', '|', '"', '?', '*']
        path_str = str(abs_path)
        
        for char in dangerous_chars:
            if char in path_str:
                logger.error(f"Dangerous character '{char}' in path: {abs_path}")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"Error validating path {cache_path}: {e}")
        return False

def check_disk_space(required_gb: float) -> bool:
    """
    检查磁盘空间是否足够
    
    Args:
        required_gb: 需要的空间(GB)
        
    Returns:
        bool: 空间是否足够
    """
    try:
        from ..config import CACHE_PATH
        cache_path = Path(CACHE_PATH)
        
        # 确保目录存在
        cache_path.mkdir(parents=True, exist_ok=True)
        
        # 获取磁盘使用情况
        statvfs = os.statvfs(cache_path)
        
        # 计算可用空间
        free_bytes = statvfs.f_frsize * statvfs.f_bavail
        free_gb = free_bytes / (1024 ** 3)
        
        # 保留10%的安全边距
        usable_gb = free_gb * 0.9
        
        if usable_gb < required_gb:
            logger.warning(f"Insufficient disk space: need {required_gb}GB, available {usable_gb:.2f}GB")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Error checking disk space: {e}")
        return False

def sanitize_cache_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    清理和标准化缓存记录
    
    Args:
        record: 原始记录
        
    Returns:
        Dict: 清理后的记录
    """
    sanitized = {}
    
    # 安全的字段复制
    safe_fields = {
        'path': str,
        'size': int,
        'created_at': float,
        'last_read_time': float,
        'score': float,
        'access_count': int,
        'item_type': str,
        'in_progress': bool,
        'item_id': int,
        'etag': (str, type(None)),
        'last_modified': (str, type(None)),
        'content_disposition': (str, type(None)),
        'tvshows_info': (dict, type(None))
    }
    
    for field, expected_type in safe_fields.items():
        if field in record:
            value = record[field]
            
            # 类型检查
            if isinstance(expected_type, tuple):
                if not isinstance(value, expected_type):
                    logger.warning(f"Invalid type for {field}: expected {expected_type}, got {type(value)}")
                    continue
            else:
                if not isinstance(value, expected_type):
                    try:
                        # 尝试类型转换
                        value = expected_type(value)
                    except (ValueError, TypeError):
                        logger.warning(f"Cannot convert {field} to {expected_type}")
                        continue
            
            sanitized[field] = value
        else:
            # 提供默认值
            defaults = {
                'access_count': 1,
                'score': 400.0,
                'item_type': 'movie',
                'in_progress': False,
                'item_id': 0,
                'etag': None,
                'last_modified': None,
                'content_disposition': None,
                'tvshows_info': None
            }
            
            if field in defaults:
                sanitized[field] = defaults[field]
    
    return sanitized

def emergency_cache_cleanup(cache_root: Path, max_attempts: int = 100) -> int:
    """
    紧急缓存清理，当正常清理失败时使用
    
    Args:
        cache_root: 缓存根目录
        max_attempts: 最大清理尝试次数
        
    Returns:
        int: 清理的文件数量
    """
    cleaned_count = 0
    
    try:
        import time
        import shutil
        
        # 获取所有缓存目录，按修改时间排序
        cache_dirs = []
        
        for item in cache_root.rglob("*"):
            if item.is_dir() and item != cache_root:
                try:
                    mtime = item.stat().st_mtime
                    cache_dirs.append((mtime, item))
                except OSError:
                    continue
        
        # 按修改时间排序（最老的先删除）
        cache_dirs.sort(key=lambda x: x[0])
        
        current_time = time.time()
        
        for i, (mtime, cache_dir) in enumerate(cache_dirs):
            if i >= max_attempts:
                break
            
            try:
                # 只删除超过7天未修改的目录
                if current_time - mtime > 7 * 24 * 3600:
                    if safe_path_operation(cache_dir, 'delete'):
                        shutil.rmtree(cache_dir)
                        cleaned_count += 1
                        logger.info(f"Emergency cleanup: removed {cache_dir}")
                
            except Exception as e:
                logger.error(f"Error in emergency cleanup for {cache_dir}: {e}")
        
        return cleaned_count
        
    except Exception as e:
        logger.error(f"Critical error in emergency cleanup: {e}")
        return 0