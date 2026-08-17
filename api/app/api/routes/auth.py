from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_csrf_header
from app.core.config import settings
from app.core.rate_limit import enforce_auth_rate_limit
from app.core.security import fake_verify_password, hash_password, verify_password
from app.db.session import get_db
from app.models.email_token import EmailTokenPurpose
from app.models.user import User
from app.schemas.auth import (
    AccessToken,
    EmailVerificationRequest,
    PasswordChangeRequest,
    PasswordForgotRequest,
    PasswordResetRequest,
)
from app.schemas.user import UserCreate, UserRead
from app.services import email, email_tokens, refresh_tokens
from app.services.email_tokens import EmailTokenError
from app.services.refresh_tokens import IssuedTokens, RefreshError

router = APIRouter(prefix="/auth", tags=["auth"])

# Scope the refresh cookie to the auth endpoints so it is never attached to
# ordinary API requests, shrinking its exposure.
_COOKIE_PATH = f"{settings.API_V1_PREFIX}/auth"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        domain=settings.REFRESH_COOKIE_DOMAIN or None,
        path=_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        domain=settings.REFRESH_COOKIE_DOMAIN or None,
        path=_COOKIE_PATH,
    )


def _token_response(response: Response, tokens: IssuedTokens) -> AccessToken:
    _set_refresh_cookie(response, tokens.refresh_token)
    return AccessToken(access_token=tokens.access_token)


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_auth_rate_limit("register"))],
)
async def register(
    user_in: UserCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> User:
    existing = await db.scalar(select(User).where(User.email == user_in.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = User(
        email=user_in.email,
        name=user_in.name,
        password_hash=hash_password(user_in.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Mint the verification token inside the request (it needs the session, which
    # is torn down once the response is sent) and hand only the finished string to
    # the background sender. A mail provider being slow or down must not hold up
    # registration, and must not fail it either — the address is unverified
    # either way, and /verify-email/send exists to try again.
    issued = await email_tokens.issue(db, user, EmailTokenPurpose.email_verification)
    await db.commit()
    background.add_task(email.send_email_verification, user.email, user.name, issued.token)
    return user


@router.post("/login", response_model=AccessToken, dependencies=[Depends(enforce_auth_rate_limit("login"))])
async def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> AccessToken:
    user = await db.scalar(select(User).where(User.email == form_data.username))
    # Run a bcrypt comparison on both branches so a missing account and a wrong
    # password are indistinguishable by timing (prevents email enumeration).
    if user is None:
        fake_verify_password()
        password_ok = False
    else:
        password_ok = verify_password(form_data.password, user.password_hash)
    if user is None or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    tokens = await refresh_tokens.issue_token_pair(db, user)
    return _token_response(response, tokens)


@router.post(
    "/refresh",
    response_model=AccessToken,
    # Order matters: the rate limit runs first so that every caller is metered,
    # including one that fails the CSRF check. Solving CSRF first would let an
    # attacker hammer the endpoint for free by simply omitting the header.
    dependencies=[
        Depends(enforce_auth_rate_limit("refresh")),
        Depends(require_csrf_header),
    ],
)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=settings.REFRESH_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> AccessToken:
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token"
        )
    try:
        tokens = await refresh_tokens.rotate_refresh_token(db, refresh_token)
    except RefreshError:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    return _token_response(response, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await refresh_tokens.revoke_all_for_user(db, current_user)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post(
    "/change-password",
    response_model=AccessToken,
    dependencies=[Depends(enforce_auth_rate_limit("change-password"))],
)
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AccessToken:
    """Change the password of the signed-in user.

    The current password is required: an access token alone must not be enough
    to change the credential, or anyone who borrows a live session locks the
    real owner out permanently.

    Every existing session is then revoked and *this* caller is handed a fresh
    pair. Ending other sessions is the point — a password change is how you
    evict someone — while reissuing here means the person doing it isn't
    logged out of the tab they're standing in.
    """
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )

    current_user.password_hash = hash_password(payload.new_password)
    await refresh_tokens.revoke_all_for_user(db, current_user)
    tokens = await refresh_tokens.issue_token_pair(db, current_user)
    return _token_response(response, tokens)


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(enforce_auth_rate_limit("forgot-password"))],
)
async def forgot_password(
    payload: PasswordForgotRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Start a password reset. Always answers the same way.

    Whether or not the address has an account, the response is 202 with the same
    body: telling the caller which is which turns this endpoint into an account
    enumerator, and it is reachable without credentials. The work that differs —
    minting a token and sending mail — happens after the response is dispatched,
    so the timing does not give it away either.
    """
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is not None:
        issued = await email_tokens.issue(db, user, EmailTokenPurpose.password_reset)
        await db.commit()
        background.add_task(email.send_password_reset, user.email, user.name, issued.token)

    return {"detail": "If that address has an account, a reset link is on its way."}


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_auth_rate_limit("reset-password"))],
)
async def reset_password(
    payload: PasswordResetRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Set a new password using an emailed token.

    Reaching here means whoever holds the link controls the inbox, which is the
    same bar as knowing the old password. So the old password is not required —
    the point of the flow is that it has been forgotten — but every session is
    revoked, because the reason to reset is usually that someone else may have
    had access.
    """
    try:
        user = await email_tokens.redeem(db, payload.token, EmailTokenPurpose.password_reset)
    except EmailTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That reset link is invalid or has expired. Request a new one.",
        )

    user.password_hash = hash_password(payload.new_password)
    # Proving control of the inbox is exactly what verification asks for, so a
    # completed reset settles it too and saves the user a second round trip.
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
    # Commits the redemption and the new password together: if this failed after
    # the token were separately committed, the link would be spent without having
    # changed anything, stranding the user.
    await refresh_tokens.revoke_all_for_user(db, user)
    _clear_refresh_cookie(response)


@router.post(
    "/verify-email/send",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(enforce_auth_rate_limit("verify-email"))],
)
async def send_verification_email(
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """(Re)send the confirmation link to the signed-in user's address."""
    if current_user.email_verified:
        return {"detail": "This address is already confirmed."}

    issued = await email_tokens.issue(db, current_user, EmailTokenPurpose.email_verification)
    await db.commit()
    background.add_task(
        email.send_email_verification, current_user.email, current_user.name, issued.token
    )
    return {"detail": "Confirmation email sent."}


@router.post("/verify-email/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_email(payload: EmailVerificationRequest, db: AsyncSession = Depends(get_db)) -> None:
    """Confirm an address from an emailed link.

    Unauthenticated on purpose: the link is opened from a mail client, and
    requiring a live session would strand anyone who clicks it in a browser they
    are not signed into. The token is the proof, and it identifies the user by
    itself.
    """
    try:
        user = await email_tokens.redeem(db, payload.token, EmailTokenPurpose.email_verification)
    except EmailTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That confirmation link is invalid or has expired. Request a new one.",
        )

    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
    await db.commit()
