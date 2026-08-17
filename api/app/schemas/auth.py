from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_BYTES, password_exceeds_max_bytes


class AccessToken(BaseModel):
    """Login/refresh responses return only the short-lived access token in the
    body. The refresh token is set as an HttpOnly cookie and never exposed to
    JavaScript."""

    access_token: str
    token_type: str = "bearer"


# Every place a password is accepted has to enforce the same bounds, or the
# weakest endpoint sets the real policy. bcrypt's ceiling is 72 *bytes*, so a
# character limit alone would let an accented or emoji passphrase reach a hash
# call that raises — see app.core.security.
NewPassword = Annotated[str, Field(min_length=8, max_length=MAX_PASSWORD_BYTES)]


def _validate_password(value: str) -> str:
    if password_exceeds_max_bytes(value):
        raise ValueError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes; accented or emoji "
            "characters count as more than one."
        )
    return value


class PasswordChangeRequest(BaseModel):
    """Changing a password requires proving you know the current one, so a
    hijacked session cannot lock the real owner out of their own account."""

    current_password: str
    new_password: NewPassword

    _check = field_validator("new_password")(_validate_password)


class PasswordForgotRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: NewPassword

    _check = field_validator("new_password")(_validate_password)


class EmailVerificationRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
