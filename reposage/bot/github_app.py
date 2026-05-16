"""GitHub App event handling.

Phase 4 implements: webhook signature verification, JWT minting from the App
private key, installation-token exchange, and the @-mention command parser.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class IssueCommentEvent:
    repo_full_name: str
    issue_number: int
    comment_id: int
    comment_body: str
    sender: str


class GitHubAppHandler:
    def __init__(self, app_id: str, private_key_pem: str, webhook_secret: str) -> None:
        self.app_id = app_id
        self.private_key_pem = private_key_pem
        self.webhook_secret = webhook_secret

    def verify_signature(self, body: bytes, header_signature: str) -> bool:
        raise NotImplementedError

    async def handle_issue_comment(self, event: IssueCommentEvent) -> None:
        raise NotImplementedError
