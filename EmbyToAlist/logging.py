import sys

import logging
from loguru import logger

from .config import LOG_LEVEL

class InterceptHandler(logging.Handler):
    def emit(self, record):
        # 获取对应的 Loguru 级别
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 获取调用日志的真正位置（去掉 logging 层）
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )
        
def setup_logging():
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss,SSSZZ}</green> - "
        "<level>{level: <8}</level> - "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> - "
        "{message}",
        level=LOG_LEVEL,
    )