"""POST /ask — Phase 2 hybrid RAG endpoint with citations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from reposage.api.dependencies import get_retrieval_service
from reposage.api.schemas import AskRequest, AskResponse, Citation, LatencyMs
from reposage.services.retrieval_service import RetrievalService

router = APIRouter(prefix="/ask", tags=["qa"])


@router.post("", response_model=AskResponse)
async def ask(
    req: AskRequest,
    service: RetrievalService = Depends(get_retrieval_service),
) -> AskResponse:
    try:
        result = await service.answer(
            req.question,
            repo=req.repo,
            route_hint=None if req.route_hint == "auto" else req.route_hint,
            top_k=req.top_k,
        )
    except RuntimeError as exc:  # e.g. dim/model mismatch on first connect
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return AskResponse(
        question=result.question,
        answer=result.answer,
        citations=[
            Citation(
                path=c.path,
                start_line=c.start_line,
                end_line=c.end_line,
            )
            for c in result.citations
        ],
        route=result.route,
        grounded=result.grounded,
        latency_ms=LatencyMs(
            embed_ms=result.latency.embed_ms,
            retrieve_ms=result.latency.retrieve_ms,
            rerank_ms=result.latency.rerank_ms,
            llm_ms=result.latency.llm_ms,
            total_ms=result.latency.total_ms,
        ),
        graph_context=None,
    )
