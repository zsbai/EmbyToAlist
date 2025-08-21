from contextlib import asynccontextmanager

import fastapi
import uvicorn

from .config import LOG_LEVEL, CACHE_ENABLE, CACHE_PATH
from .routes import redirect, playback
from .utils.common import ClientManager
from .cache.manager import AppContext

# 使用上下文管理器，创建异步请求客户端
@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    ClientManager.init_client()
    if CACHE_ENABLE:
        AppContext.init(CACHE_PATH)
        # 启动缓存清理调度器
        await AppContext.start_scheduler()
    yield
    if CACHE_ENABLE:
        # 停止调度器
        await AppContext.stop_scheduler()
    await ClientManager.close_client()

app = fastapi.FastAPI(lifespan=lifespan)

app.include_router(redirect.router)
app.include_router(playback.router)

if __name__ == "__main__":
    uvicorn.run(app, port=60001, host='0.0.0.0', log_level=LOG_LEVEL)