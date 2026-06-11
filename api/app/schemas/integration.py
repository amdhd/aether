from pydantic import BaseModel


class GoogleStatusResponse(BaseModel):
    connected: bool


class GoogleConnectResponse(BaseModel):
    authorization_url: str
