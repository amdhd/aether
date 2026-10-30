from app.core.security import decode_token
from app.models.user import User
from app.services import refresh_tokens
from tests.conftest import TestingSessionLocal


async def test_stale_user_snapshot_cannot_reuse_revocation_version():
    async with TestingSessionLocal() as db:
        user = User(email="version@example.com", name="Version", password_hash="dummy")
        db.add(user)
        await db.commit()
        user_id = user.id
    async with TestingSessionLocal() as first, TestingSessionLocal() as second:
        a = await first.get(User, user_id)
        b = await second.get(User, user_id)
        assert a.token_version == b.token_version
        await refresh_tokens.revoke_all_for_user(first, a, commit=False)
        earlier = await refresh_tokens.issue_token_pair(first, a)
        await refresh_tokens.revoke_all_for_user(second, b, commit=False)
        later = await refresh_tokens.issue_token_pair(second, b)
        assert decode_token(later.access_token)["ver"] > decode_token(earlier.access_token)["ver"]
    async with TestingSessionLocal() as db:
        assert (await db.get(User, user_id)).token_version == decode_token(later.access_token)["ver"]


async def test_uncommitted_revocation_and_password_change_roll_back_together():
    async with TestingSessionLocal() as db:
        user = User(email="rollback-version@example.com", name="Version", password_hash="original")
        db.add(user)
        await db.commit()
        user_id, original_version = user.id, user.token_version
        user.password_hash = "changed"
        await refresh_tokens.revoke_all_for_user(db, user, commit=False)
        await db.rollback()
    async with TestingSessionLocal() as db:
        user = await db.get(User, user_id)
        assert user.token_version == original_version
        assert user.password_hash == "original"


async def test_replay_invalidates_access_and_cannot_revoke_a_subsequent_login(client):
    import pytest
    from app.services.refresh_tokens import RefreshError

    async with TestingSessionLocal() as db:
        user = User(email="replay-version@example.com", name="Replay", password_hash="dummy")
        db.add(user)
        await db.commit()
        original = await refresh_tokens.issue_token_pair(db, user)
        rotated = await refresh_tokens.rotate_refresh_token(db, original.refresh_token)
        with pytest.raises(RefreshError):
            await refresh_tokens.rotate_refresh_token(db, original.refresh_token)
        denied = await client.get('/api/v1/auth/me', headers={'Authorization': f'Bearer {rotated.access_token}'})
        assert denied.status_code == 401
        await db.refresh(user)
        fresh = await refresh_tokens.issue_token_pair(db, user)
        # A replay from the preceding token version must not keep evicting
        # sessions established after detection.
        with pytest.raises(RefreshError):
            await refresh_tokens.rotate_refresh_token(db, original.refresh_token)
        accepted = await client.get('/api/v1/auth/me', headers={'Authorization': f'Bearer {fresh.access_token}'})
        assert accepted.status_code == 200
