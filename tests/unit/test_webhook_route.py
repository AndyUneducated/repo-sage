"""Route-level tests for the Phase 10 webhook endpoint (fast-ACK + HMAC gate)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from reposage import config
from reposage.api.main import create_app

SECRET = "webhook-secret"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    settings = config.Settings(
        github_webhook_secret=SECRET,
        github_bot_login="reposage",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    return TestClient(create_app())


def _post(client: TestClient, event: str, payload: dict[str, object], *, secret: str = SECRET):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook/github",
        content=body,
        headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig},
    )


def test_valid_issue_comment_is_accepted(client: TestClient) -> None:
    payload = {
        "action": "created",
        "comment": {"id": 1, "body": "@reposage where is auth?"},
        "issue": {"number": 3},
        "repository": {"full_name": "acme/widgets"},
        "sender": {"login": "alice"},
    }
    resp = _post(client, "issue_comment", payload)
    assert resp.status_code == 202
    assert resp.json()["action"] == "answer"


def test_bad_signature_rejected(client: TestClient) -> None:
    resp = _post(client, "issue_comment", {"action": "created"}, secret="wrong")
    assert resp.status_code == 401


def test_non_mention_comment_ignored(client: TestClient) -> None:
    payload = {
        "action": "created",
        "comment": {"id": 1, "body": "unrelated chatter"},
        "issue": {"number": 3},
        "repository": {"full_name": "acme/widgets"},
        "sender": {"login": "alice"},
    }
    resp = _post(client, "issue_comment", payload)
    assert resp.status_code == 202
    assert resp.json()["action"] == "ignore"
