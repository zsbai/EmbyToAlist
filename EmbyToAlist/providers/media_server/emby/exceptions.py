from typing import Optional

class EmbyError(Exception):
    pass

class EmbyHTTPError(EmbyError):
    def __init__(self, status_code: int, message: str, *, url: str, body_excerpt: Optional[str] = None):
        self.status_code = status_code
        self.url = url
        self.body_excerpt = body_excerpt
        super().__init__(f"[{status_code}] {message} url={url} body={body_excerpt}")

class EmbyNotFound(EmbyError):
    pass

class EmbySchemaError(EmbyError):
    pass
