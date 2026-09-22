from datetime import datetime

from beanie import PydanticObjectId
from beanie.operators import LT
from fastapi import APIRouter, HTTPException, Query

from ...documents import GradingTrace

router = APIRouter(prefix="/grading-traces", tags=["grading-traces"])

# Diagnostic-only endpoints, no frontend consumes these yet (see AGENTS.md
# "Grading traces") -- inspect via Swagger/curl for now.


@router.get("", response_model=list[GradingTrace])
async def list_grading_traces(
    submission_id: PydanticObjectId | None = None,
    question_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[GradingTrace]:
    """Most-recent-first, optionally narrowed to one submission and/or
    question -- the two axes someone diagnosing a specific grade actually
    has on hand."""
    filters = []
    if submission_id is not None:
        # Same Beanie Link-query shape as everywhere else (AGENTS.md sharp
        # edge #3) -- verified against real MongoDB, returns nothing under
        # mongomock regardless of a genuine match.
        filters.append(GradingTrace.submission.id == submission_id)
    if question_id is not None:
        filters.append(GradingTrace.question_id == question_id)
    return await GradingTrace.find(*filters).sort(-GradingTrace.created_at).limit(limit).to_list()


@router.get("/{trace_id}", response_model=GradingTrace)
async def get_grading_trace(trace_id: PydanticObjectId) -> GradingTrace:
    trace = await GradingTrace.get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Grading trace not found")
    return trace


@router.delete("")
async def delete_grading_traces(
    submission_id: PydanticObjectId | None = None,
    before: datetime | None = None,
    all_: bool = Query(False, alias="all"),
) -> dict[str, int]:
    """Bulk-delete grading traces -- the cleanup mechanism this collection
    has instead of a TTL index (see `documents/grading_trace.py`'s
    docstring: traceability data is meant to accumulate until someone
    deliberately decides to prune it, not expire on its own).

    Requires at least one of `submission_id`/`before`, or the explicit
    `all=true` escape hatch -- a bare `DELETE /grading-traces` silently
    wiping the entire collection would be an easy mistake otherwise.
    `submission_id`/`before` given together AND, same as every other
    multi-filter list endpoint in this app.
    """
    filters = []
    if submission_id is not None:
        filters.append(GradingTrace.submission.id == submission_id)
    if before is not None:
        filters.append(LT(GradingTrace.created_at, before))

    if not filters and not all_:
        raise HTTPException(
            status_code=422,
            detail="Refusing to delete every grading trace without confirmation -- pass submission_id, before, or all=true.",
        )

    result = await GradingTrace.find(*filters).delete()
    return {"deleted": result.deleted_count if result else 0}
