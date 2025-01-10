from httpx import AsyncClient, ReadTimeout
from fastapi import HTTPException
from uvicorn.server import logger

from ..config import ALIST_SERVER, ALIST_API_KEY

async def get_alist_raw_url(file_path, ua, client: AsyncClient) -> str:
    """根据文件路径获取Alist Raw Url"""
    
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
    
    if code == 200: return req['data']['raw_url']
               
    elif code == 403:
        logger.error("Alist server response 403 Forbidden, Please check your Alist Key")
        # return 403, '403 Forbidden, Please check your Alist Key'
        raise HTTPException(status_code=500, detail="Alist return 403 Forbidden, Please check your Alist Key")
    else:
        logger.error(f"Error: {req['message']}")
        # return 500, req['message']        
        raise HTTPException(status_code=500, detail="Alist Server Error")