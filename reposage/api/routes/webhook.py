"""GitHub App webhook endpoint (Phase 10).

Fast ACK path: verify the HMAC signature (DD-052), decode the event into a
`WebhookAction`, hand the action to a background task, and return ``202``
well under GitHub's 10s delivery timeout (DD-051). Executing the action
(answering a comment, running an incremental reindex) is a follow-up that
the returned action makes explicit; the heavy lifting must not block the
ACK.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from reposage.bot.github_app import GitHubAppHandler, WebhookAction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["github"])


def _dispatch(action: WebhookAction) -> None:
    """Background sink for a decoded webhook action.

    Kept deliberately I/O-free for now: it logs the decision so the delivery
    is observable end-to-end. Wiring ``answer`` to the retrieval service and
    ``reindex`` to the incremental indexer is the network-dependent follow-up
    (phase-10-github-app.md §D6/D7).
    """
    logger.info(
        "webhook action=%s reason=%s question=%r",
        action.kind,
        action.reason,
        action.question,
    )


@router.post("/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> dict[str, str]:
    body = await request.body()
    handler = GitHubAppHandler.from_settings()

    if not handler.verify_signature(body, x_hub_signature_256):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing webhook signature",
        )

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed JSON payload",
        ) from exc

    action = handler.route_event(x_github_event, payload)
    if action.kind != "ignore":
        background.add_task(_dispatch, action)

    return {"event": x_github_event, "status": "queued", "action": action.kind}
