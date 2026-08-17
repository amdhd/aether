from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_BYTES, password_exceeds_max_bytes


class UserBase(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)


class UserCreate(UserBase):
    # The ceiling is bcrypt's, and it is 72 *bytes* — a character limit alone
    # would still let a passphrase of accented or emoji characters through to a
    # hash call that raises. Advertising 128 here is what made a long
    # password-manager passphrase fail registration with a 500.
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_BYTES)

    @field_validator("password")
    @classmethod
    def _within_bcrypt_limit(cls, value: str) -> str:
        if password_exceeds_max_bytes(value):
            raise ValueError(
                f"Password must be at most {MAX_PASSWORD_BYTES} bytes; accented or emoji "
                "characters count as more than one."
            )
        return value


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # Read off User.email_verified. Exposed so the SPA can prompt for
    # confirmation; nothing is gated on it — verification is advisory — so this
    # drives a banner, not access.
    email_verified: bool
