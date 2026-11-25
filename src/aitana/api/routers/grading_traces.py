from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import GradingTrace
from ...db.session import get_session
from ..schemas import GradingTraceOut

router = APIRouter(prefix="/grading-traces", tags=["grading-traces"])

# Diagnostic-only endpoints, no frontend consumes these yet (see AGENTS.md
# "Grading traces") -- inspect via Swagger/curl.


@router.get("", response_model=list[GradingTraceOut])
async def list_grading_traces(
    submission_id: int | None = None,
    question_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[GradingTrace]:
    """Most-recent-first, optionally narrowed to one submission and/or
    question -- the two axes someone diagnosing a specific grade actually
    has on hand."""
    query = select(GradingTrace)
    if submission_id is not None:
        query = query.where(GradingTrace.submission_id == submission_id)
    if question_id is not None:
        query = query.where(GradingTrace.question_id == question_id)
    query = query.order_by(GradingTrace.created_at.desc(), GradingTrace.id.desc()).limit(limit)
    return list(await session.scalars(query))


@router.get("/{trace_id}", response_model=GradingTraceOut)
async def get_grading_trace(trace_id: int, session: AsyncSession = Depends(get_session)) -> GradingTrace:
    trace = await session.get(GradingTrace, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Grading trace not found")
    return trace


@router.delete("")
async def delete_grading_traces(
    submission_id: int | None = None,
    before: datetime | None = None,
    all_: bool = Query(False, alias="all"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Bulk-delete grading traces -- traces never expire on their own
    (traceability is the point), this is how they're pruned.

    Requires at least one of `submission_id`/`before`, or the explicit
    `all=true` escape hatch -- a bare `DELETE /grading-traces` silently
    wiping the entire table would be an easy mistake otherwise.
    `submission_id`/`before` given together AND, same as every other
    multi-filter list endpoint in this app.
    """
    conditions = []
    if submission_id is not None:
        conditions.append(GradingTrace.submission_id == submission_id)
    if before is not None:
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)  # a naive cutoff is read as UTC
        conditions.append(GradingTrace.created_at < before)

    if not conditions and not all_:
        raise HTTPException(
            status_code=422,
            detail="Refusing to delete every grading trace without confirmation -- pass submission_id, before, or all=true.",
        )

    result = await session.execute(delete(GradingTrace).where(*conditions))
    await session.commit()
    return {"deleted": result.rowcount or 0}
