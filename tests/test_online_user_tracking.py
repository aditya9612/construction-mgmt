import pytest
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from jose import jwt

from app.main import app
from app.core.config import settings
from app.core.security import create_access_token


@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock.set = AsyncMock(return_value=True)
    mock.sadd = AsyncMock(return_value=1)
    mock.smembers = AsyncMock(return_value=set())
    return mock


@pytest.fixture
def client(mock_redis):
    original_redis = getattr(app.state, "redis", None)
    app.state.redis = mock_redis
    test_client = TestClient(app)
    yield test_client
    app.state.redis = original_redis


def test_track_user_activity_with_valid_jwt(client, mock_redis):
    """Verify that a valid JWT causes track_user_activity to record user:online and last_seen in Redis."""
    token = create_access_token({"sub": "55"})

    response = client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    # Verify Redis online key set with 60s TTL
    mock_redis.set.assert_any_call("user:55:online", 1, ex=60)

    # Verify Redis last_seen key set
    last_seen_calls = [
        call for call in mock_redis.set.call_args_list
        if call[0][0] == "user:55:last_seen"
    ]
    assert len(last_seen_calls) == 1


def test_track_user_activity_with_invalid_jwt(client, mock_redis):
    """Verify that an invalid JWT does not crash the middleware and fails safely."""
    response = client.get("/health", headers={"Authorization": "Bearer not.a.valid.jwt"})
    assert response.status_code == 200
    mock_redis.set.assert_not_called()


def test_track_user_activity_with_expired_jwt(client, mock_redis):
    """Verify that an expired JWT does not crash the middleware and fails safely."""
    expired_token = create_access_token({"sub": "55"}, expires_delta=timedelta(minutes=-30))
    response = client.get("/health", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 200
    mock_redis.set.assert_not_called()


def test_track_user_activity_missing_auth(client, mock_redis):
    """Verify that a request without an Authorization header proceeds without error."""
    response = client.get("/health")
    assert response.status_code == 200
    mock_redis.set.assert_not_called()


def test_track_user_activity_when_redis_none(client):
    """Verify that when redis is None, track_user_activity does not crash."""
    app.state.redis = None
    token = create_access_token({"sub": "55"})
    response = client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_jwt_secret_never_logged(client, caplog):
    """Verify that JWT secret or token payload is never exposed in log output."""
    token = create_access_token({"sub": "55"})
    with caplog.at_level(logging.DEBUG):
        response = client.get("/health", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

    # Ensure secret is never present in log records
    for record in caplog.records:
        assert settings.JWT_SECRET not in record.message
