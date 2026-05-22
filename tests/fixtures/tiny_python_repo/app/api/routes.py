"""HTTP-style routes: top-level free functions calling auth + billing."""

from app.api.middleware import require_auth
from app.auth.users import AdminUser, User
from app.billing.invoices import Invoice, issue_many
from app.billing.payments import CreditCard
from app.utils.logging import log


def create_user(name: str) -> User:
    log("create_user")
    return User(name)


def create_admin(name: str) -> AdminUser:
    log("create_admin")
    return AdminUser(name)


def login_route(user: User) -> bool:
    require_auth(user)
    return User.login(user)


def billing_route(user: User, amount: int) -> bool:
    require_auth(user)
    invoice = Invoice(amount)
    card = CreditCard("4111111111111111")
    return Invoice.issue(invoice, card)


def bulk_route(user: User, amounts: list[int]) -> int:
    require_auth(user)
    invoices = [Invoice(a) for a in amounts]
    card = CreditCard("4111111111111111")
    return issue_many(invoices, card)
