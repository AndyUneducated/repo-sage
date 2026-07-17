"""GitHub App event handling — the deterministic, network-free core (Phase 10).

This module owns the parts that are pure functions of the request bytes and
so are fully unit-testable without hitting GitHub:

* :meth:`GitHubAppHandler.verify_signature` — HMAC-SHA256 with a constant-time
  compare (DD-052), failing closed when no secret is configured.
* :func:`parse_command` — pull the question out of an ``@reposage …`` mention.
* Typed events (:class:`IssueCommentEvent`, :class:`PushEvent`) parsed from the
  raw webhook payload.
* :meth:`GitHubAppHandler.route_event` — map (event name, payload) to a
  :class:`WebhookAction` the caller executes (answer / reindex / ignore).

JWT minting, installation-token exchange, and comment posting are the
network/crypto-dependent follow-ups (phase-10-github-app.md §D2/D6/D7); they
are intentionally *not* here so this layer stays deterministic.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Literal

_ZERO_SHA = "0" * 40


@dataclass(slots=True, frozen=True)
class IssueCommentEvent:
    repo_full_name: str
    issue_number: int
    comment_id: int
    comment_body: str
    sender: str
    action: str = "created"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> IssueCommentEvent:
        comment = payload.get("comment") or {}
        issue = payload.get("issue") or {}
        repo = payload.get("repository") or {}
        sender = payload.get("sender") or {}
        return cls(
            repo_full_name=str(repo.get("full_name", "")),
            issue_number=int(issue.get("number", 0)),
            comment_id=int(comment.get("id", 0)),
            comment_body=str(comment.get("body", "")),
            sender=str(sender.get("login", "")),
            action=str(payload.get("action", "")),
        )


@dataclass(slots=True, frozen=True)
class PushEvent:
    repo_full_name: str
    ref: str
    before: str
    after: str
    sender: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PushEvent:
        repo = payload.get("repository") or {}
        sender = payload.get("sender") or payload.get("pusher") or {}
        return cls(
            repo_full_name=str(repo.get("full_name", "")),
            ref=str(payload.get("ref", "")),
            before=str(payload.get("before", "")),
            after=str(payload.get("after", "")),
            sender=str(sender.get("login") or sender.get("name") or ""),
        )

    @property
    def is_branch_delete(self) -> bool:
        """A push whose ``after`` is the all-zero sha deletes the ref."""
        return self.after == _ZERO_SHA


ActionKind = Literal["answer", "reindex", "ignore"]


@dataclass(slots=True, frozen=True)
class WebhookAction:
    """A decoded, side-effect-free description of what a webhook asks for.

    The caller (webhook route / background worker) turns this into real work:
    ``answer`` posts a grounded reply, ``reindex`` kicks incremental indexing,
    ``ignore`` is a no-op (bot's own comment, no mention, branch delete, …).
    """

    kind: ActionKind
    reason: str = ""
    question: str | None = None
    issue: IssueCommentEvent | None = None
    push: PushEvent | None = None


def parse_command(comment_body: str, *, bot_login: str) -> str | None:
    """Extract the question following an ``@<bot_login>`` mention.

    Returns the trimmed text after the (case-insensitive) mention, or
    ``None`` when the comment doesn't mention the bot or the mention has no
    trailing question. The mention may sit anywhere in the comment; we take
    everything after the *first* mention to end-of-comment.
    """
    if not comment_body:
        return None
    pattern = re.compile(rf"@{re.escape(bot_login)}\b", re.IGNORECASE)
    match = pattern.search(comment_body)
    if match is None:
        return None
    question = comment_body[match.end() :].strip()
    # Strip a leading separator like ":" that users often type after the handle.
    question = question.lstrip(":,-").strip()
    return question or None


class GitHubAppHandler:
    """Deterministic webhook decoder.

    Constructed with the app credentials; only ``webhook_secret`` is needed
    for the signature/route path implemented here. ``app_id`` /
    ``private_key_pem`` are carried for the JWT follow-up and may be ``None``.
    """

    def __init__(
        self,
        *,
        webhook_secret: str | None,
        bot_login: str = "reposage",
        app_id: str | None = None,
        private_key_pem: str | None = None,
    ) -> None:
        self.webhook_secret = webhook_secret
        self.bot_login = bot_login
        self.app_id = app_id
        self.private_key_pem = private_key_pem

    @classmethod
    def from_settings(cls) -> GitHubAppHandler:
        from reposage.config import get_settings  # noqa: PLC0415

        s = get_settings()
        # The private key is deliberately *not* read here: it's only needed
        # for JWT minting (pending), and reading it on every webhook would add
        # per-request I/O and a failure surface to the signature-check path.
        # `load_private_key()` reads it lazily when JWT support lands.
        return cls(
            webhook_secret=s.github_webhook_secret,
            bot_login=s.github_bot_login,
            app_id=s.github_app_id,
            private_key_pem=None,
        )

    def verify_signature(self, body: bytes, header_signature: str) -> bool:
        """Constant-time HMAC-SHA256 check of ``X-Hub-Signature-256`` (DD-052).

        Fails closed: a missing secret or a malformed / absent header returns
        ``False`` rather than skipping verification.
        """
        if not self.webhook_secret or not header_signature:
            return False
        if not header_signature.startswith("sha256="):
            return False
        digest = hmac.new(self.webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        expected = f"sha256={digest}"
        return hmac.compare_digest(expected, header_signature)

    def route_event(self, event_name: str, payload: dict[str, Any]) -> WebhookAction:
        """Decode (event, payload) into a :class:`WebhookAction`."""
        if event_name == "issue_comment":
            return self._route_issue_comment(payload)
        if event_name == "push":
            return self._route_push(payload)
        return WebhookAction(kind="ignore", reason=f"unhandled event: {event_name or '<none>'}")

    def _route_issue_comment(self, payload: dict[str, Any]) -> WebhookAction:
        event = IssueCommentEvent.from_payload(payload)
        if event.action not in {"created", "edited"}:
            return WebhookAction(kind="ignore", reason=f"comment action={event.action}")
        # Never answer our own comments — avoids an infinite reply loop.
        if event.sender.lower() == self.bot_login.lower():
            return WebhookAction(kind="ignore", reason="self-authored comment")
        question = parse_command(event.comment_body, bot_login=self.bot_login)
        if question is None:
            return WebhookAction(kind="ignore", reason="no @mention command")
        return WebhookAction(kind="answer", question=question, issue=event)

    def _route_push(self, payload: dict[str, Any]) -> WebhookAction:
        event = PushEvent.from_payload(payload)
        if event.is_branch_delete:
            return WebhookAction(kind="ignore", reason="branch delete", push=event)
        return WebhookAction(kind="reindex", push=event)
