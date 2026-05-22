"""Sessions: opens via User.login and is consumed by the api layer."""

from app.auth.users import User
from app.utils.logging import log


class Session:
    def __init__(self, user: User) -> None:
        self.user = user
        self.active = False

    def open(self) -> bool:
        if User.login(self.user):
            self.active = True
            log("session opened")
            return True
        return False

    def close(self) -> None:
        self.active = False
        log("session closed")


def make_session(user: User) -> Session:
    s = Session(user)
    s.open()
    return s
