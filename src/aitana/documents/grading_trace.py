from datetime import UTC, datetime

from beanie import Document, Link
from pydantic import Field
from pymongo import IndexModel

from ..grading.models import GradeTrace
from .submission import Submission


class GradingTrace(Document):
    """One record per real `grade_answer()` call (`worker/tasks.py`'s
    `_grade_answers`) -- kept in its own collection, not embedded in
    `Submission`/`AnsweredQuestion`, so this diagnostic data (the full
    system prompt, any thinking/tool-call content, token usage) never rides
    along on every `Submission.save()`/serialization the way an embedded
    field would (see AGENTS.md sharp edge #1 for why `Submission` already
    gets re-serialized on every grading step -- this deliberately doesn't
    add to that).

    Not written for `POST /rubrics/{id}/test-answer`'s synchronous "try it"
    calls -- that endpoint's docstring already promises nothing is
    persisted, and `grade_answer` only emits a trace when a caller passes
    `on_trace` (see `grading/grading.py`), which that endpoint doesn't.

    There is no TTL/expiry on this collection by design (traceability is the
    point) -- `GET /grading-traces` and `DELETE /grading-traces`
    (`api/routers/grading_traces.py`) are the mechanism for inspecting and
    manually pruning it instead. `regrade_submission` (`api/routers/
    submissions.py`) does *not* clean up a submission's previous traces
    before re-grading -- old and new traces for the same submission/question
    coexist, distinguished by `created_at`, which is deliberate (it's the
    "what did the last run before we fixed the rubric actually see" case
    that traceability exists for) but does mean a repeatedly-regraded
    submission accumulates traces without the cleanup endpoint being used.
    """

    submission: Link[Submission]
    question_id: str
    trace: GradeTrace
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "grading_traces"
        indexes = [
            IndexModel("created_at"),
        ]
