import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import create_oauth_state_token, verify_oauth_state_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.integration import GoogleConnectResponse, GoogleStatusResponse
from app.services import google_oauth

router = APIRouter(prefix="/integrations/google", tags=["integrations"])


@router.get("/status", response_model=GoogleStatusResponse)
async def google_status(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GoogleStatusResponse:
    credential = await google_oauth.get_credential(db, current_user)
    return GoogleStatusResponse(connected=credential is not None)


@router.get("/connect", response_model=GoogleConnectResponse)
async def google_connect(current_user: User = Depends(get_current_user)) -> GoogleConnectResponse:
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET or not settings.GOOGLE_REDIRECT_URI:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Calendar integration is not configured.",
        )
    state = create_oauth_state_token(current_user.id)
    return GoogleConnectResponse(authorization_url=google_oauth.build_authorization_url(state))


@router.get("/callback", include_in_schema=False)
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    settings_url = f"{settings.FRONTEND_ORIGIN}/settings"

    if error or not code or not state:
        return RedirectResponse(f"{settings_url}?google=error")

    try:
        user_id = verify_oauth_state_token(state)
    except (JWTError, ValueError):
        return RedirectResponse(f"{settings_url}?google=error")

    user = await db.get(User, user_id)
    if user is None:
        return RedirectResponse(f"{settings_url}?google=error")

    try:
        token_data = await google_oauth.exchange_code(code)
        await google_oauth.upsert_credential(db, user, token_data)
    except (httpx.HTTPError, ValueError, KeyError):
        return RedirectResponse(f"{settings_url}?google=error")

    return RedirectResponse(f"{settings_url}?google=connected")


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def google_disconnect(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    await google_oauth.revoke_credential(db, current_user)
