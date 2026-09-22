from datetime import UTC, datetime, timedelta

from aitana.documents import Course, Edition, GradingTrace, Rubric, Student, Submission
from aitana.grading.models import Grade, GradeTrace, Question


async def _make_submission(client) -> Submission:
    course = Course(name="BDM", slug="bdm")
    await course.insert()
    edition = Edition(name="2026/27", slug="2026_27")
    await edition.insert()
    student = Student(student_id="s1", name="Ada")
    await student.insert()
    rubric = Rubric(
        slug="containers",
        title="Containers",
        course=course,
        questions=[Question(id="answer1", title="Q1", question="Q1?", rubric="R1")],
    )
    await rubric.insert()
    submission = Submission(student=student, rubric=rubric, edition=edition, file_object_key="k")
    await submission.insert()
    return submission


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


async def test_list_grading_traces_empty(client):
    resp = await client.get("/grading-traces")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_grading_traces_filters_by_question_id(client):
    submission = await _make_submission(client)
    await GradingTrace(submission=submission, question_id="answer1", trace=_trace()).insert()
    await GradingTrace(submission=submission, question_id="answer2", trace=_trace()).insert()

    resp = await client.get("/grading-traces", params={"question_id": "answer1"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["question_id"] == "answer1"
    assert body[0]["trace"]["provider"] == "openai"
    assert body[0]["trace"]["grade"]["level"] == "solid"


async def test_list_grading_traces_by_submission_id_is_empty_under_mongomock(client):
    # Same Beanie Link-query gap as everywhere else in this app (AGENTS.md
    # sharp edge #3): `GradingTrace.submission.id == ...` doesn't match under
    # mongomock even against a genuinely matching document. Verified against
    # real MongoDB manually; only the shape (200, well-formed response) is
    # asserted here.
    submission = await _make_submission(client)
    await GradingTrace(submission=submission, question_id="answer1", trace=_trace()).insert()

    resp = await client.get("/grading-traces", params={"submission_id": str(submission.id)})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_grading_trace_by_id(client):
    submission = await _make_submission(client)
    trace = await GradingTrace(submission=submission, question_id="answer1", trace=_trace()).insert()

    resp = await client.get(f"/grading-traces/{trace.id}")
    assert resp.status_code == 200
    assert resp.json()["question_id"] == "answer1"


async def test_get_grading_trace_not_found(client):
    resp = await client.get("/grading-traces/000000000000000000000000")
    assert resp.status_code == 404


async def test_delete_grading_traces_requires_a_filter_or_confirmation(client):
    resp = await client.delete("/grading-traces")
    assert resp.status_code == 422


async def test_delete_grading_traces_with_all_wipes_the_collection(client):
    submission = await _make_submission(client)
    await GradingTrace(submission=submission, question_id="answer1", trace=_trace()).insert()
    await GradingTrace(submission=submission, question_id="answer2", trace=_trace()).insert()

    resp = await client.delete("/grading-traces", params={"all": "true"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}
    assert await GradingTrace.find_all().to_list() == []


async def test_delete_grading_traces_before_a_cutoff(client):
    submission = await _make_submission(client)
    old = GradingTrace(submission=submission, question_id="old", trace=_trace())
    old.created_at = datetime.now(UTC) - timedelta(days=30)
    await old.insert()
    recent = await GradingTrace(submission=submission, question_id="recent", trace=_trace()).insert()

    cutoff = datetime.now(UTC) - timedelta(days=1)
    resp = await client.delete("/grading-traces", params={"before": cutoff.isoformat()})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}

    remaining = await GradingTrace.find_all().to_list()
    assert [t.id for t in remaining] == [recent.id]
