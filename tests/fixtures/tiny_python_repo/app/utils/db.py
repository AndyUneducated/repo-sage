"""Database connection wrapper. Used for the unresolvable-call sample."""

from app.utils.logging import log


class Connection:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def fetch(self, sql: str) -> list[tuple[int, ...]]:
        log(f"fetch {sql}")
        return []

    def execute(self, sql: str) -> None:
        log(f"execute {sql}")


def open_connection(dsn: str) -> Connection:
    return Connection(dsn)
