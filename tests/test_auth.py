from app.core.security import create_access_token, decode_access_token, get_password_hash, verify_password


def test_password_hash_roundtrip():
    password = "TestPassword123!"
    hashed = get_password_hash(password)
    assert verify_password(password, hashed) is True


def test_jwt_roundtrip():
    token = create_access_token({"sub": "1", "role": "Admin"})
    payload = decode_access_token(token)
    assert payload["sub"] == "1"

