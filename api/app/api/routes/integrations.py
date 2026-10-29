import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from jwt import InvalidTokenError as JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import create_oauth_state_token, verify_oauth_state_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.integration import GoogleConnectResponse, GoogleStatusResponse
from app.services import google_oauth

OAUTH_COOKIE = "google_oauth_state"

router = APIRouter(prefix="/integrations/google", tags=["integrations"])


@router.get("/status", response_model=GoogleStatusResponse)
async def google_status(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GoogleStatusResponse:
    credential = await google_oauth.get_credential(db, current_user)
    return GoogleStatusResponse(connected=credential is not None)


@router.get("/connect", response_model=GoogleConnectResponse)
async def google_connect(response: Response, current_user: User = Depends(get_current_user)) -> GoogleConnectResponse:
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET or not settings.GOOGLE_REDIRECT_URI:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Calendar integration is not configured.",
        )
    state = create_oauth_state_token(current_user.id)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        OAUTH_COOKIE, state, httponly=True,
        secure=settings.ENVIRONMENT == "production" or settings.REFRESH_COOKIE_SECURE,
        samesite="none" if settings.REFRESH_COOKIE_SAMESITE == "none" else "lax",
        max_age=settings.OAUTH_STATE_EXPIRE_MINUTES * 60,
        path=f"{settings.API_V1_PREFIX}/integrations/google",
    )
    return GoogleConnectResponse(authorization_url=google_oauth.build_authorization_url(state))


@router.get("/callback", include_in_schema=False)
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    origin = settings.FRONTEND_ORIGIN.split(",")[0].strip().rstrip("/")
    settings_url = f"{origin}/settings"

    def redirect(result: str) -> RedirectResponse:
        response = RedirectResponse(f"{settings_url}?google={result}")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.delete_cookie(OAUTH_COOKIE, path=f"{settings.API_V1_PREFIX}/integrations/google")
        return response

    # A signed state identifies an account, but does not prove this browser
    # initiated the flow. Reject forwarded authorization links before exchange.
    browser_state = request.cookies.get(OAUTH_COOKIE, "")
    if not state or not browser_state or not secrets.compare_digest(
        state.encode("utf-8"), browser_state.encode("utf-8")
    ):
        return redirect("error")

    if error or not code or not state:
        return redirect("error")

    try:
        user_id = verify_oauth_state_token(state)
    except (JWTError, ValueError):
        return redirect("error")

    user = await db.get(User, user_id)
    if user is None:
        return redirect("error")

    try:
        token_data = await google_oauth.exchange_code(code)
        await google_oauth.upsert_credential(db, user, token_data)
    except (httpx.HTTPError, ValueError, KeyError):
        return redirect("error")

    return redirect("connected")


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def google_disconnect(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    await google_oauth.revoke_credential(db, current_user)
