import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, Header, HTTPException

from .config import get_settings
from .db import get_db
from .models import User

# OWASP's minimum scrypt work (equal to N=2^17, p=1), with 16 MiB per hash instead of 128: logins can arrive in parallel
SCRYPT = "16384:8:5"  # N:r:p


def hash_password(pw: str) -> str:
    salt, (n, r, p) = os.urandom(16), map(int, SCRYPT.split(":"))
    return f"{SCRYPT}${salt.hex()}${hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p).hex()}"


def verify_password(pw: str, stored: str) -> bool:
    params, salt, h = stored.split("$") if stored.count("$") == 2 else ("16384:8:1", *stored.split("$"))  # older: salt$hash
    n, r, p = map(int, params.split(":"))
    return hmac.compare_digest(hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=n, r=r, p=p).hex(), h)


def outdated(stored: str) -> bool:
    return not stored.startswith(SCRYPT + "$")


def _sign(data: bytes) -> str:
    return hmac.new(get_settings().secret_key.encode(), data, hashlib.sha256).hexdigest()


def make_token(username: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": username, "exp": time.time() + get_settings().token_ttl_s}).encode())
    return body.decode() + "." + _sign(body)


def read_token(token: str) -> str | None:
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(body.encode())):
            return None
        data = json.loads(base64.urlsafe_b64decode(body))
        return data["u"] if data["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError):  # TypeError: compare_digest refuses a non-ASCII signature
        return None


def current_user(authorization: str = Header(default=""), db=Depends(get_db)) -> User:
    user = read_token(authorization.removeprefix("Bearer ").strip()) if authorization else None
    u = db.query(User).filter_by(username=user).one_or_none() if user else None
    if u is None:
        raise HTTPException(401, "invalid or missing token")
    return u


def create_user_session(db, username: str, password: str) -> str | None:
    u = db.query(User).filter_by(username=username).one_or_none()
    return make_token(username) if u and verify_password(password, u.password_hash) else None
