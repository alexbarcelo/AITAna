# AITAna

Automatic first-pass grading of hands-on lab/exam answer sheets. Extracts
`answer1`, `answer2`, ... answers from a submitted file, grades each one
against a rubric using an LLM (via LangChain), and stores the result --
student answer plus feedback -- as a **submission**, so a TA can
review it and later analyze results across students/rubrics.

The submission format is pluggable per rubric (`Rubric.format`): a filled-in
PDF form with named AcroForm text fields, a Jupyter notebook with answer
cells tagged `aitana:answer`, or a generic document (any PDF, or a Word
`.docx`) split into answers by the professor (optionally LLM-assisted) -- see "Submission formats" below.

**Status: WIP.** A FastAPI + PostgreSQL + MinIO + Celery service with a React
frontend. See [`AGENTS.md`](AGENTS.md) for implementation details, known
gotchas, and conventions worth knowing before changing the code.

## Architecture

```
React (Vite) --> FastAPI --> PostgreSQL (students, courses, editions, rubrics, submissions)
                          --> MinIO (raw submission files)
                          --> Gotenberg (.docx -> PDF, LibreOffice)
                          --> enqueues grading on Redis/Celery
                 Celery worker --> PostgreSQL / MinIO / the LLM provider
```

- **courses** -- a subject, e.g. "BDM". Reused across years.
- **editions** -- a term, e.g. "2026/27" -- **global**, shared by every
  course taught that term, not owned by any one of them. Create "2026/27"
  once and reference it from as many courses' rubrics as need it, rather
  than recreating an identically-named edition per course. Which course(s)
  an edition applies to is derived from rubrics (see "Creating a rubric"
  below), not stored on the edition itself.
- **students** -- the roster, managed via the API.
- **rubrics** -- one per gradable deliverable (a lab, an exam -- not
  everything is an exam, hence the generic name). Always belongs to a
  course; *optionally* pinned to one edition. Leave the edition unset for
  something reused across years (a lab whose task doesn't change); set it
  for something unique to one term (an exam). `created_at`/`updated_at` are
  how you recognize an updated version of a reused rubric. Fixes which
  **format** (`pdf_form`, `notebook` or `generic_document`) its submissions must be uploaded as --
  see "Submission formats" below. Created either by uploading a YAML file
  or by filling in a form (see below).
- **submissions** -- one per student x rubric: the extracted answers, and,
  once graded, a coarse level + short feedback per question, plus which
  edition it belongs to (see "Creating a submission" below for how that's
  decided). The original file stays in MinIO and can be re-downloaded, and
  grading can be forced to re-run at any time (discarding previous grades).
  A submission can also be **labelled** -- no roster student, just a
  free-form label (e.g. "Group 3") -- for group projects where matching
  students isn't worth it: tick "Identify by label instead" on the upload form, or untick
  "Match folders to roster students" on a batch upload (each item is then
  labelled by its folder name).

Grading is asynchronous: uploading a submission returns immediately
(`status=pending`), and a Celery worker extracts answers from the raw file
(using whichever extractor the rubric's format selects) and grades each
question in the background, updating the same submission as it goes
(`extracting` -> `grading` -> `graded`, or `failed` with an error).
Generic documents (`generic_document`) first go `converting` -> (`segmenting`) ->
`awaiting_segmentation`, and only get graded once a professor confirms
where each answer is.

## Submission formats

A rubric fixes one `format` at creation time (default `pdf_form`):

- **`pdf_form`** -- a filled-in PDF with AcroForm text fields named `answer1`,
  `answer2`, etc. (one per `\question` in the lab guide), read via `pypdf`.
- **`notebook`** -- a `.ipynb` file where the cell(s) holding a question's
  answer carry a Jupyter cell tag `aitana:answer` and further metadata
  in an `aitana` field in the cell's metadata. (These fields are accessible
  within Jupyter/JupyterLab's built-in "Cell Tags" UI and "Advanced tools",
  no custom extension needed). 
  Multiple tagged cells sharing the same id are concatenated in
  notebook order.
  If the template also tags each (locked, `editable: false`) question cell
  with `aitana:question` + the same id, every editable cell after it -- up
  to the next locked cell -- is part of that question's answer too, even
  without a tag (students recreating or adding cells). Those untagged cells
  are counted and flagged in the UI: per answer, per submission, and as an
  extraction report on a batch/rubric's submissions list. Parsed by reading the `.ipynb` JSON directly (no
  `nbformat` dependency -- see `src/aitana/grading/extraction/notebook.py`'s
  module docstring for why).

- **`generic_document`** ("Generic document" in the UI) -- any PDF, no form
  fields needed, or a Word `.docx`, which is converted to PDF on upload by
  LibreOffice running in a [Gotenberg](https://gotenberg.dev) sidecar, so
  the PDF is what gets graded and shown in the editor (the original `.docx`
  is kept alongside it and can be downloaded). With `GOTENBERG_URL` left
  blank it falls back to PyMuPDF's own `Document.convert_to_pdf`, whose
  layout of tables and complex formatting is much rougher. Right after upload
  it's converted to Markdown with PyMuPDF (`Document.to_markdown`, backed
  by `pymupdf4llm`), images included. Then each answer has to be located
  -- **segmentation**: in the segmentation editor (PDF and numbered
  Markdown lines side by side) the professor clicks the line where each
  answer starts, and optionally where it ends to drop dead lines between
  answers. With **LLM-assisted segmentation** (an upload/batch option, or
  "Suggest with LLM" in the editor) the active model proposes the start
  lines first; it's only a suggestion, nothing is graded until the
  professor clicks "Save & grade". Each answer's Markdown (and, with
  `include_images`, its images) is what the grading LLM receives. A
  question's `field` is an optional hint for the LLM: the label the
  question has in the student's document (e.g. `Exercise 3`).

  PyMuPDF and pymupdf4llm are AGPL-3.0 licensed: running AITAna as a
  network service means offering its source code to its users.

A question's `field` (defaults to `id`) is the answer-slot name under
whichever format the rubric uses -- the PDF field name or the notebook tag
suffix (or, for `generic_document`, the segmentation hint above).
Adding another format means writing one extractor function in
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

## Setup

```bash
uv sync --group dev
cp .env.example .env   # fill in the LLM provider key(s) you'll use
docker compose up -d postgres minio redis gotenberg
```

(`gotenberg` converts `.docx` uploads to PDF with LibreOffice. Leaving
`GOTENBERG_URL` blank falls back to PyMuPDF's much rougher built-in conversion.)

Run the API and worker (either via Docker or locally):

```bash
docker compose up api worker   # the api container runs `alembic upgrade head` first
# or, locally (see AGENTS.md "Environment variables" for the host-side URLs):
uv run alembic upgrade head
uv run uvicorn aitana.api.main:app --reload --port 8080
uv run celery -A aitana.worker.celery_app.celery_app worker --loglevel=info
```

The database schema is managed by Alembic migrations
(`src/aitana/migrations/`); the API never creates tables itself. A database
GUI (DbGate) is at http://localhost:8081 with `docker compose up`.

And the frontend:

```bash
cd frontend && npm install && cp .env.example .env && npm run dev
```

Or bring up everything at once: `docker compose up`.

## Deployment (Kubernetes)

Kustomize manifests are in `deploy/` (a `base/` plus an `overlays/example/` to
copy per environment). Multi-arch (amd64/arm64) images are built by GitHub
Actions (`.github/workflows/images.yml`) and pushed to
`ghcr.io/alexbarcelo/aitana-api` and `ghcr.io/alexbarcelo/aitana-frontend`.
PostgreSQL, S3-compatible object storage and the `aitana-secrets` Secret come
from outside the manifests; the example overlay shows an in-cluster PostgreSQL
run by the CloudNativePG operator. See [`deploy/README.md`](deploy/README.md).

## Repository layout

```
src/aitana/
  settings.py    pydantic-settings config, read from the environment/.env
  storage.py     MinIO wrapper (upload/download bytes)
  slugify.py     shared slug-from-text helper (rubrics, courses, editions)
  db/            SQLAlchemy models (Course, Edition, Student, Rubric, Batch,
                 Submission, GradingTrace, LlmModel), engine/session setup
  migrations/    Alembic migrations (the database schema)
  grading/       extraction/ (pluggable per-format answer extraction: pdf_form,
                 notebook, generic_document), prompt building, LLM provider factory
  api/           FastAPI app, routers and request/response schemas
  worker/        Celery app + the background tasks (conversion, grading)
frontend/        React + TypeScript + Vite app (nginx/ = production proxy config)
deploy/          Kustomize manifests (base + example overlay)
tests/           pytest suite (API routers + grading logic)
```

## API documentation

First start the API (either with `uv run` or `docker compose up`, see
[Setup](#Setup)).

Then the documentation will be available at http://localhost:8080/docs

Note that the OpenAPI json file is available at http://localhost:8080/openapi.json

## Creating a rubric

The frontend's Rubrics page offers both ways to create one: upload a YAML
file, or fill in a form (title + a dynamic list of questions). Via the API
directly:

```bash
# from a YAML file
curl -X POST http://localhost:8080/rubrics/upload \
  -F "course_id=<course id>" -F "yaml_file=@containers_questions.yaml"

# field-by-field
curl -X POST http://localhost:8080/rubrics -H 'Content-Type: application/json' -d '{
  "title": "Containers lab",
  "course_id": "<course id>",
  "questions": [
    {"id": "answer1", "title": "Docker network isolation",
     "question": "Explain why...", "rubric": "A good answer...", "expected_points": ["..."]}
  ]
}'
```

### YAML format

```yaml
title: Containers lab    # optional; falls back to a title-cased slug
course_slug: containers  # optional alternative to the course_id form field
format: pdf_form         # optional, defaults to pdf_form; also notebook, generic_document
grading_scale:           # optional; defaults to the 4-level scale below
  # Each level: an id (what the LLM answers), a description (what it grades
  # against) and a score. Exactly one level must score 0: it's what a blank
  # answer gets. Levels rank by score, so list order doesn't matter.
  - {id: not_attempted, score: 0, description: Blank, off-topic, or shows no understanding of the question.}
  - {id: some_effort,   score: 1, description: On-topic but misses most key points or has major misconceptions.}
  - {id: almost_there,  score: 2, description: Covers most key points with minor gaps or imprecision.}
  - {id: solid,         score: 3, description: Covers the key points correctly and shows clear understanding.}
context: |               # optional -- background shared by every question (lab setup, dataset...)
  What the whole lab/exam is about, sent with every question's grading prompt.
general_feedback_instructions: |  # optional -- one "general feedback" for the whole submission
  Two sentences: the strongest point overall, then the one thing to review first.
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
(When *updating* a rubric from YAML, `course_slug:`/`edition_slug:` are
ignored and the course/edition stay as they are unless the form fields say
otherwise.)
Its edition (optional) works the same way via `edition_id`/`edition_slug:`
(quote the slug if it looks like a number, e.g. `"2026_27"`).
Its format (optional, defaults to `pdf_form`) works the same way via a `format`
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
-- no submission, no file, no MinIO, no Celery job. It's the fastest way
to check whether a rubric's `question`/`rubric`/`expected_points`/
`grading_scale` actually produce the grade you expect before pointing a
real exam at it.

## How grading works

For each question in the rubric:
- the rubric's own `context` (optional, shared by every question) +
  `question` (the task as posed) + `context` (optional environment/state
  info for this question) + `rubric` (grading instructions) + `expected_points` + the
  rubric's `grading_scale` (level ids and descriptions, worst to best by
  score -- the scores themselves aren't sent) become the LLM's **system prompt**.
- The student's extracted answer text is the **user/human message**.
- The LLM returns a structured grade (`src/aitana/grading/models.py`): one
  of that rubric's `grading_scale` level ids (the default scale's four are
  `not_attempted` / `some_effort` / `almost_there` / `solid`), plus 2-3
  sentences of feedback aimed at the student's specific gaps. The set of
  valid levels is enforced per grading call, not a fixed global list --
  each rubric can define its own scale (see `AGENTS.md`'s "Pluggable
  grading scales").

If the rubric has `general_feedback_instructions`, one more LLM call follows once
every question is graded: it gets the rubric's `context`, its grading scale
and, for each question, its title, grade level and feedback (the TA's manual
grade if there is one -- not the student's answers), and digests them into a
single short **general feedback** following those instructions. It's shown
on the submission page and at the top of the feedback export. A TA can
override its text, or **regenerate** it from the current grades (e.g.
after overriding some of them) without re-grading anything else. Without
`general_feedback_instructions`, no general feedback is written.

Blank answers get the rubric's score-0 level without an LLM call --
`not_attempted` for the default scale. The grading overview's **Score** is
the graded answers' total score over the best possible total.

Grading models are managed in the web UI: the **Model** picker (top right)
selects the *active* model this browser grades with, and **Manage models...**
registers new ones (provider + model identifier, e.g. `openai` /
`gpt-4o-mini`, plus an optional reasoning effort), marks one as the *default* (used when nothing else is
picked) and others as favorites. Only providers whose package is installed
and whose key (or, for Ollama, `OLLAMA_BASE_URL`) is set in `.env` can be
picked -- credentials never leave the environment. The stock Docker image
installs `openai`, `ollama` and `openrouter`; Anthropic needs `--extra
anthropic` (see `AGENTS.md`). `LLM_PROVIDER`/`LLM_MODEL`/`LLM_REASONING_EFFORT`
in `.env` only seed the first default model.

## Tests

```bash
uv run pytest
```

The suite runs against a real PostgreSQL: by default a throwaway
`postgres:17-alpine` container started through testcontainers (needs Docker),
or any empty database you point `TEST_DATABASE_URL` at. The schema comes from
the Alembic migrations, and each test is rolled back at the end. MinIO and
Celery are in-process fakes.

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/aitana_test uv run pytest
```

## Known limitations / TODO

- No authentication/authorization -- anyone who can reach the API can read
  or create anything. Fine for a trusted local/testing deployment, not for
  a public one.
- No *editing* of courses or editions -- creation only; a duplicate slug is
  rejected (`409`) rather than updating the existing row.
- `with_structured_output` needs tool-calling support; not verified against
  small/local Ollama models. May need a manual JSON-parsing fallback.
