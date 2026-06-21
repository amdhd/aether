from pydantic import BaseModel


class AccessToken(BaseModel):
    """Login/refresh responses return only the short-lived access token in the
    body. The refresh token is set as an HttpOnly cookie and never exposed to
    JavaScript."""

    access_token: str
    token_type: str = "bearer"
