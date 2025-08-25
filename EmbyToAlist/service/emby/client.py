import asyncio
import json
import httpx
from loguru import logger
from typing import Any, Optional

from ...config import EMBY_SERVER
from .exceptions import EmbyHTTPError

class EmbyClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 10.0,
    ):
        """
        Emby API Client
        
        Args:
            base_url: Emby服务器的基础URL，例如 "http://localhost:8096"
            api_key: Emby API密钥
            client: 可选的httpx.AsyncClient实例，如果未提供则会创建一个新的
            timeout: 请求超时时间，单位为秒
        """
        self.base_url = (base_url or EMBY_SERVER).rstrip("/")
        self.api_key = api_key
        self._ext_client = client
        self._own_client = client is None
        if self._own_client:
            self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        else:
            self._client = client

    async def aclose(self):
        if self._own_client:
            await self._client.aclose()

    async def _retry(self, func, *, retries=2, retry_for=(httpx.TransportError,), backoff_base=0.3):
        last_exc = None
        for attempt in range(retries + 1):
            try:
                return await func()
            except retry_for as e:
                last_exc = e
                if attempt == retries:
                    raise
                await asyncio.sleep(backoff_base * (2 ** attempt))
        raise last_exc  # pragma: no cover

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        json_body: Any | None = None,
        content: Any | None = None,
        allow_retry: bool = True,
    ) -> httpx.Response:
        """
        发送HTTP请求到Emby服务器
        
        Args:
            method: HTTP方法，例如 "GET", "POST"
            path: API路径，例如 "/emby/Items"
            params: 查询参数字典
            headers: 额外的HTTP头字典
            json_body: 如果提供，将作为JSON体发送
            content: 如果提供，将作为原始内容体发送
            allow_retry: 是否允许在GET请求时重试
        Returns:
            httpx.Response对象
        Raises:
            EmbyHTTPError: 当响应状态码为4xx或5xx时抛出
        """
        # 构造查询参数并去掉值为 None 的键
        q = {k: v for k, v in (params or {}).items() if v is not None}
        # 统一注入 api_key（如果可用且未提供）
        if self.api_key and "api_key" not in q:
            q["api_key"] = self.api_key

        url = f"{self.base_url}{path}"
        log = logger.bind(component="emby", method=method, path=path, q_size=len(q or {}))
        log.debug(f"→ Request {method} {url} params={list(q.keys())[:6]}...")

        async def _do():
            # 仅当提供了 json_body 时传 json，否则传 content（若有）
            kwargs: dict = {"params": q, "headers": headers}
            if json_body is not None:
                kwargs["json"] = json_body
            elif content is not None:
                kwargs["content"] = content
            resp = await self._client.request(method, url, **kwargs)
            if resp.status_code >= 400:
                excerpt = None
                try:
                    text = resp.text
                    excerpt = text[:500]
                except Exception:
                    pass
                raise EmbyHTTPError(resp.status_code, "Emby API error", url=str(resp.request.url), body_excerpt=excerpt)
            return resp

        resp = await (self._retry(_do) if (allow_retry and method.upper() == "GET") else _do())
        log.debug(f"← Response {resp.status_code} {url}")
        return resp

    async def json(self, *a, **kw) -> dict:
        resp = await self.request(*a, **kw)
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise EmbyHTTPError(resp.status_code, f"Invalid JSON: {e}", url=str(resp.request.url))
    
    # 供反代使用：上层可直接拿到 Response 或者 aiter_bytes()
    async def raw_request(self, *a, **kw) -> httpx.Response:
        return await self.request(*a, **kw)

    async def stream_request(self, method: str, path: str, **kw):
        # 返回一个异步迭代器（aiter_bytes），用于大文件/视频流透传
        resp = await self.request(method, path, **kw)
        return resp.aiter_bytes()

    # 便捷 JSON 方法
    async def get_json(self, path: str, *, params: dict | None = None, headers: dict | None = None) -> dict:
        return await self.json("GET", path, params=params, headers=headers)

    async def post_json(self, path: str, *, params: dict | None = None, headers: dict | None = None, json_body: Any | None = None, content: Any | None = None) -> dict:
        return await self.json("POST", path, params=params, headers=headers, json_body=json_body, content=content)
