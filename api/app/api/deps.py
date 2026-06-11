from typing import Protocol, TypeVar

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id = payload.get("sub")
        token_version = payload.get("ver")
        if user_id is None or token_version is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = await db.get(User, int(user_id))
    if user is None or user.token_version != token_version:
        raise credentials_exception
    return user


class _OwnedModel(Protocol):
    user_id: int


ModelT = TypeVar("ModelT", bound=_OwnedModel)


async def get_owned_or_404(db: AsyncSession, model: type[ModelT], obj_id: int, user: User, detail: str) -> ModelT:
    """Fetch a row by primary key and verify it belongs to `user`, raising a
    404 (not 403, to avoid leaking whether the id exists for another user) if
    it doesn't exist or isn't owned by them."""
    obj = await db.get(model, obj_id)
    if obj is None or obj.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return obj
