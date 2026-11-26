# AGENTS.md

Implementation notes for whoever (human or AI) next touches this codebase.
`README.md` explains what the project does and how to run it; this file
explains *why* the code is shaped the way it is, plus the sharp edges you'll
otherwise rediscover the hard way.

## Naming

- **`Rubric`**: a rubric is associated to a lab *or* an exam.
- **`Submission`**: a student's deliverable against a rubric. 

## Data model

PostgreSQL, through SQLAlchemy 2.0 (async, asyncpg). The models are in
`src/aitana/db/models.py`; the schema itself is owned by Alembic migrations
(see "Database migrations"). Every table has a `BIGINT GENERATED ALWAYS AS
IDENTITY` primary key `id`, which the API exposes as is (`id: int`, and
`{..._id}` path/query parameters are ints).

Relations are foreign keys. Nested data that always belongs to one row and is
read and written as a whole (a rubric's questions and grading scale, a
submission's answers, a grading trace) lives in JSONB columns, typed by the
Pydantic models in `grading/models.py` through `PydanticJSONB`
(`db/types.py`) -- so ORM attributes are real `Question`/`AnsweredQuestion`/...
instances, never raw dicts. Read "Database layer rules" below before writing
code that touches either.

- **`Course`** (`courses`) -- a subject, e.g. "BDM". `slug` is unique, derived
  with `slugify` (`src/aitana/slugify.py`, shared with rubrics and editions).
  Reused across years -- it carries no term/year.
- **`Edition`** (`editions`) -- a term, e.g. "2026/27". **Global -- no course
  column.** Just `{name, slug, created_at}`, created once (`POST /editions`)
  and referenced from as many courses' rubrics as use that term. Which
  course(s) an edition applies to is derived, never stored: a `Rubric` pins
  one course *and* (optionally) one edition -- the only place the two are
  tied together. The frontend computes "editions used by this course" /
  "courses using this edition" by scanning rubrics
  (`frontend/src/lib/editions.ts`), shown on the Courses and Editions pages.
- **`Student`** (`students`) -- roster entry. `student_id` (the institutional
  id, not the primary key) is unique.
- **`Rubric`** (`rubrics`) -- one per lab/exam. `slug` is unique; see
  "Creating rubrics" for how it's derived. `course_id` is a **required** FK
  (every rubric belongs to a course); `edition_id` is an **optional** FK (a
  lab is often unchanged between editions, left unset and reused as is; an
  exam is typically unique to one edition, set it). `created_at`/`updated_at`
  (not a version number) are how a TA recognizes "this is an updated version"
  of a reused rubric. `format: SubmissionFormat` (default `pdf_form`) fixes
  which file format this rubric's submissions are uploaded as -- see
  "Pluggable submission formats" for why it lives on the rubric, not the
  submission. `grading_scale` (JSONB, a list of `GradingLevel`) is what the
  LLM grades against -- see "Pluggable grading scales". `context` is
  optional rubric-wide background shared by every question (see "Grading
  internals"). `questions` is a JSONB list of `Question`.
- **`Batch`** (`batches`) -- one per zip upload (`api/routers/batches.py`).
  `rubric_id` and `edition_id` are both required FKs, resolved the same way a
  single submission's are (see "Batch submission import"). No course column,
  same as rubrics/submissions never storing one either (always derivable
  through the rubric). `type: BatchType` records which zip layout was used
  (`moodle` today). `item_count` is set once, at creation.
- **`Submission`** (`submissions`) -- normally one per student x rubric.
  `rubric_id` and `edition_id` are required FKs, `student_id` and `batch_id`
  optional ones. `edition_id` is set on *every* submission, even when its
  rubric has no fixed edition -- see "Creating a submission" for how it's
  resolved; this is what makes `edition_id` filtering direct.
  `file_object_key` points at the stored file in MinIO (format-agnostic name,
  since it might be a notebook; see "Pluggable submission formats").
  `answers` (JSONB list of `AnsweredQuestion`) fills in as grading
  progresses (each one carries the LLM's `grade` plus an optional TA
  `manual_grade` -- see "Manual grade overrides"), and `status` drives the
  frontend's polling. `student_id` is unset for a batch item not matched to
  the roster (yet) and for a labelled submission (see "Labelled
  submissions"); grading doesn't need a student at all. `batch_id` and
  `batch_internal_id` are set together, only for a batch-created submission
  -- `batch_internal_id` is that item's per-student folder name from the zip,
  kept verbatim (e.g., for a Moodle batch, `<surname(s)>
  <name>_<internal_id>_assignsubmission_file`), so the feedback zip export
  can write each file back under the folder name the LMS expects.
- **`GradingTrace`** (`grading_traces`) -- one per real LLM grading call; see
  "Grading traces".
- **`LlmModel`** / **`FavoriteLlmModel`** (`llm_models`,
  `favorite_llm_models`) -- see "Choosing the grading model".

**Deletes.** Foreign keys are `ON DELETE RESTRICT`, except
`grading_traces.submission_id` and `favorite_llm_models.llm_model_id`
(`CASCADE`: traces go with their submission, favorites with their model).
Endpoints that refuse a delete because something still references the row
(a rubric with submissions/batches, a student with submissions) check first
and answer `409` naming what's in the way; the FK is only the backstop.

`AnsweredQuestion` lives in `db/models.py` (it only ever exists inside a
submission's `answers`). `Question`, `Grade`, `GradingLevel`, `GradeTrace`
and the other grading-time shapes live in `src/aitana/grading/models.py`,
*not* in `db/`, on purpose: they're plain Pydantic models reused in two
places -- as the grading-time input/output (`grading/grading.py`'s
`grade_answer`) and stored verbatim in JSONB columns. Keeping them
DB-agnostic means `grading/` has zero database dependency and can be
unit-tested (or reused) without a database at all. Don't make grading logic
depend on `db/` -- it would tangle a pure function with ORM state for no
benefit.

`Question` has both a `title` (short label, UI-only -- lists/headers) and a
`question` (the actual task text sent to the LLM) -- don't conflate them;
`title` is never part of the grading prompt. `context` is optional
environment/state info (mainly useful for a lab: what the student's
environment already looks like at this point) included in the prompt only
when set. `model_answer` is likewise optional: a worked/canonical answer,
for the subset of questions that actually have one (e.g. "what command
fixes this") as opposed to an open-ended one -- also included in the prompt
only when set. See "Grading internals" below for exactly how these compose
into the system prompt. `Question.field` (exposed as the `answer_key`
property, `field or id`) names the answer slot under the rubric's format --
see "Pluggable submission formats".

**API shapes** are in `api/schemas.py`: `*Out` response models read straight
off the ORM objects (`from_attributes`), plus `RubricIn` (the rubric body
every create/edit path validates through). Nested objects in a response are
exactly what the endpoint eager-loaded (the `*_LOADS` tuples in
`db/models.py`):

- `RubricOut`: the rubric with its `course` and `edition` objects.
- `SubmissionOut`: `student`, `edition`, and `rubric` as a `RubricSummaryOut`
  (the rubric with `course_id`/`edition_id`, no nested objects -- no
  submission view needs them), plus `batch_id`.
- `BatchOut`: `rubric` (summary) and `edition`.

## Database migrations (`src/aitana/migrations/`, `alembic.ini`)

The schema is created and changed only by Alembic, never by the app at
startup (no `create_all`). `alembic upgrade head` runs before the API
starts: the compose `api` service runs it in its command, and the
Kubernetes api Deployment in a `migrate` initContainer. The worker never
migrates.

- **Changing a model means adding a revision**: `uv run alembic revision
  --autogenerate -m "..."` against a database at `head`, then read and fix
  the generated file (autogenerate misses renames, and data migrations are
  hand-written). Never edit a revision that has been applied anywhere.
  `alembic check` fails when the models and the migrations disagree -- the
  test suite's migrated schema is what the tests run against, so a forgotten
  revision shows up there too.
- `migrations/env.py` reads `DATABASE_URL` through the app settings (tests
  pass a URL through `config.attributes["database_url"]` instead), and holds
  a PostgreSQL advisory lock for the whole run, so several api replicas
  starting at once migrate one at a time instead of racing.
- Its `render_item` hook makes autogenerate write `PydanticJSONB` columns as
  plain `postgresql.JSONB()`: revisions never import app code, so an old
  revision keeps working whatever happens to the models later.
- Enums (`format`, `status`, ...) are stored as plain strings (VARCHAR, no
  native PostgreSQL enum, no CHECK constraint -- `_str_enum` in
  `db/models.py`), so adding a member needs no migration.

## Pluggable submission formats

Multiple document format are accepted (at the moment: AcroForm PDF documents,
Jupyter Notebook .ipynb files, and generic documents -- PDF or `.docx`; the
latter has its own section below, "Generic-document submissions"). The format of the document
conditions the extraction step.

- **`SubmissionFormat`** (`grading/models.py`) is the enum of supported
  formats -- currently `pdf_form`, `notebook` and `generic_document`. It lives in `grading/models.py`
  next to `Question`/`Grade`, not in `db/`, for the same
  DB-agnostic-reuse reason those do (see above).
- **Format lives on the `Rubric`, not the `Submission`.** A rubric's
  `questions[].field` values only make sense under one format (a PDF
  AcroForm field name vs. a notebook cell tag) -- a rubric authored for one
  format can never sensibly accept the other, so asking the uploader to
  repick format per-file would just be a second place that can disagree
  with the rubric it's submitted against. `Rubric.format` is set once, at
  rubric-creation time (`POST /rubrics`'s JSON body, or `POST
  /rubrics/upload`'s `format` form field / top-level `format:` YAML key,
  same precedence order as `course_slug`/`edition_slug`), and defaults to
  `pdf_form`. Editable via the edit endpoints (see
  "Editing rubrics" below), but changing `format` on a rubric that already
  has submissions against it is rejected with `409` (`_guard_format_change`)
  -- a submission doesn't record its own format, only `rubric.format` at
  grading time, so allowing the flip would make existing submissions'
  stored answers permanently unre-explainable.
- **Extraction is pluggable** (`grading/extraction/`): `__init__.py`
  exposes `extract_answers(fmt, path) -> ExtractionResult`, dispatching
  to `pdf_form.py` or `notebook.py` via a `_EXTRACTORS` dict, plus
  `FORMAT_FILE_INFO: dict[SubmissionFormat, (extension, content_type)]` used
  by both the upload path (`storage.object_key`, extension-mismatch
  validation in `create_submission`) and the download path (`Content-Type`
  + filename extension in `download_submission_file`). Adding a third format
  means writing one `(path) -> ExtractionResult` function in a new sibling
  module and registering it in both dicts -- nothing in `worker/tasks.py` or
  the API routers needs to change.
- **`pdf_form.py`** is the `pypdf`-based AcroForm-field reader (never reports
  untagged cells).
- **`notebook.py`** parses a `.ipynb` submission by loading it as plain JSON
  (stdlib `json.load`, no `nbformat` dependency) and scanning `cells` for the
  Jupyter cell tag `aitana:answer` (`cell.metadata.tags`, Jupyter's own
  built-in per-cell tagging mechanism -- editable via Jupyter/JupyterLab's
  "Cell Tags" UI, no custom extension needed). A cell with that tag must
  also carry `cell.metadata.aitana.id`, matching `Question.answer_key` the
  same way a PDF field name does -- editable via Jupyter/JupyterLab's
  "Advanced Tools" metadata editor. **Deliberately
  not using the `nbformat` package** -- see that module's docstring for the
  full reasoning, in short: `.ipynb` is just JSON, this only ever *reads*
  three well-known fields (`cells`, `cell.source`, `cell.metadata.tags`) and
  never validates/rewrites/version-converts a notebook, which is most of
  what `nbformat` would actually buy over five lines of `dict.get()` calls.
  The one thing given up is auto-upgrading a pre-v4 notebook (`cell.input`
  instead of `cell.source`, an extra `worksheets` nesting) -- v4 has been
  the export format of every current Jupyter/JupyterLab/Colab/VS Code for
  about a decade, so this module just raises a clear error on anything
  older instead of silently misreading it. If that assumption ever breaks
  in practice, that's the point to reconsider `nbformat`, specifically for
  its version converters -- not for routine parsing.
- **Untagged cells (notebook heuristic).** Students delete/recreate answer
  cells (losing the tag) or add scratch cells. A template may tag each
  question-statement cell `aitana:question` (+ `aitana.id`); every
  following *editable* cell up to the next `editable: false` or
  `aitana:question` cell is joined into that question's answer, tag or not.
  An explicit `aitana:answer` id always wins over position; blank/`...`
  cells are skipped. Full rules in `notebook.py`'s docstring. This is why
  extractors return an **`ExtractionResult`** (`grading/models.py`:
  `answers` + `untagged_cells: {answer_key: count}`) instead of a bare
  dict -- the worker copies the count onto `AnsweredQuestion.untagged_cells`
  (`_rebuild_answers`), and the frontend flags it everywhere answers are
  shown (`components/UntaggedCells.tsx`: per-answer badge, per-submission
  summary, and an aggregated per-question/per-submission report above a
  batch- or rubric-filtered submissions list, computed client-side from
  the listed submissions -- no dedicated report endpoint). The count never
  reaches the grading prompt; each picked-up cell is also logged at INFO
  by the worker.
- **`storage.py`** has no format-specific logic at all -- `upload_file(key,
  data, content_type)` and `object_key(rubric_slug, student_id,
  submission_id, extension)` both take the format-derived bits as plain
  parameters (from `FORMAT_FILE_INFO`) rather than assuming PDF.
- `create_submission` (`api/routers/submissions.py`) does one cheap
  extension check (`file.filename` against `FORMAT_FILE_INFO[rubric.format]`,
  plus any `UPLOAD_CONVERSIONS` extension -- see "Generic-document
  submissions") and 422s on an obvious mismatch (e.g. uploading a `.ipynb` against a
  `pdf_form`-format rubric) -- this is a courtesy check on the *filename*, not a
  content sniff, so a mislabeled file can still slip through to a confusing
  extraction failure at grading time; it just catches the common "wrong
  file picked in the form" case early.

## Generic-document submissions (`generic_document`, "segmentation")

"Generic document" in the UI. A PDF with no form fields (or a `.docx`, see below): nothing in the file says which part answers
which question, so a professor has to say it -- **segmentation** (each
answer's span is a **segment**). Code: `grading/extraction/generic_document.py`
(pure: conversion, segment validation/slicing), `grading/segmentation.py`
(LLM suggestion), `worker/tasks.py` (`prepare_submission`,
`suggest_segments_task`, the `SEGMENTED_FORMATS` branches),
`api/routers/submissions.py` (bottom section), `pages/SegmentationEditorPage.tsx`.

- **`.docx` is converted to PDF at upload, in the API request**
  (`generic_document.document_to_pdf`). Which extra extensions a format accepts is
  `extraction.UPLOAD_CONVERSIONS`; `submissions.py`'s `upload_extension` /
  `to_stored_format` do the check and conversion for both `POST
  /submissions` and `POST /batches` (a batch converts every item before
  writing anything, so one unreadable file is a `422` naming it and nothing
  is created). The PDF is *the* stored file -- `FORMAT_FILE_INFO` stays
  `.pdf` and the editor's iframe, `/file` and the Markdown conversion need no
  `.docx` awareness at all. Conversion runs in `run_in_threadpool`.
- **The original upload is kept for provenance**, but only as a side file:
  `store_original_upload` writes the untouched bytes to
  `derived_object_key(file_object_key, "original<ext>")` (so
  `delete_submission_files` removes it with the rest) and sets
  `Submission.original_object_key`; `GET /submissions/{id}/original` serves
  it (`UPLOAD_CONTENT_TYPES`; the uploaded filename via RFC 5987
  `filename*`, since names may be non-ASCII), shown as "Download original"
  on the submission page. Nothing else reads it.
  `Submission.original_filename` (the uploaded name; for a batch item, its
  name inside the zip folder) is set for *every* upload, converted or not.
- **LibreOffice does the `.docx` layout, via a Gotenberg sidecar**
  (`gotenberg` compose service, `POST /forms/libreoffice/convert`;
  `GOTENBERG_URL`/`GOTENBERG_TIMEOUT` settings, read by the router and passed
  in, so `generic_document.py` stays config-free). This replaced PyMuPDF's
  own `Document.convert_to_pdf`, measured on a real exam `.docx`: MuPDF's
  layout made pymupdf4llm emit every table cell on its own line with stray
  `#` headings and lose bold/lists (314 lines); the LibreOffice PDF gave real
  Markdown tables, headings and numbered questions (100 lines). The weak link
  was MuPDF's Office layout, not pymupdf4llm. A sidecar rather than
  LibreOffice in the api image keeps that image ~300 MB lighter; the
  Gotenberg image also ships Carlito/Caladea (Calibri/Cambria metric
  clones), without which word spacing collapses in places. Apache Tika was
  considered and rejected: flat text for PDFs, and its direct `.docx`
  parsing yields no PDF for the editor/download. **`GOTENBERG_URL` unset or
  blank** = fall back to MuPDF (warned once in the log) -- fine for tests
  and quick host runs. **Set but failing** (unreachable, non-200, not a PDF)
  = `DocumentConversionError` -> `422`, deliberately never a silent fallback
  to the poor output. `tests/conftest.py` blanks `GOTENBERG_URL` so a real
  `.env` never makes the suite call a live converter.
- **One coordinate system: Markdown line numbers.** The PDF is converted
  once, right after upload, with `pymupdf.Document.to_markdown`
  (`page_chunks=True`, `write_images=True`). Use the `Document` method, not
  `pymupdf4llm.to_markdown` directly -- pymupdf4llm's docs ask for that
  since PyMuPDF 2.0 (it still needs `pymupdf4llm` installed; `Document`
  delegates to it). The Markdown is normalized (trailing whitespace, blank
  runs collapsed, one blank line between pages) and stored in MinIO
  (`storage.derived_object_key`, next to the PDF). A segment
  (`AnswerSegment`) is `{question_id, start_line, end_line}`, 1-based
  inclusive, into *that stored text*. The LLM sees the same numbers
  (`number_lines`), the editor shows them, grading slices by them -- no
  fuzzy text matching anywhere. **Never regenerate the Markdown silently**:
  a different PyMuPDF version could shift every line. Re-conversion
  (`_prepare`) therefore always drops the segments.
- **Segment semantics.** `start_line=None` = "not answered" (graded blank,
  via `grade_answer`'s short-circuit). `end_line=None` (the default, and what
  the LLM is told to produce unless there are dead lines to skip) = up to
  the line before the next segment's start, or EOF (`resolve_segments`). An
  explicit `end_line` drops dead lines between answers; the LLM may suggest
  one, `suggest_segments` discards it if it's before the start, past EOF or
  reaches into the next answer. A question with no segment at
  all = "not decided yet": `PUT .../segments` with `grade=true` refuses it
  (`422`), and the worker parks the submission back in
  `awaiting_segmentation` if a regrade finds one (e.g. the rubric gained a
  question) instead of grading it as blank.
- **Images are segmented for free.** Image refs are rewritten to stable
  names (`![](img-0003.png)`); `DocumentImage.line` is the line holding the
  ref, and an image belongs to whichever segment contains that line. Only
  `include_images` questions' images are downloaded at extraction time.
  `generic_document` is in `IMAGE_FORMATS`, not in `OUTPUT_FORMATS`.
- **Storage split**: Markdown + images in MinIO; only metadata
  (`Submission.document: ConvertedDocument`, `segments`,
  `segmentation_source`, `segmented_at`) in PostgreSQL -- the submission row
  is rewritten on every grading commit, keep it small.
- **Not in `_EXTRACTORS`**: those are `(raw file path) -> ExtractionResult`;
  this format's answers come from stored Markdown + segments instead.
  `extract_submission_file` branches on `SEGMENTED_FORMATS`
  (`_extract_segmented_submission`), so everything downstream (grading,
  answer thumbnails, feedback export) works unchanged.
- **Status flow**: `pending -> converting -> [segmenting] ->
  awaiting_segmentation` (the "dirty" state) `-> [professor: Save & grade]
  -> pending -> extracting -> grading -> graded`. LLM segmentation is
  **only ever a suggestion** (product decision): nothing auto-grades after
  it. An LLM failure is non-fatal (`awaiting_segmentation` + `error`, the
  professor segments by hand); a conversion failure is `failed`, and a
  regrade of a never-converted submission re-runs conversion. Saving
  segments *without* grading puts the submission back in
  `awaiting_segmentation` even if it was graded (its grades are now stale).
- **Model use**: LLM-assisted segmentation (`llm_segmentation` form field
  on `POST /submissions`/`POST /batches`, default `true`) uses the active
  model; with it off, an upload against a `generic_document` rubric needs no
  model at all (`upload_model`). The suggestion sanitizes the LLM output
  (out-of-range or duplicate lines become `None`) so it always validates.
- **OCR fallback**: PyMuPDF OCRs pages that look scanned when it finds
  Tesseract. A half-installed Tesseract (binary, no language data) makes
  conversion *raise*, so `_to_markdown_chunks` retries once with
  `use_ocr=False`. The Docker image has no Tesseract (no OCR, no failure).
- **Editor** (`/submissions/:id/segments`, full-width -- `App.tsx` drops
  the `max-w-5xl` shell for that route): PDF in an iframe (`GET
  .../file?inline=true#page=N`, remounted on page change since viewers only
  honour `#page` on load -- no pdf.js dependency), Markdown lines tinted per
  segment. Click = start of the active question (then moves on to the next
  unplaced one), shift-click = end. "Save & grade, next" walks a batch's
  `awaiting_segmentation` submissions.
- **License**: PyMuPDF/pymupdf4llm are AGPL-3.0 (this project is GPL-3.0;
  compatible, but network use triggers AGPL's source offer). Accepted as a
  base dependency deliberately.

## Pluggable grading scales

A rubric can define its own scale entirely, or use one of the provided ones
out of the box.

- **`Rubric.grading_scale`** is a list of **`GradingLevel`**
  (`grading/models.py`): `{id, description, score}`. `id` is what the LLM
  answers with and what a `Grade.level` holds; `description` is what the LLM
  grades against; `score` is a number. Defaults to `DEFAULT_GRADING_SCALE`
  (4 levels, scored 0-3).
- **Rules** (the `GradingScale` annotated type, enforced on every rubric
  create/edit through `RubricIn`, so the JSON body and the YAML upload paths
  `422` alike): at least one level, unique ids, and **exactly one level with
  `score == 0`**. Scores are otherwise unconstrained.
- **The score-0 level is what a blank answer gets.** `grade_answer`
  (`grading/grading.py`) short-circuits a blank answer (no text, no images)
  to `blank_level(grading_scale)` without calling the LLM -- no level id is
  hardcoded, so this works for any scale.
- **Levels are ranked by score, never by list position**
  (`ordered_levels`): the prompt lists them worst to best, the
  structured-output `Literal[...]` follows the same order, and the
  frontend's `GradeBadge` colors them along a single-hue ordinal ramp by
  that rank (lightest = lowest score; see its own comment for the palette
  and why a single hue -- an **ordinal** job per the dataviz skill's color
  formula, not a status one). The frontend mirrors these helpers in
  `lib/gradingScale.ts`.
- **Scores never reach the LLM.** The `# Grading` section of the prompt
  (`build_grading_section`) is `- id: description` lines only; the LLM picks
  a level by its description.
- **`Grade.level` is a plain `str`**, not an `Enum` -- which identifiers are
  valid depends on which rubric graded the answer. Validation that an LLM
  call returned one of *this* rubric's levels happens per call instead, via
  `grade_schema_for_scale(grading_scale)`: a `pydantic.create_model()`-built
  one-off schema whose `level` field is a `Literal[...]` over the scale's
  ids, passed to the structured-output call. `grade_answer` then normalizes
  the result into a real `Grade` -- the one-off schema never leaves that
  call.
- This is deliberately *not* a `grading/scales/` plugin package the way
  `grading/extraction/` is for submission formats -- there's no per-scale
  *code* to dispatch to, just data that flows through the same
  prompt-building and structured-output logic regardless of its contents.
  Don't add a scales registry unless a genuinely different *mechanism*
  shows up.
- **Frontend presets, not a fixed list.**
  `frontend/src/api/gradingScalePresets.ts` offers a few starting points
  (the first mirrors `DEFAULT_GRADING_SCALE`, kept in sync by hand -- there's
  no shared source of truth across the language boundary) in the rubric
  form's quick-pick dropdown, which loads into a fully editable
  id/score/description list (`GradingScaleEditor` in `RubricForm.tsx`). The
  editor shows the rules' violations (`gradingScaleProblem`) and disables
  saving until they're fixed.
- No form-field equivalent on `POST /rubrics/upload` the way `format` has one
  (a list of objects doesn't fit a multipart field) -- only the YAML body's
  top-level `grading_scale:` key, or omit it for the default.

## General feedback (`grading/general_feedback.py`)

One short, submission-wide feedback text digesting every question's grade
and feedback. **Named "general feedback", never "overview"**: "grading
overview" is already the students x questions grid (`GradingGrid.tsx`,
`?overview=1`), and the UI/export label is "General feedback" too.

Optional, per rubric: `Rubric.general_feedback_instructions` (unset/blank =
off -- stored as `NULL` when blank). When set, `_grade_submission` makes one
extra LLM call after `_grade_answers` (`_write_general_feedback`) and stores
the result on `Submission.general_feedback` (`GeneralFeedback`: `feedback` +
an `info` dict in the same shape as `AnsweredQuestion.grading_info`, so
`GradingInfo.tsx`'s `GradingDetailsTooltip` renders it).

- **Input**: system prompt = rubric `context` + grading scale (ids and
  descriptions, never scores) + the instructions; human message = per
  question (rubric order) its `title`, level and feedback. It digests the
  grading, it doesn't re-grade: student answers are not sent. Background
  about the assignment belongs in the rubric's `context` (which this call
  sees), not in the instructions -- a per-question `context` is *not* seen.
- **Uses `effective_grade`**, so a TA override counts -- but only as of when
  the general feedback was written. Editing a grade afterwards doesn't
  rewrite it: the frontend (`GeneralFeedbackEditor.tsx`) flags it as stale
  (latest `manual_graded_at` newer than `general_feedback.info.graded_at`)
  and offers **Regenerate**.
- **Regenerate**: `POST /submissions/{id}/general-feedback/regenerate`
  (`?model_id=` like every LLM endpoint). Only the general feedback -- no
  re-extraction, no per-question call (`worker/tasks.py`'s
  `regenerate_general_feedback` task). `422` if the rubric has no
  `general_feedback_instructions`, `409` unless the submission is `graded`.
  It sets `status = grading` before enqueueing (so manual edits get the
  usual `409` and the frontend polls); the task always ends in `graded`.
  Bulk: `POST /submissions/general-feedback/regenerate`
  (`{submission_ids}` -> `{regenerated, skipped}`) -- same, over an id
  list; non-`graded` submissions are skipped instead of failing the
  request (`422` if any involved rubric has no instructions). The
  submissions list's regrade bar offers it on its own line ("Regenerate
  general feedback") when the listed rubric has instructions.
- **Manual override**: `Submission.manual_general_feedback` +
  `manual_general_feedback_at`, next to (not instead of) the LLM's
  `general_feedback`, same rules as `manual_grade`: `PUT
  /submissions/{id}/general-feedback` (`{feedback}`, blank = `422`) /
  `DELETE` to revert, `409` while in progress, survives re-grades and
  regenerations, the LLM text stays visible under it. Allowed even when the
  rubric has no instructions. What the student sees is
  `Submission.effective_general_feedback` (frontend:
  `effectiveGeneralFeedback()`) -- the feedback export goes through it.
- **The LLM text is cleared at the start of every grading run** (it's
  based on the old grades), rewritten at the end;
  `manual_general_feedback` is kept.
- **Not fatal**: a failed call leaves the per-question grades, status
  `graded`, and `error = "General feedback failed: ..."` (shown on the
  submission page for a graded submission).
- No `GradingTrace` row (that table is per question, its `GradeTrace`
  needs a `Grade`); the `info` dict is the only audit record.
- **CSV export**: `GET /submissions/general-feedback.csv?rubric_id=&edition_id=`
  (both required: one rubric x edition = one LMS assignment), one row per
  submission with an effective general feedback: `ID number`, `Full name`,
  `Username`, `Email address`, `General feedback` -- Moodle's own field
  labels, so its gradebook CSV import maps them without renaming. UTF-8 with
  BOM (Moodle strips it). Unrostered submissions (label/unmatched) get a row
  with blank user columns. See "Exports" for where the UI offers it.
- **Grading overview**: a submission with an effective general feedback gets
  an icon next to its name in `GradingGrid.tsx` -- native `title` tooltip
  preview (a styled popover would be clipped by the grid's scroll
  container), click opens a dialog with the same `GeneralFeedbackEditor`.
- YAML: top-level `general_feedback_instructions:` key, string only (`422`
  otherwise). Feedback report: a "General feedback" box above the
  questions.

## Exports (UI naming)

Two names, used the same way in every label, tooltip and doc:

- **Feedback report** -- one student's standalone HTML page
  (`feedback_export.render_feedback_html`): general feedback plus each
  question's grade and feedback. Submission page: "Feedback report" (`GET
  /submissions/{id}/feedback.html`).
- **General feedback** -- just the submission-wide text (see "General
  feedback").

The submissions list shows both bulk exports as `ExportCard`s
(`SubmissionsPage.tsx`) **always**, greyed out with a hint naming the
missing filter, so a TA sees that they exist and what they're for:
"Feedback reports (zip)" (`GET /batches/{id}/feedback.zip`, needs a batch:
the zip reuses the batch's folder names, Moodle's feedback-files upload)
and "General feedback (CSV)" (needs one rubric and one edition -- a batch or
a fixed-edition rubric pins the edition; Moodle's gradebook import). Keep
card text to a title and one line; a new bulk export gets a card too.

## Python sandbox for grading

Per-question opt-in, not a rubric-wide setting: `Question.needs_python_sandbox`
(`grading/models.py`, default `false`). When set, `grade_answer` (`grading/grading.py`) gives the grading LLM a
sandboxed Python code-execution tool for that one question, instead of the
default single `with_structured_output` call -- useful for a question whose
rubric genuinely benefits from *running* something (checking a claimed
command's output, verifying a computation) rather than judging prose alone.
Most questions never need it, including most questions in a rubric that has
one that does.

- **Mechanism**: `grading/sandbox.py`'s `PythonSandboxTool` shells out to
  `deno run <permissions> jsr:@langchain/pyodide-sandbox@0.0.4 -c <code>` --
  Pyodide (CPython compiled to WASM) executing inside Deno's OS-level
  permission sandbox, the same underlying mechanism the `langchain-sandbox`
  PyPI package wraps. Stateless: each tool call is a fresh interpreter, no
  variables carried between calls within one grading run -- grading is one
  question at a time, so there's no session worth the complexity of
  `langchain-sandbox`'s stateful mode (which needs a LangGraph checkpointer).
- **Deliberately not the `langchain-sandbox` package itself.** It pins
  `langchain-core<0.4.0`; this project is on `langchain>=1.3.14`
  (`langchain-core>=1.6.0`, required for `create_agent` to exist at all).
  Installing it would downgrade the whole langchain/langgraph stack.
  `sandbox.py`'s module docstring has the full reasoning -- in short, the
  Python-side wrapper is thin (spawn a subprocess, parse a JSON envelope off
  stdout), so it's reimplemented directly against this project's own
  langchain-core rather than pulling in an incompatible version range. If
  `langchain-sandbox` ever relaxes that pin, revisit whether carrying this
  separately is still worth it.
- **Grading path**: `grade_answer` prepares the tools needed for the agent
  (at the moment, it just checks `question.needs_python_sandbox` and prepares
  the sandbox if needed). `_grade_with_tools` builds a `langchain.agents.create_agent`
  agent including the tools required. The result is obtained with
  `result["structured_response"]` -- `create_agent` supports tool-calling and
  structured final output together (verified against the installed
  `langchain` 1.4/`langchain-core` 1.6/`langgraph` 1.2).
- **Network permissions, verified manually against a real `deno` binary**:
  Pyodide needs `--allow-net=cdn.jsdelivr.net` even for something as trivial
  as `print(1+1)` -- it bootstraps `micropip`/`packaging` from jsdelivr on a
  cold cache every time, and lazily fetches any package the code actually
  imports (numpy, pandas, ...) the same way, auto-detected from `import`
  statements. `sandbox.py`'s `ALLOWED_NET_HOSTS` is scoped to exactly that
  host, not `allow_net=True` -- the code being sandboxed is ultimately driven
  by a student's submitted answer, so don't open it to arbitrary outbound
  requests. No other Deno permission (`--allow-run`, `--allow-ffi`,
  unrestricted `--allow-read`/`--allow-write`) is granted.
- **Deno is a system dependency, not a Python package** -- must be on `PATH`
  wherever grading actually runs (the `worker` container for real grading,
  the `api` container for the `POST /rubrics/{id}/test-answer` "try it"
  endpoint). The `Dockerfile` installs it (both containers build from the
  same image) and does a best-effort build-time cache warmup so the first
  real grading request isn't the one paying for a cold network fetch of
  Pyodide's bootstrap wheels. Running `uvicorn`/`celery` directly on the host
  instead (see "Environment variables" below) needs Deno installed locally
  too -- `PythonSandboxTool._run` raises a clear `RuntimeError` (caught by
  `_grade_submission`'s broad except, same as any other grading failure --
  the submission ends up `failed` with that message) if it isn't, rather than
  a cryptic `FileNotFoundError` from the subprocess call. Constructing the
  tool itself (`get_python_sandbox_tool`) never checks for Deno -- only
  `_run` does, lazily -- so a rubric with no `needs_python_sandbox` questions
  never pays for the check, and unit tests can construct the tool without
  Deno installed as long as they don't actually invoke it.

## Cell outputs and image inputs

Goal: grade what the student's code actually produced (printed values,
errors, figures) instead of re-running it. Two per-question opt-ins on
`Question` (`grading/models.py`), both default `false`:

- **`include_outputs`** -- `student_answer` becomes the answer with each
  cell's text outputs appended under an `--- output ---` line.
- **`include_images`** -- images found in the answer are attached to the
  grading call as multimodal input (LangChain standard `image` content
  blocks, `grading.py`'s `build_human_message`; all four providers convert
  them -- Ollama needs a vision model).

Both add a short section to the system prompt (`# Cell outputs` /
`# Figures`, the latter also when an image was expected but none found).

Flow: the extractor fills `ExtractionResult.answers_with_outputs` and
`.images` (`{answer_key: [ExtractedImage]}`, base64) -> `_rebuild_answers`
picks the text variant and records `AnsweredQuestion.image_count` ->
`_grade_answers` passes the images to `grade_answer(images=...)`. Only the
notebook extractor reports either today (rules and caps in
`notebook.py`'s "Outputs and images"); `extraction/__init__.py`'s
`OUTPUT_FORMATS`/`IMAGE_FORMATS` list which formats do, and `Rubric`'s
validator rejects a toggle on any other format (`422`). Another format
gaining figures = fill `images` in its extractor + add it to
`IMAGE_FORMATS`.

Images are never persisted (not in `Submission`, not in `GradingTrace`,
which only keeps `image_count`): `GET /submissions/{id}/answers/
{question_id}/images/{index}` re-extracts them from the stored file for the
submission page's thumbnails, and the feedback HTML export
(`feedback_export.load_answer_images`) does the same to embed them as
`data:` URIs, keeping the file self-contained. Both go through
`worker/tasks.py`'s `extract_submission_file`. Text outputs need nothing
extra there: they're already part of `student_answer`.

The export picks which answers get figures from the rubric's *current*
`include_images`, not the stored `image_count` -- that count is a
grading-time snapshot, 0 for any answer graded before the toggle was turned
on. The UI thumbnails go by `image_count`, so they only appear after a
re-grade in that situation.

## Grading traces (`GradingTrace`, `api/routers/grading_traces.py`)

Diagnostic record of every real `grade_answer()` call made by the worker,
kept for later "why did the LLM grade this the way it did" investigation --
backend only so far, no frontend consumes it yet.

- **`GradeTrace`** (`grading/models.py`, next to `Grade`/`Question` for the
  same DB-agnostic-reuse reason) is the transport shape: `provider`, `model`,
  the exact `system_prompt` sent, the verbatim `student_answer`, the
  resulting `grade`, `blank_short_circuit` (true when `grade_answer` never
  called the LLM at all -- see "Pluggable grading scales"), best-effort
  `thinking`, `tool_calls`, token usage, and `elapsed_seconds`. `grade_answer`
  builds one and hands it to an optional, keyword-only `on_trace` callback;
  `POST /rubrics/{id}/test-answer` passes none, so nothing is persisted
  there, per that endpoint's promise. `worker/tasks.py`'s `_grade_answers`
  is the only caller that passes it.
- **`thinking` is best-effort and `None` unless reasoning was explicitly
  requested.** None of this project's four providers exposes reasoning
  content unless the request asks for it -- see "Reasoning/thinking capture
  across providers" below for how `llm.py`'s `get_chat_model` turns it on,
  and what shape each provider sends back that `_extract_thinking`
  (`grading.py`) normalizes into one flat string. `None` means "this
  provider/model/call didn't expose anything recognizable" (including simply
  "reasoning wasn't requested at all," the default), not a bug.
  `_extract_tool_calls` and `_extract_usage` do the equivalent extraction for
  a `needs_python_sandbox` question's tool-call transcript and LangChain's
  standardized `usage_metadata` -- both provider-agnostic without
  per-provider handling, since `tool_calls`/`usage_metadata` are shapes every
  LangChain chat-model integration normalizes the same way, unlike reasoning
  content. All three read off `result["messages"]`, the full
  `agent.invoke(...)` result `grade_answer` keeps instead of discarding
  everything but `structured_response`.
- **`GradingTrace`** (`db/models.py`, table `grading_traces`):
  `submission_id` (FK, `ON DELETE CASCADE`), `question_id`, `trace`
  (JSONB, the whole `GradeTrace`), `created_at`. **Deliberately its own table,
  not part of `Submission.answers`** -- this data (full prompts, tool-call
  transcripts) is much larger than a `Grade` and has no reason to be
  rewritten with the submission's `answers` on every grading commit.
  `_grade_answers` adds one per question and commits it together with that
  question's grade.
- **No expiry, by design** -- traceability is the point, so nothing expires
  on its own. Cleanup is a deliberate action instead: `DELETE
  /grading-traces` bulk-deletes by `submission_id` and/or `before` (a cutoff
  datetime; a naive one is read as UTC), ANDed together when both are given,
  same as every other multi-filter endpoint in this app. It refuses to run
  with **no** filter at all unless `all=true` is passed -- a bare `DELETE
  /grading-traces` silently wiping the whole table would be an easy mistake
  otherwise. `GET /grading-traces` (optionally filtered by
  `submission_id`/`question_id`, most recent first) and `GET
  /grading-traces/{id}` exist for inspecting what's there before pruning.
  Deleting a submission deletes its traces (the cascading FK).
- **A re-grade doesn't clean up a submission's previous traces** -- old and
  new traces for the same submission/question coexist, distinguishable by
  `created_at`. Deliberate (comparing "what did the LLM see before we fixed
  the rubric" against "what does it see now" is exactly what traceability is
  for), but a repeatedly-regraded submission accumulates traces until
  `DELETE /grading-traces` is used.

## Per-answer grading info (audit tooltip)

`AnsweredQuestion.grading_info: dict | None` is a compact summary of the
LLM call behind `grade` -- provider, model (+ `model_reported` when the
provider names a different snapshot), `reasoning_effort`, features used
(sandbox/outputs/images), LLM/tool call counts, elapsed time, `tokens`
(standard input/output/total plus any `input_token_details`/
`output_token_details` sub-counts like `cache_read`/`reasoning`), `cost`
(only OpenRouter reports one today), and the final response's scalar
`response_metadata` under `provider_details` (finish reason, service tier,
Ollama durations, ...). Built by `grading.py`'s `build_grading_info`,
carried on `GradeTrace.info`, copied onto the answer by `_grade_answers`.

- **Sparse and schemaless on purpose**: a key is only present when the
  provider reported it (exception: `reasoning_effort`, always recorded from
  the grading `LlmModel` -- `null` means "provider default"); nothing
  queries or indexes it. Adding a key needs no
  frontend change -- `components/GradingInfo.tsx` renders known keys with
  labels and anything unknown under "Other".
- The full prompt/thinking/tool transcript stays in `GradingTrace`; this is
  just what fits in a tooltip (per answer, plus header totals on the
  submission page). An answer without info still shows the icon, saying
  so.
- Kept for untouched answers on a single-question regrade, alongside
  `grade` (`_rebuild_answers`).

## Manual grade overrides

A TA can correct any answer's level/feedback from the submission page or
from the grading overview's cell modal (`GradingGrid.tsx`) -- both use the
same `AnswerGradeEditor.tsx`. The grid modal keeps only ids in state and
re-reads the answer from the live `submissions` prop, so a save (which
refetches `['submissions']`) is reflected without closing it.

- **Stored next to, not instead of, the LLM's grade**:
  `AnsweredQuestion.manual_grade: Grade | None` + `manual_graded_at`
  (`db/models.py`). `grade` is only ever written by the worker.
  What the student sees is `effective_grade` (`manual_grade or grade`) -- a
  plain Python `@property`, not a `computed_field`, so it's never persisted;
  the frontend has its own `effectiveGrade()` (`api/types.ts`) for the same
  rule. Anything rendering a grade (`feedback_export.py`, `GradingGrid`,
  `SubmissionDetailPage`) must go through it, not read `.grade` directly.
- **`PUT`/`DELETE /submissions/{id}/answers/{question_id}/grade`**
  (`api/routers/submissions.py`) set/clear it. `level` must be one of the
  rubric's `grading_scale` level ids (`422` otherwise). **`409` while the
  submission is being processed**: the worker holds its own copy of
  `answers` and writes the whole list after every question, so an edit made
  mid-run would just be overwritten.
- **Overrides survive a re-grade**: `worker/tasks.py`'s `_rebuild_answers`
  carries `manual_grade`/`manual_graded_at` over by `question_id` when the
  answers list is rebuilt (dropped if the question left the rubric). The UI
  keeps showing the LLM's suggestion under an override, which is how a
  changed LLM opinion after a re-grade stays visible.

## Grading overview statistics

`GradingGrid.tsx` shows stats at three levels -- a summary panel above the
grid (`components/GradeStats.tsx`), a sortable per-student "Score" column, a
per-question footer row. All are computed client-side from the listed
submissions (no stats endpoint), always from `effectiveGrade`.

- **"Score" is the graded answers' total score over the best possible
  total** (every one of those answers at the scale's highest-scoring level),
  from the rubric's `GradingLevel.score`s (`gradeStats` in
  `lib/gradeStats.ts`). 100% = all top marks. With negative-scoring levels it
  can go below 0% (the histogram clamps that into its lowest bin). Only
  comparable within one rubric.
- Ungraded answers and levels no longer in the scale are left out of the
  score, but ungraded answers still show as a neutral segment in the
  distribution bars. Distribution bars and legends list levels in score
  order.

## Choosing the grading model (`api/routers/llm.py`)

Which model grades is runtime data, not config: provider credentials stay in
the environment, but the models themselves are registered from the UI.

- **`LlmModel`** (`llm_models`): `{provider, model}` (a unique pair; `model`
  is the provider's own identifier string) plus `reasoning_effort` and
  `is_default`. Effort is a setting of the model, not part of its identity --
  one row per model, not one per effort. It's the only field editable after
  creation (`PATCH /llm/models/{id}`, body field required since `null` is a
  real value: "provider default") -- provider/identifier changes mean
  registering a new model. **At most one default**, enforced by a partial
  unique index (`uq_llm_models_single_default`, `WHERE is_default`);
  `_make_default` unsets the previous default and flushes before setting the
  new one, in one transaction. No secrets stored -- a model whose provider
  later loses its key stays listed, flagged unavailable.
- **Favorites** are rows of `favorite_llm_models` (`user_key`,
  `llm_model_id`). There are no users yet, so every row uses
  `GLOBAL_USER_KEY` (`"global"`); a per-user version keys by user id
  instead. Deleting a model removes it from favorites (cascading FK). The
  default is deliberately *not* a per-user setting: it's deployment-wide.
- **Provider availability** (`llm.py`'s `PROVIDER_REQUIREMENTS` /
  `provider_unavailable_reason`): the provider's optional package installed
  (`importlib.util.find_spec`, no actual import) *and* its env var set --
  the API key, or `OLLAMA_BASE_URL` for Ollama (which is why
  `.env.example` leaves it blank; `get_chat_model` still falls back to
  localhost if called directly). `GET /llm/providers` reports every
  provider with a reason; the frontend only offers available ones.
- **Active vs. default**: the *active* model is a per-browser choice
  (`frontend/src/lib/activeModel.ts`, localStorage), falling back to the
  default. Every grading-triggering endpoint (`POST /submissions`, `POST
  /batches`, the three regrade endpoints, the segmentation suggestion
  endpoints, `POST /rubrics/{id}/test-answer`) takes `?model_id=` (a query
  param so multipart and JSON endpoints share one shape, `MODEL_ID_QUERY`),
  and the frontend hooks attach the active one themselves
  (`useActiveModelId`) -- callers never pass it.
- **Resolved at enqueue time**: `require_llm_model` resolves `model_id` (or
  the default) in the request and fails it there (`404` unknown id, `422`
  no default / provider unavailable), then passes the concrete id to the
  Celery task. The worker re-reads that model when the task runs
  (`resolve_llm_model`), so an effort change made in between applies; a
  model deleted in between fails that submission with a clear error. Which
  model graded each answer is in `AnsweredQuestion.grading_info`.
- `DELETE /llm/models/{id}` refuses (`409`) to delete the default while
  other models exist; the first model ever added becomes default
  automatically -- grading always has a fallback once any model exists.
- **Seeding**: `LLM_PROVIDER`/`LLM_MODEL` (optional, no built-in default)
  and `LLM_REASONING_EFFORT` only register the first default model on API
  startup when the table is empty (`seed_llm_models_from_env`; an invalid
  effort is logged and dropped). None of the three affects grading
  otherwise.
- Tests: the `client` fixture seeds a default `openai/gpt-4o-mini` model
  with a fake `OPENAI_API_KEY`, so every grading endpoint test has
  something to resolve; `client.delayed_model_ids` records what was
  enqueued.

## Pluggable LLM providers (`grading/llm.py`)

Four providers: OpenAI, Anthropic, OpenRouter (an OpenAI-compatible proxy in
front of many backends), and local Ollama. **All four are pluggable
extras** (`pyproject.toml`'s `[project.optional-dependencies]`:
`aitana[openai]`/`[ollama]`/`[anthropic]`/`[openrouter]`) -- none is a base
dependency of the `aitana` package itself, so a bare `pip install aitana`
gets no provider SDK at all. `get_chat_model`'s `_import_chat_*` helpers
import each provider's package lazily, inside the function, specifically so
importing `llm.py` (which happens transitively through most of the app --
`worker/tasks.py`, `api/routers/rubrics.py`) never requires any of the four
to be installed; picking a provider whose package isn't present raises a
clear `RuntimeError` naming the exact `pip install 'aitana[...]'` fix, not
an opaque `ImportError` surfacing from deep inside a grading task or the
`test-answer` endpoint.

- **`Dockerfile` installs `openai`, `ollama`, and `openrouter`** (matching
  what `.env.example` documents) **but deliberately not
  `anthropic`** (so Anthropic never shows up as an available provider in
  the model picker of a stock image) -- both `api`/`worker` images come from this one Dockerfile
  (see "Python sandbox for grading" above for the same pattern with Deno),
  so this is the one place that decides which providers actually work in a
  stock `docker compose up`. Wanting Anthropic in a real deployment means
  adding `--extra anthropic` to both `uv sync` lines there (or a separate
  image) -- setting `ANTHROPIC_API_KEY` alone is not enough.
- **`pydantic`'s floor is `>=2.11.2`**, low on purpose, to keep
  `aitana[openrouter]` installable: the `openrouter` SDK
  (`langchain-openrouter`'s own dependency) caps at `pydantic<2.13` in its
  newest releases. Nothing in this dependency tree needs a newer floor
  (`langchain-core` itself only needs `>=2.7.4`), and a plain `pip install
  aitana` (no extras) still resolves the newest compatible pydantic. With
  `[openrouter]`, `uv`'s resolver picks an `openrouter` release without that
  cap where one exists; the low floor is what keeps the install satisfiable
  if none does.
- **The dev dependency group installs all four extras**
  (`aitana[openai,ollama,anthropic,openrouter]`) so `tests/test_llm.py` can
  exercise every provider's actual *construction* path (right kwargs, right
  class) for real -- never a live API call (no keys in CI/dev), just
  `ChatOpenAI(...)`/`ChatAnthropic(...)`/etc. built with a fake API key and
  inspected. The "package genuinely not installed" error path is tested
  separately by forcing `sys.modules[<module>] = None`, which makes
  Python's own import machinery raise `ImportError` regardless of whether
  the package is actually present -- so that path is covered without
  needing a second venv.

### Reasoning/thinking capture across providers

None of the four providers exposes reasoning/"thinking" content unless a
request explicitly asks for it, and each does so in its own shape --
`get_chat_model`'s `reasoning_effort` parameter (`"low"`/`"medium"`/`"high"`,
or `None` -- the default, changing nothing) is what turns it on, and
`grading.py`'s `_extract_thinking` is what reads it back off afterwards.
Both were verified against each provider's actual installed client source
(`langchain-openai` 1.6.0, `langchain-anthropic` 1.7.2, `langchain-ollama`
1.1.0, `langchain-openrouter` 0.2.9) -- reading the real parsing code, not
assuming a shape -- and this write-up exists so the next person touching
either doesn't have to redo that reading:

- **openai**: Chat Completions (the default API mode) never exposes raw
  reasoning text at all -- only the Responses API does, and only when both
  `reasoning={"effort": ..., "summary": "auto"}` and
  `output_version="responses/v1"` are set. The response then carries a
  `{"type": "reasoning", "summary": [{"text": ...}, ...]}` block inside
  `message.content` (a list of blocks) -- the text is nested under
  `summary`, not directly on the block, which is where `_extract_thinking`
  looks. `temperature` is passed through unchanged regardless --
  `ChatOpenAI`'s own `validate_temperature` already drops/normalizes it for
  the specific model families (o1, non-chat gpt-5) that reject a non-default
  value once reasoning is active, so `get_chat_model` doesn't special-case it.
- **anthropic**: extended thinking is off unless
  `thinking={"type": "enabled", "budget_tokens": ...}` is set explicitly.
  `_ANTHROPIC_THINKING_BUDGET_TOKENS` maps the three-level dial onto
  conservative token budgets (there's no named-effort concept on Anthropic's
  API, only a raw budget) chosen to sit comfortably inside every current
  Claude model's default `max_tokens` (`ChatAnthropic` fills that in from
  the model's own profile when unset) -- not tuned against real grading
  traffic. The response carries a `{"type": "thinking", "thinking": "..."}`
  content block (text under the `thinking` key, not `text`), or
  `{"type": "redacted_thinking"}` with no recoverable text when Anthropic's
  safety filtering redacted a segment -- `_extract_thinking` surfaces that
  as an explicit `"[reasoning segment redacted by the provider]"` marker
  rather than silently dropping it, so a trace reader can tell "reasoning
  happened but is hidden" apart from "no reasoning happened at all".
  Anthropic's API also rejects any `temperature` other than `1` once
  thinking is enabled -- unlike OpenAI, `ChatAnthropic` only self-guards
  that for a couple of specific model families, not universally, so
  `get_chat_model` overrides `temperature` to `1` itself whenever
  `reasoning_effort` is set, rather than letting a live 400 be the first
  sign of the conflict. (Separately: `ChatAnthropic.with_structured_output`
  already handles the thinking-enabled + forced-tool-call tension itself --
  it warns and falls back to unforced tool calling rather than raising, so
  `grade_answer`'s `create_agent(..., response_format=schema)` needs no
  special handling here; verified by actually building the agent graph with
  a thinking-enabled model and a response_format schema and confirming it
  constructs cleanly.)
- **ollama**: a local reasoning-capable model (deepseek-r1, qwq, ...) either
  leaks `<think>`/`</think>` tags into the main response content, or, if
  `reasoning=` is set on `ChatOllama`, returns the reasoning separately in
  `additional_kwargs["reasoning_content"]` (a plain string) and keeps
  `content` clean. Unlike the other three, Ollama also accepts
  `reasoning=True` (no graded effort) for a model with no notion of effort
  levels -- `get_chat_model` just forwards whichever of this project's three
  named levels was asked for as-is; `ChatOllama` accepts any string.
- **openrouter**: reasoning is off unless `reasoning={"effort": ...}` is
  set -- OpenRouter's own unified convention, the same shape regardless of
  which backend model it's actually proxying to. `langchain-openrouter`
  copies OpenRouter's `reasoning`/`reasoning_details` response fields into
  `additional_kwargs["reasoning_content"]`/`["reasoning_details"]` --
  deliberately *not* replicated by hand against a plain `ChatOpenAI` (which
  would silently drop them: its own docstring says non-standard
  third-party response fields like these "are not extracted or preserved"),
  which is the whole reason `openrouter` is its own provider/extra rather
  than "point `ChatOpenAI` at OpenRouter's base_url".

`_extract_thinking` normalizes all of the above into one flat string (or
`None`) regardless of provider -- `GradeTrace.thinking` is meant to be
provider-agnostic; a reader of a `GradingTrace` row shouldn't need to
know which of the four produced it to make sense of the `thinking` field.
`tool_calls`/token usage need no equivalent per-provider handling: LangChain
already normalizes `AIMessage.tool_calls`/`.usage_metadata` to the same
shape across every provider's integration.

## Creating rubrics (`api/routers/rubrics.py`)

Two creation paths; a duplicate `slug` is `409` on both:

- **`POST /rubrics`** -- JSON body (`RubricIn`: `title`, optional `slug`,
  `course_id` **required**, optional `edition_id`, optional
  `format`/`grading_scale` (default `pdf_form`/the 4-level scale, see
  "Pluggable submission formats"/"Pluggable grading scales"), optional
  `context`, `questions: list[Question]`). This is the frontend's "Build
  manually" form (`frontend/src/components/RubricForm.tsx`).
- **`POST /rubrics/upload`** -- multipart (`yaml_file`, optional `slug`/
  `course_id`/`edition_id`/`format` form fields). Parses the rubric YAML
  shape (`grading_scale` and `context` are YAML keys only). This is the
  frontend's "Upload YAML" form (same component).

Both end in the same validation: the YAML path builds a `RubricIn` from the
file and form fields (`_parse_rubric_yaml`), so every rule -- question shape,
grading-scale rules, `include_outputs`/`include_images` against the format --
is checked once, in one place, and a bad file is a `422`, not a `500`
(malformed YAML or a missing top-level `questions` key included).

Slug resolution order (`upload_rubric_yaml`): the `slug` form field, then a
top-level `slug:` key in the YAML body, then the uploaded filename with
`_questions` stripped and slugified (`slugify()`, a simple
lowercase-and-underscore regex, `src/aitana/slugify.py`). `create_rubric`
(the JSON path) uses `slug` if given, else slugifies `title`.

Course resolution order (`upload_rubric_yaml`): the `course_id` form field,
then a top-level `course_slug:` key in the YAML body; if neither resolves,
`422` (course is required, unlike edition). Edition resolution is the same
shape via `edition_id`/`edition_slug:`, but optional -- no match because
neither was given is fine, a given-but-unknown value is `404`.
`_require_yaml_string` guards `course_slug`, `edition_slug` and the
top-level `slug` against a real YAML footgun: an unquoted scalar like
`2026_27` parses under PyYAML's default (YAML 1.1) resolver as the
*integer* `202627`, not the string `"2026_27"` -- underscores inside a
number are digit separators, same as Python's `1_000_000` literal. That's
exactly the shape `slugify("2026/27")` produces for an edition. Without the
guard this fails at the edition lookup with a baffling
`Edition 202627 not found` 404; with it, a `422` names the field and tells
you to quote it. The JSON path takes `course_id`/`edition_id` directly and
has no such ambiguity.

## Listing/filtering rubrics (`api/routers/rubrics.py`)

`GET /rubrics` takes optional `course_id`/`edition_id` query params, ANDed
together when both are given -- plain column filters on `rubrics.course_id`
/ `rubrics.edition_id`. The frontend's `RubricsPage.tsx` uses this to filter
by course/edition, and it's how `CoursesPage.tsx`'s and `EditionsPage.tsx`'s
"See rubrics" links work.

## Editing rubrics (`api/routers/rubrics.py`)

A separate `PUT`, not an upsert on the creation endpoints: `POST /rubrics`
and `POST /rubrics/upload` stay creation-only (a duplicate `slug` is still a
plain `409` there, unconditionally). Two edit paths, mirroring the two
creation paths and the frontend's two `RubricForm.tsx` modes:

- **`PUT /rubrics/{rubric_id}`** -- JSON body, same `RubricIn` shape as
  creation. The "Build manually" edit path.
- **`PUT /rubrics/{rubric_id}/upload`** -- multipart, same fields as `POST
  /rubrics/upload`. The "upload updated version" edit path: the YAML file's
  contents *replace* the rubric's `title`/`format`/`grading_scale`/
  `context`/`questions` wholesale. Unlike creation:
  - the slug (absent a form field or YAML `slug:` key) defaults to the
    rubric's *current* slug, not one derived from the uploaded filename --
    the point of this flow is updating an existing rubric's content, not
    accidentally renaming it because the new file is named differently.
  - `course_slug:`/`edition_slug:` keys in the file are ignored, and the
    course/edition stay as they are unless the `course_id`/`edition_id` form
    fields say otherwise -- re-uploading a file must never move a rubric to
    another course or edition as a side effect.

Both share `_update_rubric` (mirrors `_insert_rubric`): validate through
`RubricIn`, check the slug and the format change, assign, bump
`updated_at`, commit. Two things an edit does differently from creation:

- **Slug conflicts only 409 against a *different* rubric**
  (`_check_slug_available(slug, exclude_id=rubric.id)`) -- saving a rubric
  back with its own unchanged slug isn't a conflict with itself.
- **Format changes are guarded** (`_guard_format_change`): `409` if the
  rubric already has any submission and the edit changes `format` (see
  "Pluggable submission formats" for why). A rubric with zero submissions
  (still being drafted) can change format freely.

Nothing about `slug`/`course_id`/`edition_id` is protected from being
changed on edit *at the API level* -- `PUT /rubrics/{id}` takes all three,
same shape as creation. The *frontend* (`RubricForm.tsx`) locks all three to
the rubric's current values during edit -- greyed-out `<select>`s for
course/edition, a disabled slug input pre-filled with the current slug --
deliberately: moving a rubric to another course/edition mid-life is a
bigger structural move than "edit this rubric's content," and changing
`slug` changes which rubric a future re-upload of the same YAML file is
expected to match. A script or API caller that genuinely needs to move or
rename a rubric can still do it through `PUT /rubrics/{id}`.

## Deleting rubrics

`DELETE /rubrics/{rubric_id}` (`delete_rubric`) -- `204` on success, `404`
if the rubric doesn't exist. Blocked with `409` if any submission or batch
references it: their history would be left without the rubric every
submission/batch view renders from (the `RESTRICT` foreign keys would refuse
anyway; the check exists to say why). A rubric nobody has submitted
anything against yet (still being drafted, or created and abandoned)
deletes freely.

`GET /rubrics/submission-counts` returns `{rubric_id: count}` (one `GROUP BY
rubric_id`), shown on the rubric list/detail pages as a link to
`/?rubric_id=...`.

The frontend (`RubricDetailPage.tsx`) exposes this as a "Delete" button next
to "Edit", behind a native `confirm()` -- there's no undo, and a `409` here
means "go deal with the submissions/batches first," not "retry."

### Interactively testing a rubric (`POST /rubrics/{id}/test-answer`)

A "try it before you upload a real exam" endpoint: `{question_id, answer}`
in, a `Grade` (level + feedback) out, synchronously. No submission is
created, nothing touches MinIO or Celery, and nothing is persisted at all --
it just runs `grade_answer(chat_model, question, answer, rubric.grading_scale)`
against one of the rubric's existing `questions` and returns the result
directly. This is what backs the frontend's per-question "Test this
question" panel (`RubricDetailPage.tsx`'s `QuestionTestPanel`) -- a TA can
type a sample answer and see exactly how the LLM would grade it, using the
rubric's real prompt and grading scale, without a PDF/notebook, a student
record, or a background job.

Two things worth knowing if you touch this:
- **It calls the LLM directly inside the request handler**, unlike
  `worker/tasks.py`'s Celery-isolated grading -- `grade_answer`'s
  `.invoke()` is a blocking call, and blocking the shared FastAPI event loop
  for the duration of one LLM round-trip would stall every other concurrent
  request. `run_in_threadpool` (from `fastapi.concurrency`) hops it off the
  event loop without needing a full task-queue+poll flow for what's a single
  quick "try it" call, not the batch pipeline.
- **Errors are caught broadly and turned into `502`s**, not left to bubble
  into a raw `500` -- a missing/bad LLM provider config (see `llm.py`) is a
  very plausible thing to hit while iterating on a rubric before its real
  credentials are wired up, and the frontend shows whatever message comes
  back.

## Deleting students, submissions and batches

All behind a native `confirm()` naming what's deleted, same as rubrics.

- **`DELETE /students/{id}`**: `409` if any submission is linked to the
  student (counted first, for the message; the FK would refuse anyway).
- **`DELETE /submissions/{id}`** removes the row (its grading traces go
  with it, cascading FK) and then its MinIO objects (stored file plus the
  generic-document derived folder, `storage.delete_submission_files`). The
  database commit goes first and storage after, best-effort (logged): a
  failure there only orphans objects, never leaves a submission pointing at
  a missing file. **`409` while in progress unless `?force=true`**: the
  worker would otherwise keep working on a submission whose file is gone.
  `force` exists for lost jobs; a worker still running on a force-deleted
  submission notices on its next commit and stops (see "Database layer
  rules"). The UI (`DeleteSubmissionButton.tsx`) sends `force` when the
  user confirms deleting an in-progress submission.
- **`DELETE /batches/{id}`** deletes the batch *and* all its submissions
  (same helpers, `delete_submissions`/`guard_not_in_progress`),
  all-or-nothing on the in-progress check. The UI tries without `force` and,
  on `409`, asks again before retrying with it (`ApiError.status` in
  `hooks.ts`).

## Batch student import (`api/routers/students.py`)

`POST /students/import` -- multipart (`file`, `format` form field). Bulk
creates/updates roster entries from a roster file, alongside the existing
one-at-a-time `POST /students`.

- **`StudentImportFormat`** is the enum of supported roster shapes --
  `atenea` (UPC's Moodle instance's roster export, CSV) and `learnsql`
  (LearnSQL's student list, `.ods` or CSV). Same
  registered-parser-function shape as `SubmissionFormat`/`_EXTRACTORS`
  (`grading/extraction/__init__.py`, see "Pluggable submission formats"
  above): `_IMPORT_PARSERS: dict[StudentImportFormat, Callable[[bytes],
  list[_ImportedStudentRow]]]`. Add a new format by writing one
  `(bytes) -> list[_ImportedStudentRow]` function and registering it there --
  nothing else in the endpoint changes. A format that is just "these column
  names" is a `_RosterColumns` plus a one-line parser over the shared
  `_students_from_table` (as both existing ones are).
- **Atenea** (`_ATENEA_COLUMNS`): `ID number` and `First name` required
  (matched case-insensitively against the header row, see below for why);
  `Last name`/`Username`/`Email address` optional; anything else (`Group`)
  ignored. `csv.Sniffer` picks the delimiter off the header line (comma,
  semicolon or tab), falling back to comma if sniffing is inconclusive.
- **LearnSQL** (`_LEARNSQL_COLUMNS`): header `Nom, Cognoms, Número ID,
  Institució, Departament, Adreça electrònica, Suspès, Darrera descàrrega des
  d'aquest curs`. Only `Número ID` (-> `student_id`), `Nom` + `Cognoms` (->
  `name`) and `Adreça electrònica` (-> `email`) are read; no username.
  Headers are NFC-normalized before matching, for the accents.
  **LearnSQL exports `.ods`, read with the standard library only**
  (`_read_ods`: an `.ods` is a zip holding `content.xml`), not odfpy/pandas
  -- a roster is a flat list of text cells. It takes the first sheet,
  expands `number-columns-repeated`/`number-rows-repeated` (how ODF stores
  runs of identical cells, including thousands of blank ones after the
  data; trailing blanks are not expanded), and reads a numeric cell from
  `office:value` rather than its displayed text (which may have thousands
  separators). ODS vs. CSV is sniffed from the content (zip magic), not the
  filename. If an ODS ever needs more than this (several sheets, formulas,
  dates), that's the point to reconsider a library.
- **The student id and first-name columns are the only required ones**
  (`ID number`/`First name` for Atenea, `Número ID`/`Nom` for LearnSQL), for two
  different reasons, both enforced the same way (missing header column ->
  `422` before any row is even read; missing/blank value in a data row ->
  `422` that aborts the **whole** import, nothing written, not even rows
  before it):
  - `ID number` is the sole source of `Student.student_id` -- the unique
    roster key every other endpoint (`Submission.student`, ...) already
    joins against. It can't be
    relaxed to some other column as a fallback without a second, later
    import silently minting a duplicate `Student` under a different key for
    someone whose `ID number` merely wasn't in this particular export.
  - `First name` exists so every imported student is identifiable by a real
    name -- `Student.name` is required, and this project deliberately does
    *not* fall back to `Username`/`ID number` as a stand-in name for a row
    that omits it.
  - Aborting the whole file rather than skipping just the bad row is the
    same reasoning in both cases: silently dropping one student from an
    otherwise-successful import is easy to miss, a hard failure that names
    the row is not. Two rows sharing the same `ID number` within one file
    are rejected the same way, before anything is written, since neither
    one is inherently more current-looking than the other.
- **`name`** is `First name` + `Last name` joined with a space when `Last
  name` is present, else just `First name` (the task that requested this
  format specifically wanted `First name` to work alone when `Last name` is
  blank). LearnSQL: `Nom` + `Cognoms`, same rule.
- **`username: str | None`** (`db/models.py`) is purely
  informational metadata -- not a lookup key (`student_id` fills that
  role). A `Group` column in the CSV, if present, is silently ignored --
  there is no `Student.group` field to put it in.
- **Existing-student handling is upsert-by-`student_id`, and it's a full
  overwrite, not a merge**: one `INSERT ... ON CONFLICT (student_id) DO
  UPDATE` sets `name`/`username`/`email` to whatever the row says, including
  clearing a field the row leaves blank. A re-export is expected to be the
  current source of truth for the roster, not something to be reconciled
  field-by-field against what's already there.
- Response (`StudentImportResult`: `created`, `updated`, `students`) lets
  the frontend show a plain "N created, M updated" summary
  (`StudentsPage.tsx`).

## Batch submission import (`api/routers/batches.py`)

`POST /batches` -- multipart (`file` zip, `rubric_id`, optional
`edition_id`, `type` form field). Creates one `Batch` plus one `Submission`
per subfolder in the zip, each enqueued for grading immediately, same as
`POST /submissions`.

- A batch always picks a **rubric**, not a bare course -- a batch upload is
  always a single deliverable's worth of submissions (one exam sitting, one
  lab's worth of hand-ins), same as a single `POST /submissions` call. The
  course is implied by `rubric.course`, same as everywhere else in this
  data model. `edition_id` is resolved via the exact same
  `resolve_edition` helper `create_submission` uses (the rubric's own
  edition if it has one, else the uploader must supply one) -- imported
  from `submissions.py` rather than duplicated.
- **`BatchType`** is the enum of supported zip layouts -- currently just
  `moodle` (Moodle's per-assignment "download all
  submissions" export). Same registered-parser-function shape as
  `StudentImportFormat`/`_IMPORT_PARSERS` above:
  `_BATCH_PARSERS: dict[BatchType, Callable[[zipfile.ZipFile],
  list[_BatchItem]]]`. Add a new layout by writing one
  `(zipfile.ZipFile) -> list[_BatchItem]` function and registering it there.
- **`_parse_moodle_zip`** expects one top-level folder per student
  submission, each folder containing exactly one file. A Moodle export
  names each folder `<surname(s)> <name>_<internal_id>_assignsubmission_
  file` -- this is stored **verbatim**, not parsed into its name/
  internal-id parts, as `Submission.batch_internal_id`. A later, separate
  feedback-export feature reads that id back off the submission to write
  grades into the originating Moodle assignment against the same item --
  this is the reason the id is kept as-is rather than normalized. A folder
  is read off its own directory entry in the zip (not merely inferred from
  file paths), so a folder with zero files (e.g. a student who never
  submitted) is still reported as an offending folder, same as one holding
  more than one file -- everything is validated before anything is
  written, same fail-fast-before-writing shape as `import_students`.
  `__MACOSX/` entries and dotfiles are skipped as export junk; any entry
  path containing a `..` segment is rejected outright (zip-slip).
- Each item's filename extension is checked against the rubric's expected
  one (`FORMAT_FILE_INFO[rubric.format]`), the same early check
  `create_submission` does for a single file, catching an obviously wrong
  zip before grading starts on every item. All rows are flushed (for their
  ids, which the storage keys contain), every file is stored, and only then
  is everything committed and enqueued -- a storage failure leaves no
  half-created batch behind.
- Created submissions have **no student** unless `_match_student` finds
  one (below). Grading runs anyway (`worker/tasks.py` never touches
  `submission.student`); only per-student attribution is deferred.
- **`_match_student`** makes a best-effort auto-match right at upload time,
  so most items don't stay unmatched: it strips Moodle's fixed `_<internal_
  id>_assignsubmission_file` suffix off the folder name, tokenizes what's
  left into lowercase, accent-folded words (`_name_tokens`), and compares
  that *set* against each roster student's `Student.name` tokenized the
  same way. Set comparison, not string comparison, because Moodle's folder
  names are "surname(s) first name" while `Student.name` (see "Batch
  student import" above) is "first name surname(s)" -- token order differs,
  token *content* doesn't. Only commits when **exactly one** student's
  tokens match; zero or more-than-one (e.g. two students sharing a name)
  is left `None` rather than risk a wrong guess. The whole roster is loaded
  once and matched in memory against every item, not queried per item.
- Batch items are stored under `storage.batch_object_key(rubric_slug,
  submission_id, extension)` (`f"{rubric_slug}/_batch/{submission_id}
  {extension}"`) rather than `storage.object_key`, since there's no student
  id to namespace by yet.
- Response (`BatchUploadResult`: `batch`, `created`, `submissions`) mirrors
  `StudentImportResult`'s shape.
- Whatever `_match_student` couldn't resolve (or got wrong) is fixed up
  through `PUT /submissions/{submission_id}/student` (`api/routers/
  submissions.py`, body `{student_id}` -- the student's database id) --
  works on any submission, not just a batch-created one.
- `POST /batches/{batch_id}/regrade` is the bulk version of `POST
  /submissions/{id}/regrade` -- same per-submission reset (`status` back to
  `pending`, `error` cleared, committed, then re-enqueued), applied to every
  submission of the batch. Response is `{regraded: <count>}`.
  Optional `?question_id=` re-grades only that question (`422` if not in the
  rubric): the worker still re-extracts, but `_rebuild_answers`
  (`keep_grades_except`) keeps every other answer's previous LLM grade and
  `_grade_answers` (`only_question_id`) skips them -- no LLM call, no trace.
  For re-running one question after a rubric tweak without paying for the
  rest. `POST /submissions/regrade` (`{submission_ids, question_id?}`) is
  the same over an explicit id list; the frontend uses it for its regrade
  bar, shown whenever the submissions list is filtered to a single rubric
  (batch or rubric filter), regrading exactly the listed submissions.
  `AnswerGradeEditor.tsx` uses it too, for a per-answer "Regrade question"
  button (one submission id + that question), so it shows on the submission
  page and in the grading overview's cell modal alike. `useSubmissions`
  polls while any listed submission is in progress, which is what makes
  that regrade visible in the grid without a reload.

## Courses, editions and filtering submissions

Courses and editions are creation-only, same `409`-on-duplicate-slug shape
as rubrics. `POST /courses` takes just `name` (+ optional `slug`). `POST
/editions` takes only `name` (+ optional `slug`, defaulting to
`slugify(name)`) -- **no `course_id`**, since editions are global (see "Data
model" above). `slugify("2026/27")` is `2026_27`, a YAML footgun -- see
`_require_yaml_string` in "Creating rubrics".

`GET /submissions` filters (`student_id`, `rubric_id`, `course_id`,
`edition_id`, `batch_id`, `status`) all AND together. **Filtering by course
vs. by edition are genuinely different relationships -- don't conflate
them:**

- `course_id` filters via the **rubric's** course (a join on `rubrics`).
  "Show me all BDM submissions" means "submissions whose rubric belongs to
  BDM," regardless of who submitted them or which edition it was submitted
  under.
- `edition_id` filters **directly** on the submission's own `edition_id` --
  every submission carries its own definitive edition (see "Creating a
  submission" for how that's decided).

Passing both ANDs them, which only returns results where the rubric's
course and the submission's edition line up -- the normal case, but not one
the schema enforces: nothing stops a BDM rubric from being submitted under
an edition no other BDM rubric uses, if the uploader picks it explicitly
(editions don't belong to a course).

## Labelled submissions

For group projects, where attributing a submission to roster students is
more work than it's worth: `Submission.label: str | None` is a free-form,
display-only identifier ("Group 3"), used *instead of* `student` (not
unique, never a lookup key).

Deliberately *not* called "anonymous": the professor knows exactly who
handed it in (it just isn't linked to the roster), and "anonymous" is the
natural name for a possible future blind-grading feature. It's also
distinct from "Unmatched" (a batch item not linked to a student *yet*).

- **Single upload** (`POST /submissions`): `student_id` is now optional,
  `label` is new; exactly one of them is required (`422` for neither or
  both; a blank label counts as none). Labelled files are stored under
  `storage.labelled_object_key` (`<rubric>/_labelled/<id><ext>`) -- the
  label isn't a safe path segment.
- **Batch upload** (`POST /batches`): `match_students` form field (default
  `true`). `false` skips `_match_student` entirely and sets `label` to the
  folder name minus Moodle's suffix (`_folder_label`; for a Moodle group
  assignment that's the group name). `batch_internal_id` is still stored
  verbatim, so the feedback zip works unchanged.
- **Display**: `Submission.display_name`/`.file_identifier` (backend, used
  by the feedback export and download filenames) and `submissionName()`
  (`frontend/src/api/types.ts`) share one fallback chain: student ->
  label -> batch folder name -> id. Use them rather than reading
  `.student` directly. The UI hides the "match student" button on a
  labelled submission (it was deliberately left unmatched), though `PUT
  /submissions/{id}/student` still works on it -- `label` is then simply
  shadowed by the student.
- **Renaming**: `PUT /submissions/{id}/label` (`{label}`, stripped; blank
  is `422` -- this renames, it never clears), behind a pencil button
  (`RenameLabelDialog.tsx`) on labelled submissions in the list and detail
  pages, in place of the match-student one.

## Creating a submission (`api/routers/submissions.py`)

`POST /submissions` needs a definite edition, but not every rubric fixes
one (see "Data model" above). `resolve_edition` decides it:

- If the rubric has a fixed edition, that edition is used, full stop -- any
  `edition_id` the client sent is ignored.
- If the rubric has no fixed edition, the client's `edition_id` is required
  -- `422` if missing, `404` if it doesn't resolve.

The frontend mirrors this exactly (`UploadSubmissionPage.tsx`): picking a
rubric with a fixed edition shows it read-only; picking one without shows
an editable, required dropdown listing every edition (global, not scoped to
the rubric's course -- see "Data model" above).

`POST /submissions` does the fast part synchronously (validate
student/rubric, resolve the edition, flush the new row to get its id, upload
the file to MinIO under a key containing that id, commit with
`status=pending`) and only then enqueues `grade_submission.delay(id)` --
after the commit, so the worker never looks for a row that isn't visible
yet. A storage failure before the commit leaves no row behind. The Celery
task does the slow part:

```
pending -> extracting (raw file -> {answer_key: text}, via whichever
                        extractor rubric.format selects)
        -> grading (answers populated with student_answer, grade=None;
                     committed BEFORE the LLM client is constructed, so a
                     missing API key / bad provider config still leaves the
                     extracted answers visible, not just an opaque failure)
        -> graded (each answer's grade filled in one LLM call at a time via
                    `_grade_answers`, committed after every single question
                    so polling clients see partial progress)
   or -> failed (error message captured on the row)
```

Generic documents (`generic_document`) have extra states before `pending` ->
`extracting`: see "Generic-document submissions" above.

If you change this flow, preserve the "commit extracted answers before the
LLM step" ordering -- otherwise a provider misconfiguration hides the
extraction result too.

Celery tasks are sync and the database layer is async, so each task runs
`asyncio.run(...)` around its body with its own short-lived, unpooled engine
(`db.session.task_session`, `NullPool`): asyncpg connections belong to the
event loop that opened them, and each `asyncio.run()` gets a new loop. One
connection per task, no pooling across tasks -- fine for this app's volume;
if grading throughput ever becomes a bottleneck, look here first (e.g. a
long-lived per-worker-process event loop and engine via `celery_app.py`'s
`worker_process_init`/`worker_process_shutdown` signals).

## Downloading the original file

`GET /submissions/{id}/file` (`api/routers/submissions.py`) reads the object
from MinIO server-side (`storage.download_bytes`) and returns it as a
`Response` with `Content-Disposition: attachment` (`Content-Type` and the
filename extension taken from `FORMAT_FILE_INFO[submission.rubric.format]`,
see "Pluggable submission formats" above), rather than redirecting
the browser to a MinIO presigned URL. This is deliberate, not an
oversight: `MINIO_ENDPOINT` (`minio:9000` in `.env.example`) is the
Docker-internal hostname `api`/`worker` resolve via Docker's network DNS --
a presigned URL built from it would point the *browser* at a hostname it
has no way to resolve, since the browser runs on the host, outside that
network. Proxying bytes through the API (which the browser can already
reach, same as every other endpoint) sidesteps the whole
internal-vs-external-hostname problem. If this ever needs to scale to
large files or high download volume, revisit with a properly
externally-reachable MinIO/S3 endpoint and use presigned URLs or
similar feature to lighten the load.

## Database layer rules

The handful of things about SQLAlchemy + asyncpg + JSONB that will bite if
ignored. Each one has a test that fails when it's broken.

**1. In-place changes to a JSONB value are invisible to the ORM -- call
`flag_modified`.** `PydanticJSONB` columns (`Submission.answers`,
`.document`, `.segments`, `Rubric.questions`, `.grading_scale`,
`GradingTrace.trace`) hold Pydantic objects. Assigning a new value to the
attribute (`submission.answers = [...]`) is always written. Mutating what's
already there (`submission.answers[i].grade = grade`) is *not*: the ORM
never sees it and the commit silently writes nothing. After an in-place
change, call `sqlalchemy.orm.attributes.flag_modified(obj, "answers")`
before committing -- as `_grade_answers` (worker) and the manual-grade
endpoints do. `tests/test_worker_tasks.py`'s
`test_grade_answers_persists_every_question` fails if the worker's call is
removed.

**2. Nothing is loaded lazily -- say what you need.** Every relationship is
`lazy="raise"`: touching `submission.rubric` without having loaded it raises
instead of quietly issuing a query (which async SQLAlchemy can't do
implicitly anyway -- you'd get a `MissingGreenlet` error from deep inside
serialization). Load relationships explicitly with
`options=(selectinload(...),)`; the `RUBRIC_LOADS`/`BATCH_LOADS`/
`SUBMISSION_LOADS` tuples in `db/models.py` are the shapes the response
schemas need. A new endpoint returning a `*Out` model must load what that
model nests, or the response serialization raises.

**3. New objects get their defaults at construction.** `Base.__init__`
(`db/models.py`) fills every column the caller didn't pass with its Python
default (or `None`) immediately, not at INSERT time. Otherwise a column left
unset would be loaded lazily on first access after the flush -- point 2
again. Keep column defaults Python-side (`default=...`), not
`server_default`, unless you also refresh the object after inserting.

**4. Sessions don't expire on commit.** `expire_on_commit=False`
(`db/session.py`): endpoints commit and then serialize the objects they just
wrote, and the worker keeps using the same objects across its per-question
commits. A *rollback* still expires everything in the session -- after one,
don't read attributes of objects from before it (the worker's `_mark` writes
a failure with a fresh `UPDATE` for this reason).

**5. A row deleted under the worker.** The worker commits after every step;
if the submission was force-deleted meanwhile, that commit's `UPDATE`
matches no row and SQLAlchemy raises `StaleDataError`. `_commit`
(`worker/tasks.py`) turns that into `SubmissionGone`, which the task entry
points log and stop on -- nothing is recreated or marked failed.

**6. Commit before enqueueing.** An endpoint that resets a submission and
enqueues a task (`enqueue_regrades`, `enqueue_segment_suggestions`,
`create_submission`) commits first: a task enqueued before the commit could
run against the old row.

## Frontend/backend contract

- `frontend/src/api/schema.ts` is generated from the backend's own OpenAPI
  schema via `openapi-typescript` -- see `frontend/README.md` for the exact
  command. It is **not** regenerated automatically; if you add/change an API
  route or a request/response schema (`api/schemas.py`, or a router's own
  models), regenerate it or the frontend's types will silently drift from
  reality.
- `openapi-typescript` is deliberately **not** a `package.json`
  dependency -- it's invoked via `npx openapi-typescript@latest` at codegen
  time only. It has a peer dependency on TypeScript ^5.x that conflicts with
  this project's TypeScript ^6.x; since it's never imported at runtime or
  build time, keeping it out of `package.json` sidesteps that conflict
  entirely rather than forcing `--legacy-peer-deps`.
- `frontend/src/api/types.ts` names the generated schemas the app uses
  (`Rubric` = `RubricOut`, `Submission` = `SubmissionOut`, ...) -- the
  generated types describe the real responses exactly, nested objects
  included, so there are no hand-written overrides of their shape. Since
  every nested object is typed as what the endpoint actually loads,
  reaching for something it doesn't load (e.g. `submission.rubric.course`)
  is a compile error, not a silent runtime `undefined`.
- **Ids are numbers.** URL params, search params and `<select>` values carry
  them as strings; convert at that boundary with `parseId`
  (`frontend/src/lib/ids.ts`) or `Number(...)`, never by comparing a string
  to an id.
- `types.ts`'s `IN_PROGRESS_STATUSES` is a hand-maintained subset of the
  backend's `SubmissionStatus` (`db/models.py`; it includes
  `converting`/`segmenting`, but *not* `awaiting_segmentation`, which waits
  on a human, not a worker), used to decide whether `useSubmission` keeps
  polling. If you add an in-progress status server-side, update this too --
  and `_IN_PROGRESS_STATUSES` in `api/routers/submissions.py`. Same for
  `SEGMENTED_FORMATS`/`IMAGE_FORMATS`/`OUTPUT_FORMATS`, mirrored by hand in
  `types.ts`, and the grading-scale helpers in `lib/gradingScale.ts`
  (mirroring `grading/models.py`'s).

## Grading internals (`src/aitana/grading/`)

See file docstrings for the mechanics:
- system prompt = question + context + rubric + expected points + grading scale
- human message = verbatim student answer

See "Pluggable grading scales" above for why that schema is
built per-call rather than a fixed `Grade` type).

`build_system_prompt` (`grading.py`) composes, in order: an optional
`# Assignment context` block (the rubric-wide `Rubric.context`, passed in as
the keyword-only `rubric_context` -- `Question` stays rubric-agnostic, so
`grade_answer`/`_grade_answers`/`test-answer` thread it through explicitly;
omitted when unset or blank), `# Question`
(`question.question`, the task as posed -- *not* `title`, which is UI-only
and never sent to the LLM), an optional `# Context` block (only rendered
when `question.context` is set -- omitted entirely otherwise, not left as
an empty heading), `# Grading instructions` (`rubric`), an optional
`# Model answer` block (only rendered when `question.model_answer` is set
-- a worked/canonical answer for questions that have one; most don't, and
`rubric`/`expected_points` remain the primary grading criteria either way),
`# Points a good answer should cover` (`expected_points`), and `# Grading`
(the rubric's `grading_scale`, rendered by `build_grading_section` -- see
"Pluggable grading scales" above). If you add another optional per-question
field that should reach the prompt, follow the same pattern: build the
block conditionally in Python and interpolate it as a single
already-formatted chunk, don't try to make the template itself branch.

## Environment variables

`.env.example` (backend) and `frontend/.env.example` list what's read.
`pydantic-settings` (`settings.py`) is case-insensitive, so `.env`
conventionally uses `UPPER_SNAKE_CASE` while `Settings` fields are
`lower_snake_case`. `DATABASE_URL` accepts any `postgres://`/`postgresql://`
URI and rewrites it to the asyncpg driver (`postgresql+asyncpg://`), so an
operator-generated URI (e.g. a CloudNativePG secret's `uri`) works verbatim.

The **hostnames differ between Docker and local runs**: `.env.example`'s
defaults (`postgres`, `minio`, `redis`, `gotenberg`) are the docker-compose
service names, correct for the `api`/`worker` containers but wrong if you
run `uvicorn`/`celery` directly on the host. The compose file doesn't
publish those services' ports, so for a host run add a `ports:` mapping
locally (or use a `docker-compose.override.yml`) and point the URLs at
`localhost`, e.g.:

```bash
export DATABASE_URL=postgresql://aitana:aitana@localhost:5432/aitana
export MINIO_ENDPOINT=localhost:9000
export REDIS_URL=redis://localhost:6379/0
export GOTENBERG_URL=http://localhost:3000   # or blank: MuPDF fallback
uv run alembic upgrade head
uv run uvicorn aitana.api.main:app --reload --port 8080
```

Running `uvicorn`/`celery` directly on the host also means you're
responsible for whichever LLM provider extras `uv sync` installed --
`uv sync` (no `--extra` flags) alone gets none of the four (see "Pluggable
LLM providers" above); match whatever `Dockerfile` installs
(`--extra openai --extra ollama --extra openrouter`) or add `--extra
anthropic` yourself if you want Anthropic models available locally.

**If you ever test `POST /submissions` (or anything else that calls a
Celery task's `.delay()`) against an isolated setup, that setup needs an
isolated Redis too**, not just PostgreSQL/MinIO -- `.delay()` connects to
`settings.redis_url`, which defaults to `redis://localhost:6379/0`. Without
an isolated Redis, the request either hangs (nothing listening) or, worse,
silently reaches whatever real Redis happens to be on that port (see
"Verifying manually" below). `docker run -d --rm --name aitana-scratch-redis
-p <port>:6379 redis:7-alpine` plus `REDIS_URL=redis://localhost:<port>/0`
covers it.

## Code style (Ruff, Prettier)

Both sides are auto-formatted; don't hand-format, and don't fight the
formatter's output. Line length is 120 on both.

- **Python: Ruff** (dev dependency; config in `pyproject.toml`'s
  `[tool.ruff]`). `uv run ruff format .` formats (Black-compatible),
  `uv run ruff check --fix .` lints (pycodestyle, pyflakes, isort, pyupgrade,
  bugbear). Scoped to `.py` files only (`include`), since the repo root may
  hold notebooks/Markdown course material that isn't project code. Two
  deliberate exceptions: FastAPI's `Depends`/`File`/`Form`/`Query` are
  allowed in argument defaults (B008), and `UP042` (`(str, Enum)` ->
  `StrEnum`) is off -- that rewrite changes every member's `str()`/f-string
  output, and these enums are persisted as strings.
- **Migrations**: `alembic.ini`'s `post_write_hooks` runs Ruff on every new
  revision file, so autogenerated revisions come out formatted. Formatting
  an already-applied revision is fine (Ruff never changes the AST); the
  "never edit an applied revision" rule is about what it does.
- **Frontend: Prettier** (`frontend/.prettierrc.json`: no semicolons, single
  quotes). `npm run format` / `npm run format:check`; linting stays with
  `npm run lint` (oxlint). `src/api/schema.ts` is generated
  (`openapi-typescript`) and in `.prettierignore` -- regenerate it, don't
  format it.
- **CI** (`.github/workflows/ci.yml`) checks all of the above (formatter in
  check mode, both linters, the frontend build), and runs `pytest` plus
  `alembic check` against a throwaway PostgreSQL. A pure-reformat commit
  goes in `.git-blame-ignore-revs`.

## Testing strategy

`uv run pytest` runs against a **real PostgreSQL** -- the same database
engine as production, so filters, joins, constraints, cascades and JSONB
behave in tests exactly as they do deployed:

- `TEST_DATABASE_URL`, if set, points at an empty database to use. Otherwise
  `tests/conftest.py` starts a throwaway `postgres:17-alpine` container
  through testcontainers for the session (needs a Docker daemon), and stops
  it at the end.
- The schema comes from the real Alembic migrations (`alembic upgrade head`
  once per session), not from the models -- a model change without a
  revision breaks the suite.
- **Isolation by rollback.** The `db` fixture opens one connection and one
  outer transaction per test and binds an `AsyncSession` to it with
  `join_transaction_mode="create_savepoint"`: every `commit()` the app (or a
  test) makes becomes a savepoint, and the whole transaction is rolled back
  at teardown. Nothing is truncated or recreated between tests. (Identity
  sequences aren't transactional, so ids keep growing across tests -- never
  assert a literal id.)
- The `client` fixture overrides the app's `get_session` dependency with
  that same session, so data a test inserts directly is what endpoints see
  and vice versa. Each request starts with `expunge_all()`, like a fresh
  per-request session in production. `tests/factories.py` has the setup
  helpers (`add`, `make_rubric`, `make_submission`, `save` -- which also
  flags JSONB columns, see "Database layer rules" -- and `reload`).
- MinIO and Celery are in-process fakes (`monkeypatch` on `storage.*` and
  on the tasks' `.delay`), recorded on the client
  (`client.fake_objects`, `client.delayed_submission_ids`, ...).
- `tests/test_worker_tasks.py` drives the worker's task bodies
  (`_grade_submission`, `_grade_answers`, `_prepare`) against the test
  database with a fake chat model in the same style as
  `tests/test_grading.py`'s -- its `create_agent` replacement must share
  *one* grade queue across calls (`grade_answer` builds a fresh agent per
  question), or every question grades identically instead of in sequence.
  This includes the failure path (extracted answers survive a broken
  provider) and a submission deleted mid-run.

`tests/test_llm.py` needs no live services, but does need all four
provider packages installed (the dev dependency group's job, see
"Pluggable LLM providers") to construct real `ChatOpenAI`/`ChatAnthropic`/
`ChatOllama`/`ChatOpenRouter` instances -- no network call is made (a fake
API key is enough to get past each client's credential check at
construction time). This is deliberately a stronger guarantee than mocking
`get_chat_model` out entirely (as `tests/test_rubrics_api.py`/
`test_grading.py` do for everything downstream of it): it catches a real
kwarg/class mismatch against the actual installed client, which a mock
can't.

## Kubernetes deployment and image builds (`deploy/`, `.github/workflows/images.yml`)

`deploy/README.md` covers how to deploy. This section is about why it's built this way.

- **Kustomize, not Helm.** There's one app with a handful of settings and no
  third-party consumers of a chart. A `base/` plus per-environment overlays
  covers it without a templating language. PostgreSQL, the S3 store and
  the `aitana-secrets` Secret belong to DevOps and are only *referenced*.
  Redis (just a broker) and Gotenberg (stateless) ship in the base. MinIO
  doesn't: in the cluster, the object store is whatever S3 endpoint the
  `MINIO_*` settings point at.
- **The base needs only a `DATABASE_URL`.** `overlays/example/` adds an
  in-cluster PostgreSQL run by CloudNativePG as an *example*
  (`postgres-cluster.yaml`), plus a patch (`database-url.yaml`) feeding the
  operator-generated `aitana-db-app` Secret's `uri` key to the api, its
  `migrate` initContainer and the worker as `DATABASE_URL` (an explicit
  `env` entry wins over `envFrom`). The app accepts that `postgresql://`
  URI as is. No PgBouncer `Pooler`: connection counts are tiny, and asyncpg's
  prepared statements need session pooling if one is ever added.
- **Migrations run in an initContainer** (`migrate`, same image, `alembic
  upgrade head`) on the api Deployment, so a pod only serves on the schema
  its code expects; concurrent replicas serialize on the advisory lock in
  `migrations/env.py`.
- **The frontend image proxies `/api/`.** In production the frontend is nginx
  (`frontend/nginx/default.conf.template`). It serves `dist/` and
  reverse-proxies `/api/` to `aitana-api` with the prefix stripped, so FastAPI
  routes stay at the root (no `root_path`). `VITE_API_URL=/api` is a *build arg*
  with that default, deliberately not per-deployment config: a same-origin
  path makes one image work everywhere, and the Ingress is a single rule with
  no controller-specific rewrites. The nginx template is rendered by the base
  image's envsubst entrypoint, and `NGINX_ENVSUBST_FILTER` restricts
  substitution to `API_UPSTREAM`/`MAX_BODY_SIZE` so nginx's own `$uri`/`$host`
  survive. If you add an endpoint that streams or takes very long, check
  `proxy_read_timeout` (300s) and, on the Ingress, the matching annotations.
- **`frontend/Dockerfile` is multi-stage.** `target: dev` (the Vite dev
  server) is what `docker-compose.yml` builds; the default last stage is the
  production nginx image. Building without `--target` gives you nginx, not
  Vite.
- **The backend image runs as uid 10001**, so it fits clusters that enforce
  `runAsNonRoot`. `HOME`/`DENO_DIR` point inside `/app`, and the Deno/Pyodide
  warmup runs *as that user*, so its cache is readable at runtime. `/app` and
  `/app/node_modules` are chowned non-recursively on purpose: the sandbox
  writes `./node_modules` relative to the working directory (see "Python
  sandbox for grading"), and a recursive chown would copy the whole `.venv`
  into another layer. Because of that runtime write, the root filesystem
  can't be read-only.
- **The backend image has no dev dependencies** (`uv sync --no-dev`): no
  pytest, testcontainers or Ruff, and only the provider extras the
  Dockerfile names (the dev group would otherwise pull in all four,
  `anthropic` included). Tests run in CI (`ci.yml`), never in the image.
- **The root `.dockerignore` is a whitelist**, so a local build can never
  copy `.env` (API keys), `.venv`, `configs/` or student files at the repo
  root into the image. If the image needs a new top-level file (as
  `alembic.ini` does), add an `!` line for it.
- **Resource names are fixed** (`aitana-api`, `aitana-redis`, ...), with no
  `namePrefix`: nginx's `API_UPSTREAM` default and `base/config.env`'s
  `REDIS_URL`/`GOTENBERG_URL` are written against them.
- **Multi-arch CI on native runners.** Each image is built once per platform
  (amd64 on `ubuntu-24.04`, arm64 on `ubuntu-24.04-arm`), pushed by digest,
  and a `merge` job joins them into one manifest list. That's Docker's
  documented "distribute builds across runners" pattern; uv, Deno and npm
  under QEMU are far too slow. PRs build both platforms and push nothing. If
  arm64 hosted runners become unavailable (e.g. the repo goes private), the
  workflow's comment says how to fall back to QEMU. A third job renders every
  `deploy/overlays/*` and checks it with kubeconform (custom resources such as
  the CloudNativePG `Cluster` against the datreeio CRDs catalog).

## Verifying manually: never touch a stack you didn't start

**Do not start the stack** and **do not run commands against running containers**.

Unless explicitly told by the user, there might be running processes started
by the user, and best to not conflict with them. Only start the docker stack
if the user explicitly asks you to do so.
