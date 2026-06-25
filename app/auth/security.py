import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from app.core.config import env


PBKDF2_ITERATIONS = 200_000
ACCESS_TOKEN_TTL_SECONDS = int(env("ACCESS_TOKEN_TTL_SECONDS", "900") or "900")
REFRESH_SESSION_TTL_SECONDS = int(env("REFRESH_SESSION_TTL_SECONDS", str(60 * 60 * 24 * 30)) or str(60 * 60 * 24 * 30))
EMAIL_VERIFICATION_TTL_SECONDS = int(env("EMAIL_VERIFICATION_TTL_SECONDS", str(60 * 60 * 24)) or str(60 * 60 * 24))
AUTH_SECRET = env("AUTH_SECRET", "dev-auth-secret-change-me") or "dev-auth-secret-change-me"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return f"{salt}${_b64url_encode(derived_key)}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected_hash = stored_hash.split("$", 1)
    except ValueError:
        return False

    candidate_hash = hash_password(password, salt)
    return hmac.compare_digest(candidate_hash, f"{salt}${expected_hash}")


def create_access_token(user_id: int, email: str, session_id: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "email": email,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
    }

    header_segment = _b64url_encode(
        json.dumps(header, separators=(",", ":")).encode("utf-8")
    )
    payload_segment = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    signature = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()

    return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> Optional[dict]:
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
    except ValueError:
        return None

    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    expected_signature = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()

    if not hmac.compare_digest(
        _b64url_encode(expected_signature),
        signature_segment,
    ):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None

    if int(payload.get("exp", 0)) < int(time.time()):
        return None

    return payload


def generate_opaque_token_secret() -> str:
    return secrets.token_urlsafe(48)


def hash_opaque_token(secret: str) -> str:
    return hashlib.sha256(f"{AUTH_SECRET}:{secret}".encode("utf-8")).hexdigest()


def build_scoped_token(record_id: int, secret: str) -> str:
    return f"{record_id}.{secret}"


def parse_scoped_token(token: str) -> Optional[tuple[int, str]]:
    try:
        record_id_raw, secret = token.split(".", 1)
        record_id = int(record_id_raw)
    except (ValueError, TypeError):
        return None

    secret = (secret or "").strip()
    if record_id <= 0 or not secret:
        return None

    return record_id, secret
