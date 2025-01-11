from contextlib import asynccontextmanager

import fastapi
import httpx
import uvicorn

from .config import LOG_LEVEL
from .routes import redirect

# 使用上下文管理器，创建异步请求客户端
@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    app.state.requests_client = httpx.AsyncClient()
    yield
    await app.state.requests_client.aclose()

app = fastapi.FastAPI(lifespan=lifespan)

app.include_router(redirect.router)

if __name__ == "__main__":
    uvicorn.run(app, port=60001, host='0.0.0.0', log_level=LOG_LEVEL)