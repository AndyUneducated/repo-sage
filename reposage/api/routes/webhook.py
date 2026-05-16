"""GitHub App webhook endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

router = APIRouter(prefix="/webhook", tags=["github"])


@router.post("/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> dict[str, str]:
    # Phase 4: verify HMAC, dispatch to reposage.bot.github_app.handle_event.
    _ = await request.body()
    return {"event": x_github_event, "status": "queued"}
