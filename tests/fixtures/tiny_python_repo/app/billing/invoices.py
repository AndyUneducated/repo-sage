"""Invoice issuance — calls into payments."""

from app.billing.payments import Payment, charge
from app.utils.logging import log


class Invoice:
    def __init__(self, amount: int) -> None:
        self.amount = amount
        self.paid = False

    def issue(self, payment: Payment) -> bool:
        log("issuing invoice")
        ok = charge(payment, self.amount)
        if ok:
            self.paid = True
        return self.paid


def issue_many(invoices: list[Invoice], payment: Payment) -> int:
    count = 0
    for inv in invoices:
        if Invoice.issue(inv, payment):
            count += 1
    return count
