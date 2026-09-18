"""Standalone HTML export of a graded submission's feedback.

Produces one self-contained `.html` file per submission -- everything (CSS,
markup) inlined into the single file, no external stylesheet/font/CDN
reference, so it still renders correctly with no internet connection once
downloaded (e.g. handed back to a student, or re-uploaded into an LMS as
per-submission feedback -- see `batch_zip_entries` below for the batch
case). Mirrors the ordinal grade-color ramp `GradeBadge`/`GradingGrid` use
in the web UI (`frontend/src/lib/gradeColor.ts`) so the export reads
consistently with what grading this submission in the app looks like.
"""

from __future__ import annotations

import html
import re

from ..documents.submission import Submission

# Same 9-step, one-hue ramp as frontend/src/lib/gradeColor.ts's ORDINAL_STEPS
# -- keep the two in sync if that ramp ever changes.
_ORDINAL_STEPS: list[tuple[str, str]] = [
    ("#86b6ef", "black"),
    ("#6da7ec", "black"),
    ("#5598e7", "black"),
    ("#3987e5", "black"),
    ("#256abf", "white"),
    ("#1c5cab", "white"),
    ("#184f95", "white"),
    ("#104281", "white"),
    ("#0d366b", "white"),
]


def _grade_color(level: str, scale: dict[str, str]) -> tuple[str, str] | None:
    levels = list(scale.keys())
    if level not in levels:
        return None
    count = len(levels)
    fraction = levels.index(level) / (count - 1) if count > 1 else 1
    return _ORDINAL_STEPS[round(fraction * (len(_ORDINAL_STEPS) - 1))]


def _prettify_level(level: str) -> str:
    words = re.sub(r"[_-]+", " ", level).strip()
    return re.sub(r"\b\w", lambda m: m.group().upper(), words)


_STYLE = """
  * { box-sizing: border-box; }
  body { margin: 0; padding: 2rem 1rem; background: #f8fafc; color: #0f172a;
         font-family: system-ui, "Segoe UI", Roboto, sans-serif; }
  .page { max-width: 720px; margin: 0 auto; }
  header { margin-bottom: 1.5rem; }
  h1 { font-size: 1.25rem; font-weight: 600; margin: 0 0 .25rem; }
  .meta { font-size: .875rem; color: #64748b; margin: 0; }
  .notice { border-radius: .375rem; padding: .5rem .75rem; font-size: .875rem; margin: 0 0 1rem; }
  .notice.progress { background: #fffbeb; color: #92400e; }
  .notice.failed { background: #fef2f2; color: #991b1b; }
  .question { background: #fff; border: 1px solid #e2e8f0; border-radius: .5rem;
              padding: 1rem; margin-bottom: 1rem; }
  .question-header { display: flex; align-items: center; justify-content: space-between;
                      gap: .75rem; margin-bottom: .5rem; }
  .question-header h2 { font-size: 1rem; font-weight: 500; margin: 0; }
  .badge { display: inline-block; border-radius: 999px; padding: .125rem .625rem;
           font-size: .75rem; font-weight: 500; white-space: nowrap; }
  .answer { white-space: pre-wrap; background: #f8fafc; border-radius: .375rem;
            padding: .75rem; font-size: .875rem; color: #334155; margin: 0 0 .75rem; }
  .feedback { font-size: .875rem; color: #475569; margin: 0; }
  .muted { color: #94a3b8; }
  footer { margin-top: 2rem; font-size: .75rem; color: #94a3b8; }
"""


def render_feedback_html(submission: Submission) -> str:
    """Render `submission`'s graded answers as a standalone HTML page.

    `submission.rubric`/`.student`/`.edition` must already be resolved (i.e.
    fetched with `fetch_links=True`, as every submissions/batches endpoint
    does) -- this never re-fetches anything itself.
    """
    rubric = submission.rubric
    identifier = submission.student.name if submission.student else (submission.batch_internal_id or str(submission.id))
    secondary = submission.student.student_id if submission.student else (submission.batch_internal_id or "unmatched")

    notice = ""
    if submission.status in ("pending", "extracting", "grading"):
        notice = '<p class="notice progress">Grading is still in progress -- this export reflects the current, possibly partial, state.</p>'
    elif submission.status == "failed":
        notice = f'<p class="notice failed">Grading failed{": " + html.escape(submission.error) if submission.error else ""}.</p>'

    questions_by_id = {q.id: q for q in rubric.questions}
    sections: list[str] = []
    for answer in submission.answers:
        question = questions_by_id.get(answer.question_id)
        title = question.title if question else answer.question_id

        badge = ""
        feedback = ""
        if answer.grade:
            color = _grade_color(answer.grade.level, rubric.grading_scale)
            if color:
                hex_color, text = color
                style = f"background:{hex_color};color:{'#ffffff' if text == 'white' else '#0b0b0b'};"
            else:
                style = "background:#f1f5f9;color:#475569;"
            badge = f'<span class="badge" style="{style}">{html.escape(_prettify_level(answer.grade.level))}</span>'
            feedback = f'<p class="feedback">{html.escape(answer.grade.feedback)}</p>'

        answer_text = (
            html.escape(answer.student_answer)
            if answer.student_answer
            else '<span class="muted">No answer provided.</span>'
        )
        sections.append(
            f"""
        <section class="question">
          <div class="question-header">
            <h2>{html.escape(title)}</h2>
            {badge}
          </div>
          <p class="answer">{answer_text}</p>
          {feedback}
        </section>"""
        )

    if not sections and submission.status not in ("pending", "extracting", "grading"):
        sections.append('<p class="muted">No answers extracted yet.</p>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(identifier)} &middot; {html.escape(rubric.title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="page">
  <header>
    <h1>{html.escape(identifier)} &middot; {html.escape(rubric.title)}</h1>
    <p class="meta">{html.escape(secondary)} &middot; {html.escape(submission.edition.name)}</p>
  </header>
  {notice}
  {"".join(sections)}
  <footer>Exported from AITAna.</footer>
</div>
</body>
</html>
"""


def feedback_filename(submission: Submission) -> str:
    identifier = submission.student.student_id if submission.student else (submission.batch_internal_id or str(submission.id))
    return f"{identifier}_{submission.rubric.slug}_feedback.html"
