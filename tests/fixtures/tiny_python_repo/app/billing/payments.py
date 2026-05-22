"""Payment instruments and the `charge` workhorse."""

from app.utils.logging import log


class Payment:
    def authorize(self) -> bool:
        return True


class CreditCard(Payment):
    def __init__(self, number: str) -> None:
        self.number = number

    def authorize(self) -> bool:
        log("cc authorize")
        return self.number.isdigit()


def charge(payment: Payment, amount: int) -> bool:
    if Payment.authorize(payment):
        log(f"charged {amount}")
        return True
    return False
