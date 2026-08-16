"""认证：bcrypt 密码哈希 + 服务端会话（Cookie）。"""

from __future__ import annotations

import secrets
from typing import Optional

import bcrypt
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import SESSION_COOKIE, SESSION_MAX_AGE
from app.models import SessionToken, User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def create_session(db: Session, user: User) -> str:
    token = secrets.token_hex(32)
    db.add(SessionToken(token=token, user_id=user.id))
    db.commit()
    return token


def delete_session(db: Session, token: str) -> None:
    db.query(SessionToken).filter(SessionToken.token == token).delete()
    db.commit()


def current_user(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    st = db.scalar(select(SessionToken).where(SessionToken.token == token))
    return st.user if st else None
