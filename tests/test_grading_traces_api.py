from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from aitana.db import GradingTrace, Student
from aitana.grading.models import Grade, GradeTrace

from .factories import add, make_submission


async def _make_submission(db):
    student = await add(db, Student(student_id="s1", name="Ada"))
    return await make_submission(db, student=student)


def _trace(**overrides) -> GradeTrace:
    defaults = dict(
        provider="openai",
        model="gpt-4o-mini",
        system_prompt="system prompt text",
        student_answer="student answer text",
        grade=Grade(level="solid", feedback="Good."),
        elapsed_seconds=1.23,
    )
    defaults.update(overrides)
    return GradeTrace(**defaults)


async def _add_trace(db, submission, question_id: str, **fields) -> GradingTrace:
    return await add(db, GradingTrace(submission_id=submission.id, question_id=question_id, trace=_trace(), **fields))


async def test_list_grading_traces_empty(client):
    resp = await client.get("/grading-traces")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_grading_traces_filters_by_question_id(client, db):
    submission = await _make_submission(db)
    await _add_trace(db, submission, "answer1")
    await _add_trace(db, submission, "answer2")

    resp = await client.get("/grading-traces", params={"question_id": "answer1"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["question_id"] == "answer1"
    assert body[0]["submission_id"] == submission.id
    assert body[0]["trace"]["provider"] == "openai"
    assert body[0]["trace"]["grade"]["level"] == "solid"


async def test_list_grading_traces_filters_by_submission_id(client, db):
    submission = await _make_submission(db)
    other = await make_submission(db, rubric=submission.rubric, edition=submission.edition)
    mine = await _add_trace(db, submission, "answer1")
    await _add_trace(db, other, "answer1")

    resp = await client.get("/grading-traces", params={"submission_id": submission.id})
    assert resp.status_code == 200
    assert [t["id"] for t in resp.json()] == [mine.id]


async def test_list_grading_traces_most_recent_first(client, db):
    submission = await _make_submission(db)
    old = await _add_trace(db, submission, "answer1", created_at=datetime.now(UTC) - timedelta(hours=1))
    new = await _add_trace(db, submission, "answer1")

    resp = await client.get("/grading-traces")
    assert [t["id"] for t in resp.json()] == [new.id, old.id]


async def test_get_grading_trace_by_id(client, db):
    submission = await _make_submission(db)
    trace = await _add_trace(db, submission, "answer1")

    resp = await client.get(f"/grading-traces/{trace.id}")
    assert resp.status_code == 200
    assert resp.json()["question_id"] == "answer1"


async def test_get_grading_trace_not_found(client):
    resp = await client.get("/grading-traces/999999999")
    assert resp.status_code == 404


async def test_delete_grading_traces_requires_a_filter_or_confirmation(client):
    resp = await client.delete("/grading-traces")
    assert resp.status_code == 422


async def test_delete_grading_traces_with_all_wipes_the_table(client, db):
    submission = await _make_submission(db)
    await _add_trace(db, submission, "answer1")
    await _add_trace(db, submission, "answer2")

    resp = await client.delete("/grading-traces", params={"all": "true"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}
    assert list(await db.scalars(select(GradingTrace))) == []


async def test_delete_grading_traces_by_submission(client, db):
    submission = await _make_submission(db)
    other = await make_submission(db, rubric=submission.rubric, edition=submission.edition)
    await _add_trace(db, submission, "answer1")
    kept = await _add_trace(db, other, "answer1")

    resp = await client.delete("/grading-traces", params={"submission_id": submission.id})
    assert resp.json() == {"deleted": 1}
    assert [t.id for t in await db.scalars(select(GradingTrace))] == [kept.id]


async def test_delete_grading_traces_before_a_cutoff(client, db):
    submission = await _make_submission(db)
    await _add_trace(db, submission, "old", created_at=datetime.now(UTC) - timedelta(days=30))
    recent = await _add_trace(db, submission, "recent")

    cutoff = datetime.now(UTC) - timedelta(days=1)
    resp = await client.delete("/grading-traces", params={"before": cutoff.isoformat()})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}

    remaining = list(await db.scalars(select(GradingTrace)))
    assert [t.id for t in remaining] == [recent.id]


async def test_deleting_a_submission_deletes_its_traces(client, db):
    submission = await _make_submission(db)
    submission_id = submission.id
    await _add_trace(db, submission, "answer1")
    submission.status = "graded"
    await db.commit()

    resp = await client.delete(f"/submissions/{submission_id}")
    assert resp.status_code == 204
    assert list(await db.scalars(select(GradingTrace))) == []
