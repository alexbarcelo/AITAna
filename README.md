# AITAna

Automatic first-pass grading of hands-on lab/exam answer sheets. Extracts
`answer1`, `answer2`, ... answers from a submitted file, grades each one
against a rubric using an LLM (via LangChain), and stores the result --
student answer plus feedback -- as a **submission** document, so a TA can
review it and later analyze results across students/rubrics.

The submission format is pluggable per rubric (`Rubric.format`): a filled-in
PDF form with named AcroForm text fields, or a Jupyter notebook with answer
cells tagged `aitana:answer` -- see "Submission formats" below.

**Status: WIP.** A FastAPI + MongoDB + MinIO + Celery service with a React
frontend. See `configs/AGENTS.md` for the rubric YAML format, and
[`AGENTS.md`](AGENTS.md) for implementation details, known gotchas, and
conventions worth knowing before changing the code.

## Architecture

```
React (Vite) --> FastAPI --> MongoDB (students, courses, editions, rubrics, submissions)
                          --> MinIO (raw submission files)
                          --> enqueues grading on Redis/Celery
                 Celery worker --> MongoDB / MinIO / the LLM provider
```

- **courses** -- a subject, e.g. "BDM". Reused across years.
- **editions** -- a term, e.g. "2026/27" -- **global**, shared by every
  course taught that term, not owned by any one of them. Create "2026/27"
  once and reference it from as many courses' rubrics as need it, rather
  than recreating an identically-named edition per course. Students enroll
  in editions directly (a student can be in several); which course(s) an
  edition applies to is derived from rubrics (see "Creating a rubric"
  below), not stored on the edition itself.
- **students** -- the roster, managed via the API.
- **rubrics** -- one per gradable deliverable (a lab, an exam -- not
  everything is an exam, hence the generic name). Always belongs to a
  course; *optionally* pinned to one edition. Leave the edition unset for
  something reused across years (a lab whose task doesn't change); set it
  for something unique to one term (an exam). `created_at`/`updated_at` are
  how you recognize an updated version of a reused rubric. Fixes which
  **format** (`pdf` or `notebook`) its submissions must be uploaded as --
  see "Submission formats" below. Created either by uploading a YAML file
  or by filling in a form (see below).
- **submissions** -- one per student x rubric: the extracted answers, and,
  once graded, a coarse level + short feedback per question, plus which
  edition it belongs to (see "Creating a submission" below for how that's
  decided). The original file stays in MinIO and can be re-downloaded, and
  grading can be forced to re-run at any time (discarding previous grades).

Grading is asynchronous: uploading a submission returns immediately
(`status=pending`), and a Celery worker extracts answers from the raw file
(using whichever extractor the rubric's format selects) and grades each
question in the background, updating the same document as it goes
(`extracting` -> `grading` -> `graded`, or `failed` with an error).

## Submission formats

A rubric fixes one `format` at creation time (default `pdf`):

- **`pdf`** -- a filled-in PDF with AcroForm text fields named `answer1`,
  `answer2`, etc. (one per `\question` in the lab guide), read via `pypdf`.
- **`notebook`** -- a `.ipynb` file where the cell(s) holding a question's
  answer carry a Jupyter cell tag `aitana:answer` and further metadata
  in an `aitana` field in the cell's metadata. (These fields are accessible
  within Jupyter/JupyterLab's built-in "Cell Tags" UI and "Advanced tools",
  no custom extension needed). 
  Multiple tagged cells sharing the same id are concatenated in
  notebook order. Parsed by reading the `.ipynb` JSON directly (no
  `nbformat` dependency -- see `src/aitana/grading/extraction/notebook.py`'s
  module docstring for why).

A question's `field` (defaults to `id`) is the answer-slot name under
whichever format the rubric uses -- the PDF field name or the notebook tag
suffix. Adding a third format means writing one extractor function in
`src/aitana/grading/extraction/` and registering it there; see
`AGENTS.md`'s "Pluggable submission formats" section for the full shape.

## Editions are global

An edition (e.g. "2026/27") is created once and shared across every course
taught that term -- it carries no course of its own. Create it on the
Editions page (or `POST /editions`, just a `name`), then reference it from
as many rubrics as need it (`Rubric.course` + `Rubric.edition` together are
what actually tie one course's rubric to one edition). Which courses use a
given edition, and which editions a given course's rubrics use, are both
computed from that rubric data and shown on the Editions and Courses pages
respectively -- neither is a separate thing you manage directly.

This replaced an earlier design where an edition belonged to exactly one
course, which meant creating "2026/27" once per course produced multiple,
identically-named, unrelated editions -- confusing everywhere an edition
picker showed up (e.g. uploading a submission). See `AGENTS.md`'s "Data
model" section for the full history.

## Setup

```bash
uv sync --group dev
cp .env.example .env   # fill in the LLM provider key(s) you'll use
docker compose up -d mongo minio redis
```

Run the API and worker (either via Docker or locally):

```bash
docker compose up api worker
# or, locally:
uv run uvicorn aitana.api.main:app --reload
uv run celery -A aitana.worker.celery_app.celery_app worker --loglevel=info
```

And the frontend:

```bash
cd frontend && npm install && cp .env.example .env && npm run dev
```

Or bring up everything at once: `docker compose up`.

## Repository layout

```
src/aitana/
  settings.py    pydantic-settings config, read from the environment/.env
  db.py          Beanie/PyMongo initialization (shared by API + worker)
  storage.py     MinIO wrapper (upload/download bytes)
  slugify.py     shared slug-from-text helper (rubrics, courses, editions)
  documents/     Beanie documents: Student, Course, Edition, Rubric, Submission
  grading/       extraction/ (pluggable per-format answer extraction: pdf,
                 notebook), prompt building, LLM provider factory --
                 mostly carried over from the original CLI PoC as-is
  api/           FastAPI app + routers (students, courses, editions, rubrics,
                 submissions)
  worker/        Celery app + the grade_submission background task
configs/         example rubric YAMLs, uploadable via the API (see configs/AGENTS.md)
frontend/        React + TypeScript + Vite app
tests/           pytest suite (API routers + grading logic)
```

## API (first pass)

- `POST /students`, `GET /students`, `GET /students/{id}`
- `POST /students/import` (multipart: `file`, `format` -- currently only
  `atenea`) -- bulk-create/update students from a roster CSV. `ID number`
  and `First name` are required columns; matches existing students by `ID
  number` and overwrites `name`/`username`/`email`/`group` wholesale. A row
  missing `ID number` or `First name`, or two rows sharing an `ID number`,
  aborts the whole import with `422` (nothing is written). See AGENTS.md's
  "Batch student import" for the column mapping.
- `POST /courses` (JSON body: `name`, optional `slug`), `GET /courses`,
  `GET /courses/{id}` -- e.g. a course named "BDM"
- `POST /editions` (JSON body: `name`, optional `slug`), `GET /editions`,
  `GET /editions/{id}` -- e.g. edition "2026/27", global (no course
  involved) -- see "Editions are global" above.
- `GET /rubrics`, `GET /rubrics/{id}`
- `POST /rubrics` (JSON body: `title`, optional `slug`, `course_id`
  required, optional `edition_id`, optional `format` (`pdf`/`notebook`,
  defaults to `pdf`), optional `grading_scale` (`{level_id: description}`,
  defaults to the 4-level scale), `questions`) -- create a rubric
  field-by-field ("fill a form")
- `POST /rubrics/upload` (multipart: `yaml_file`, optional `slug`/
  `course_id`/`edition_id`/`format`) -- create a rubric from a YAML file
  (see below)
- `POST /rubrics/{id}/test-answer` (JSON body: `question_id`, `answer`) --
  grade one free-typed answer against a single question of this rubric,
  synchronously, with no submission/file/student involved and nothing
  persisted. Lets you try out a rubric's wording and see the LLM's actual
  grade + feedback before uploading a real exam -- see "Testing a rubric
  interactively" below.
- `POST /submissions` (multipart: `file`, `student_id`, `rubric_id`,
  optional `edition_id`) -> `202`, enqueues grading. `edition_id` is
  required only if the chosen rubric has no fixed edition of its own (see
  "Creating a submission" below). The uploaded file's format is dictated by
  the chosen rubric's `format`, not chosen per-upload -- see "Submission
  formats" above.
- `GET /submissions/{id}` -- status + per-question answers/grades
- `GET /submissions?student_id=&rubric_id=&course_id=&edition_id=&status=`
  -- filtering/listing. `course_id` matches submissions whose *rubric*
  belongs to that course; `edition_id` matches submissions whose own
  `edition` field is that edition -- these are different relationships
  (see Architecture above), so combining both only returns results where
  both happen to hold.
- `GET /submissions/{id}/file` -- streams the original uploaded file back
  (proxied through the API, not a MinIO presigned URL -- see AGENTS.md for why)
- `POST /submissions/{id}/regrade` -> `202` -- force a submission back
  through extraction+grading from scratch, discarding its current
  grades/feedback (e.g. after fixing a rubric, or retrying a `failed` run)

Rubric/course/edition creation endpoints return `409` if the (given or
derived) `slug` already exists -- there's no update/edit endpoint for any
of them yet, only creation.

## Creating a rubric

The frontend's Rubrics page offers both ways to create one: upload a YAML
file, or fill in a form (title + a dynamic list of questions). Via the API
directly:

```bash
# from a YAML file
curl -X POST http://localhost:8000/rubrics/upload \
  -F "course_id=<course id>" -F "yaml_file=@configs/containers_questions.yaml"

# field-by-field
curl -X POST http://localhost:8000/rubrics -H 'Content-Type: application/json' -d '{
  "title": "Containers lab",
  "course_id": "<course id>",
  "questions": [
    {"id": "answer1", "title": "Docker network isolation",
     "question": "Explain why...", "rubric": "A good answer...", "expected_points": ["..."]}
  ]
}'
```

### YAML format

`configs/*_questions.yaml` holds example/reference rubrics in the expected
shape -- see `configs/AGENTS.md` for the full format and authoring
conventions:

```yaml
title: Containers lab    # optional; falls back to a title-cased slug
course_slug: containers  # optional alternative to the course_id form field
format: pdf              # optional, defaults to pdf; notebook is the other value
grading_scale:           # optional; {level_id: description}, worst -> best; defaults to the 4-level scale below
  not_attempted: Blank, off-topic, or shows no understanding of the question.
  some_effort: On-topic but misses most key points or has major misconceptions.
  almost_there: Covers most key points with minor gaps or imprecision.
  solid: Covers the key points correctly and shows clear understanding.
questions:
  - id: answer1          # label; also the default answer-slot name (PDF field / notebook tag)
    field: answer1       # optional override if the answer slot is named differently
    title: "Short question title"
    question: |
      The actual question/task text posed to the student.
    context: |            # optional -- environment/state info, handy for a lab
      What the student's environment looks like at this point.
    rubric: |
      Grading instructions: what a good answer contains, common misconceptions.
    expected_points:
      - bullet list of things a good answer covers
```

The rubric's `slug` (its stable id) comes from, in order: the `slug` form
field on upload, a top-level `slug:` key in the YAML, or the uploaded
filename with `_questions` stripped and slugified (e.g.
`containers_questions.yaml` -> `containers`).

Its course (required) comes from, in order: the `course_id` form field, or
a top-level `course_slug:` key in the YAML -- matching an existing course's
slug (`GET /courses` to look one up); uploading without either is a `422`.
Its edition (optional) works the same way via `edition_id`/`edition_slug:`
(quote the slug if it looks like a number, e.g. `"2026_27"` -- see
`configs/AGENTS.md`).
Its format (optional, defaults to `pdf`) works the same way via a `format`
form field or a top-level `format:` key. Its grading scale (optional,
defaults to the 4-level scale above) has no form-field equivalent -- only
the top-level `grading_scale:` key, or the frontend's "Build manually" form
(which offers a few quick presets plus free-form editing).

## Creating a submission

Uploading a submission needs a rubric and an edition. If the chosen rubric
has a fixed edition (e.g. an exam), that edition is used automatically --
the frontend's upload form shows it read-only. If the rubric doesn't fix
one (e.g. a lab, reused across editions), the uploader picks which edition
this particular submission belongs to, from the full (global) list of
editions -- not scoped to the rubric's course, since editions aren't scoped
to a course at all (see "Editions are global" above).

## Testing a rubric interactively

Before wiring up a real PDF/notebook and a student, you can try out a
rubric's wording directly: open a rubric's detail page, expand "Test this
question" under any question, type a sample answer, and grade it. This
calls the exact same `grade_answer` prompt/grading-scale logic a real
submission would go through, but synchronously and with nothing persisted
-- no `Submission`, no file, no MinIO, no Celery job. It's the fastest way
to check whether a rubric's `question`/`rubric`/`expected_points`/
`grading_scale` actually produce the grade you expect before pointing a
real exam at it.

## How grading works

For each question in the rubric:
- `question` (the task as posed) + `context` (optional environment/state
  info) + `rubric` (grading instructions) + `expected_points` + the
  rubric's `grading_scale` (level ids and descriptions, see above) become
  the LLM's **system prompt**.
- The student's extracted answer text is the **user/human message**.
- The LLM returns a structured grade (`src/aitana/grading/models.py`): one
  of that rubric's `grading_scale` level ids (the default scale's four are
  `not_attempted` / `some_effort` / `almost_there` / `solid`), plus 2-3
  sentences of feedback aimed at the student's specific gaps. The set of
  valid levels is enforced per grading call, not a fixed global list --
  each rubric can define its own scale (see `AGENTS.md`'s "Pluggable
  grading scales").

Blank answers are graded as the rubric's `grading_scale`'s first (worst)
level without an LLM call -- `not_attempted` for the default scale.

The LLM provider/model the worker grades with is set via `LLM_PROVIDER`/
`LLM_MODEL` in `.env` -- one of `openai`, `openrouter`, or `ollama` (no
Anthropic, by design -- see `src/aitana/grading/llm.py`).

## Tests

```bash
uv run pytest
```

Router tests run against an in-memory Mongo double (`mongomock-motor`) with
MinIO/Celery swapped for in-process fakes -- no live services needed. One
known gap: `mongomock`'s aggregation emulation doesn't fully support the
`$lookup`-based queries Beanie's `fetch_links=True` compiles to, so
full-document resolution of a Link field (a submission's `student`/
`rubric`/`edition`, a rubric's `course`/`edition`) is only exercised against
a real MongoDB (verified manually; see AGENTS.md and the comments in
`tests/test_submissions_api.py`/`tests/test_rubrics_api.py`).

## Known limitations / TODO

- No authentication/authorization -- anyone who can reach the API can read
  or create anything. Fine for a trusted local/testing deployment, not for
  a public one.
- No *editing* of rubrics, courses, or editions -- creation only; a
  duplicate slug is rejected (`409`) rather than updating the existing
  document.
- `with_structured_output` needs tool-calling support; not verified against
  small/local Ollama models. May need a manual JSON-parsing fallback.
