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


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def verify_password(pw: str, stored: str) -> bool:
    salt, h = stored.split("$")
    return hmac.compare_digest(hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex(), h)


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
    except (ValueError, KeyError):
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
