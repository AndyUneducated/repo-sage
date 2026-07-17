"""Unit tests for the Phase 10 deterministic webhook core (DD-051..053)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from reposage.bot.github_app import GitHubAppHandler, parse_command

SECRET = "s3cr3t"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _handler(secret: str | None = SECRET) -> GitHubAppHandler:
    return GitHubAppHandler(webhook_secret=secret, bot_login="reposage")


def test_verify_signature_accepts_valid() -> None:
    body = b'{"hello":"world"}'
    assert _handler().verify_signature(body, _sign(body)) is True


def test_verify_signature_rejects_tampered_body() -> None:
    good = _sign(b"original")
    assert _handler().verify_signature(b"tampered", good) is False


def test_verify_signature_rejects_wrong_secret() -> None:
    body = b"payload"
    assert _handler().verify_signature(body, _sign(body, "other-secret")) is False


def test_verify_signature_fails_closed_without_secret() -> None:
    body = b"payload"
    assert _handler(secret=None).verify_signature(body, _sign(body)) is False


def test_verify_signature_rejects_missing_or_malformed_header() -> None:
    body = b"payload"
    h = _handler()
    assert h.verify_signature(body, "") is False
    assert h.verify_signature(body, "sha1=deadbeef") is False


def test_parse_command_extracts_question() -> None:
    assert parse_command("@reposage where is auth handled?", bot_login="reposage") == (
        "where is auth handled?"
    )


def test_parse_command_is_case_insensitive_and_strips_separators() -> None:
    assert parse_command("@RepoSage: explain login", bot_login="reposage") == "explain login"


def test_parse_command_none_without_mention() -> None:
    assert parse_command("just a normal comment", bot_login="reposage") is None
    assert parse_command("@reposage   ", bot_login="reposage") is None
    assert parse_command("", bot_login="reposage") is None


def _issue_payload(body: str, *, action: str = "created", sender: str = "alice") -> dict[str, Any]:
    return {
        "action": action,
        "comment": {"id": 42, "body": body},
        "issue": {"number": 7},
        "repository": {"full_name": "acme/widgets"},
        "sender": {"login": sender},
    }


def test_route_issue_comment_answer() -> None:
    action = _handler().route_event("issue_comment", _issue_payload("@reposage what is X?"))
    assert action.kind == "answer"
    assert action.question == "what is X?"
    assert action.issue is not None
    assert action.issue.repo_full_name == "acme/widgets"
    assert action.issue.issue_number == 7


def test_route_issue_comment_ignores_self_and_non_mentions() -> None:
    h = _handler()
    assert h.route_event("issue_comment", _issue_payload("no mention here")).kind == "ignore"
    self_comment = _issue_payload("@reposage hi", sender="reposage")
    assert h.route_event("issue_comment", self_comment).kind == "ignore"
    deleted = _issue_payload("@reposage hi", action="deleted")
    assert h.route_event("issue_comment", deleted).kind == "ignore"


def test_route_push_reindex_and_branch_delete() -> None:
    h = _handler()
    push = {
        "ref": "refs/heads/main",
        "before": "a" * 40,
        "after": "b" * 40,
        "repository": {"full_name": "acme/widgets"},
        "pusher": {"name": "bob"},
    }
    action = h.route_event("push", push)
    assert action.kind == "reindex"
    assert action.push is not None and action.push.after == "b" * 40

    delete = {**push, "after": "0" * 40}
    assert h.route_event("push", delete).kind == "ignore"


def test_route_unknown_event_ignored() -> None:
    assert _handler().route_event("ping", {}).kind == "ignore"
