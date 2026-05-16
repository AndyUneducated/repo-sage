"""POST /ask — programmatic Q&A endpoint (also used by the GitHub bot)."""

from __future__ import annotations

from fastapi import APIRouter

from reposage.api.schemas import AskRequest, AskResponse

router = APIRouter(prefix="/ask", tags=["qa"])


@router.post("", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    # Phase 2: query router → (symbol-graph | community | hybrid) → LLM with citations.
    return AskResponse(
        answer="",
        citations=[],
        route="placeholder",
        latency_ms=0,
    )
