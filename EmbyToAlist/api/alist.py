from httpx import AsyncClient, ReadTimeout
from fastapi import HTTPException
from loguru import logger

from ..config import ALIST_SERVER, ALIST_API_KEY
from ..utils.network import ClientManager

# return Alist Raw Url
async def get_alist_raw_url(file_path: str, ua: str) -> str:
    """创建或获取Alist Raw Url缓存，缓存时间为5分钟

    Args:
        file_path (str): Alist中的文件路径
        ua (str): 请求头中的User-Agent，用于适配115等需要验证UA的网站
        client (AsyncClient): HTTPX异步请求客户端

    Returns:
        str: Alist Raw Url
    """
    client = ClientManager.get_client()
    alist_api_url = f"{ALIST_SERVER}/api/fs/get"

    body = {
        "path": file_path,
        "password": ""
    }
    header = {
        "Authorization": ALIST_API_KEY,
        "Content-Type": "application/json;charset=UTF-8"
    }
    
    if ua is not None:
        header['User-Agent'] = ua
    
    try:
        req = await client.post(alist_api_url, json=body, headers=header)
        req.raise_for_status()
        req = req.json()
    except ReadTimeout as e:
        logger.error(f"Error: get_alist_raw_url failed, {e}")
        raise HTTPException(status_code=500, detail="Alist Server Timeout")
    except Exception as e:
        logger.error(f"Error: get_alist_raw_url failed, {e}")
        logger.error(f"Error: {req.text}")
        raise HTTPException(status_code=500, detail="Alist Server Error")
    
    code = req['code']
    
    if code == 200: 
        logger.debug(f"Alist Raw Url: {req['data']['raw_url']}")
        return req['data']['raw_url']
               
    elif code == 403:
        logger.error("Alist server response 403 Forbidden, Please check your Alist Key")
        # return 403, '403 Forbidden, Please check your Alist Key'
        raise HTTPException(status_code=500, detail="Alist return 403 Forbidden, Please check your Alist Key")
    else:
        logger.error(f"Error: {req['message']}")
        # return 500, req['message']        
        raise HTTPException(status_code=500, detail="Alist Server Error")