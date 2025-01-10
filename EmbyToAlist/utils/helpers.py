import re

import fastapi
from uvicorn.server import logger

# a wrapper function to get the time of the function
def get_time(func):
    def wrapper(*args, **kwargs):
        import time
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger.info(f"Function {func.__name__} takes: {end - start} seconds")
        return result
    return wrapper

def get_content_type(container) -> str:
    """文件格式对应的Content-Type映射"""
    content_types = {
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'ogg': 'video/ogg',
        'avi': 'video/x-msvideo',
        'mpeg': 'video/mpeg',
        'mov': 'video/quicktime',
        'mkv': 'video/x-matroska',
        'ts': 'video/mp2t',
    }

    # 返回对应的Content-Type，如果未找到，返回一个默认值
    return content_types.get(container.lower(), 'application/octet-stream')

def extract_api_key(request: fastapi.Request):
    """从请求中提取API密钥"""
    api_key = request.query_params.get('api_key') or request.query_params.get('X-Emby-Token')
    if not api_key:
        auth_header = request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                api_key = match_token.group(1)
    return api_key

def validate_regex(word: str) -> bool:
    """
    验证用户输入是否为有效的正则表达式
    """
    try:
        re.compile(word)
        return True
    except re.error:
        return False

def match_with_regex(pattern, target_string):
    """
    使用正则表达式匹配目标字符串
    """
    if validate_regex(pattern):
        match = re.search(pattern, target_string)
        if match:
            return True
        else:
            return False
    else:
        raise ValueError("Invalid regex pattern")