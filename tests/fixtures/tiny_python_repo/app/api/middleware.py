"""Auth middleware shared by all routes."""

from app.auth.sessions import Session, make_session
from app.auth.users import User
from app.utils.logging import log


def require_auth(user: User) -> Session:
    log("require_auth")
    session = make_session(user)
    if not session.active:
        raise RuntimeError("not authenticated")
    return session
