"""User models with a simple class hierarchy."""

from app.utils.logging import log


class User:
    def __init__(self, name: str) -> None:
        self.name = name

    def login(self) -> bool:
        log("login attempt")
        return self.check_password()

    def check_password(self) -> bool:
        return True


class AdminUser(User):
    def login(self) -> bool:
        log("admin login")
        return self.check_password() and self.has_admin_flag()

    def has_admin_flag(self) -> bool:
        return True
