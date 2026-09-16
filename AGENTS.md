# AGENTS.md

Implementation notes for whoever (human or AI) next touches this codebase.
`README.md` explains what the project does and how to run it; this file
explains *why* the code is shaped the way it is, plus the sharp edges you'll
otherwise rediscover the hard way. `configs/AGENTS.md` covers the rubric YAML
format specifically -- not repeated here.

## Naming

- **`Rubric`**: a rubric is associated to a lab *or* an exam.
- **`Submission`**: a student's deliverable against a rubric. 

## Data model

Five Beanie documents (`src/aitana/documents/`). Read
this section before touching any of `Course`, `Edition`, `Rubric.course`/
`.edition`, or `Student.edition_ids`/`Submission.edition`:

- **`Course`** -- a subject, e.g. "BDM". `slug` (unique); same creation
  shape/helper (`slugify`, `src/aitana/slugify.py`, shared with `Rubric` and
  `Edition`) throughout. Reused across years -- it does not itself carry a
  term/year.
- **`Edition`** -- a term, e.g. "2026/27". **Global -- no `course` field.**
  An edition is just `{name, slug, created_at}`, created once
  (`POST /editions`, no course involved at all) and referenced from as many
  courses as actually use that term. Which course(s) an edition applies to
  is derived, never stored: a `Rubric` pins one course *and* (optionally)
  one edition (see below) -- that's the only place the two are actually
  tied together. The frontend computes "editions used by this course" /
  "courses using this edition" by scanning rubrics for matching
  `course`/`edition` pairs (`frontend/src/lib/editions.ts`), shown on the
  Courses and Editions pages respectively.
- **`Student`** -- roster entry. Unique index on `student_id` (the
  institutional id, not the Mongo `_id`). `edition_ids:
  list[PydanticObjectId]` is a **plain list of raw ids, not a Beanie
  `Link[Edition]` list** -- deliberate: Beanie's query sugar for a *single*
  `Link` field is solid, but querying a *list* of Links doesn't have the
  same sugar and would reintroduce the `fetch_links`/`mongomock` gaps (sharp
  edge #5) just to render a chip list of edition names. A plain id array
  plays directly with Mongo's native "array contains scalar" equality
  (`Student.find(Student.edition_ids == edition_id)` -- no aggregation, no
  Link machinery, works identically under `mongomock` and real Mongo), at
  the cost of doing edition-name lookups as a separate `GET /editions` call
  from the frontend rather than embedding resolved edition docs. There is
  **deliberately no `course_ids`** either -- and, since `Edition` carries no
  course anymore, a student's course is no longer derivable from
  `edition_ids` at all (it never told you *which* course's "2026/27" a
  student was in anyway, once editions could be shared -- this enrollment
  was always course-agnostic in spirit, just not in the data model until
  now). This is *enrollment* only -- it is not consulted when filtering
  submissions (see `Submission.edition` below, and "Creating courses and
  editions").
- **`Rubric`** -- one per lab/exam. `slug` (unique) identifies it; see
  "Creating rubrics" below for how it's derived. `course: Link[Course]`
  (**required** -- every rubric belongs to a course) and `edition:
  Link[Edition] | None` (**optional** -- a lab is often unchanged between
  editions, left unset and reused as-is across years; an exam is typically
  unique to one edition, set it). `created_at`/`updated_at` (not a version
  field) are how a TA recognizes "this is an updated version" of a reused
  rubric across years. `format: SubmissionFormat` (default `pdf`) fixes
  which file format this rubric's submissions must be uploaded as -- see
  "Pluggable submission formats" below for why this lives on the rubric, not
  the submission. `grading_scale: dict[str, str]` (default the original
  4-level scale) is the `{level_id: description}` the LLM grades against --
  see "Pluggable grading scales" below. `questions: list[Question]` is
  embedded, not referenced.
- **`Submission`** -- normally one per student x rubric. `student`,
  `rubric`, `edition`, and `batch` are all Beanie `Link` fields; `student`
  and `batch` are the only optional ones. `edition` is set on *every*
  submission, even when its rubric has no fixed edition of its own -- see
  "Creating a submission" below for exactly how it's resolved; this is what
  makes `edition_id` filtering direct (sharp edge #3) instead of needing to
  go via `Student.edition_ids`. `file_object_key` points at the raw uploaded
  file in MinIO (name is format-agnostic on purpose -- not `pdf_object_key`
  -- since it might be a notebook; see "Pluggable submission formats"
  below). `answers: list[AnsweredQuestion]` is embedded and grows/fills in
  as grading progresses, `status` drives the frontend's polling. `student`
  is unset for a submission created by a batch upload (see `Batch` below
  and "Batch submission import"): the zip's per-item folder name isn't
  necessarily identifiable against the roster at upload time, and grading
  doesn't need `student` at all, so this doesn't block it. `batch: Link[
  Batch] | None` and `batch_internal_id: str | None` are set together, only
  for a batch-created submission -- `batch_internal_id` is that item's
  per-student folder name from the zip, kept verbatim (e.g., for an Atenea
  batch, `<surname(s)> <name>_<internal_id>_assignsubmission_file`) for a
  later feedback-export feature to key off of when writing grades back into
  the originating LMS.
- **`Batch`** -- one per zip upload (`api/routers/batches.py`). `rubric:
  Link[Rubric]` and `edition: Link[Edition]` are both required, resolved
  the same way a single submission's are (see "Batch submission import"
  below) -- no `course` field, same reasoning as `Rubric`/`Submission`
  never storing one either (always derivable via `rubric.course`). `type:
  BatchType` records which zip layout was used (`atenea` today).
  `item_count` is set once, at creation, from however many submissions the
  zip actually produced.

`Question` and `Grade` live in `src/aitana/grading/models.py`, *not* in
`documents/`, on purpose: they're plain Pydantic models (not Beanie
Documents) reused in two places -- as the grading-time input/output
(`grading/grading.py`'s `grade_answer`) and embedded verbatim inside
`Rubric` and `Submission`. Keeping them DB-agnostic means `grading/` has
zero Beanie dependency and could be unit-tested (or reused) without a
database at all. Don't move grading logic to depend on `documents/` -- it
would tangle a pure function with ODM state for no benefit.

`Question` has both a `title` (short label, UI-only -- lists/headers) and a
`question` (the actual task text sent to the LLM) -- don't conflate them;
`title` is never part of the grading prompt. `context` is optional
environment/state info (mainly useful for a lab: what the student's
environment already looks like at this point) included in the prompt only
when set. `model_answer` is likewise optional: a worked/canonical answer,
for the subset of questions that actually have one (e.g. "what command
fixes this") as opposed to an open-ended one -- also included in the prompt
only when set. See "Grading internals" below for exactly how these compose
into the system prompt.

`Question.field` (exposed as the `answer_key` property, `field or id`) used
to be called `pdf_field` -- renamed because it no longer means "PDF AcroForm
field name" specifically, see "Pluggable submission formats" below.

## Pluggable submission formats

Multiple document format are accepted (at the moment: AcroForm PDF documents
and Jupyter Notebook .ipynb files). The format of the document conditions
the extraction step.

- **`SubmissionFormat`** (`grading/models.py`) is the enum of supported
  formats -- currently `pdf` and `notebook`. It lives in `grading/models.py`
  next to `Question`/`Grade`, not in `documents/`, for the same
  DB-agnostic-reuse reason those do (see above).
- **Format lives on the `Rubric`, not the `Submission`.** A rubric's
  `questions[].field` values only make sense under one format (a PDF
  AcroForm field name vs. a notebook cell tag) -- a rubric authored for one
  format can never sensibly accept the other, so asking the uploader to
  repick format per-file would just be a second place that can disagree
  with the rubric it's submitted against. `Rubric.format` is set once, at
  rubric-creation time (`POST /rubrics`'s JSON body, or `POST
  /rubrics/upload`'s `format` form field / top-level `format:` YAML key,
  same precedence order as `course_slug`/`edition_slug` -- see
  `configs/AGENTS.md`), and defaults to `pdf` so every rubric created before
  this existed keeps working unchanged. Editable via the edit endpoints (see
  "Editing rubrics" below), but changing `format` on a rubric that already
  has submissions against it is rejected with `409` (`_guard_format_change`)
  -- a submission doesn't record its own format, only `rubric.format` at
  grading time, so allowing the flip would make existing submissions'
  stored answers permanently unre-explainable.
- **Extraction is pluggable** (`grading/extraction/`): `__init__.py`
  exposes `extract_answers(fmt, path) -> dict[answer_key, text]`, dispatching
  to `pdf.py` or `notebook.py` via a `_EXTRACTORS` dict, plus
  `FORMAT_FILE_INFO: dict[SubmissionFormat, (extension, content_type)]` used
  by both the upload path (`storage.object_key`, extension-mismatch
  validation in `create_submission`) and the download path (`Content-Type`
  + filename extension in `download_submission_file`). Adding a third format
  means writing one `(path) -> dict[str, str]` function in a new sibling
  module and registering it in both dicts -- nothing in `worker/tasks.py` or
  the API routers needs to change.
- **`pdf.py`** is the `pypdf`-based AcroForm-field reader.
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
- **`storage.py`** has no format-specific logic at all -- `upload_file(key,
  data, content_type)` and `object_key(rubric_slug, student_id,
  submission_id, extension)` both take the format-derived bits as plain
  parameters (from `FORMAT_FILE_INFO`) rather than assuming PDF.
- `create_submission` (`api/routers/submissions.py`) does one cheap
  extension check (`file.filename` against `FORMAT_FILE_INFO[rubric.format]`)
  and 422s on an obvious mismatch (e.g. uploading a `.ipynb` against a
  `pdf`-format rubric) -- this is a courtesy check on the *filename*, not a
  content sniff, so a mislabeled file can still slip through to a confusing
  extraction failure at grading time; it just catches the common "wrong
  file picked in the form" case early.

## Pluggable grading scales

A rubric can define its own scale entirely, or use one of the provided ones
out-of-the-box.

- **`Rubric.grading_scale: dict[str, str]`** (`documents/rubric.py`) is
  `{level_id: description}`, e.g. `{"fail": "...", "pass": "..."}`.
  Defaults to `DEFAULT_GRADING_SCALE` (`grading/models.py` -- a
  4-level scale). A `field_validator` rejects an empty dict (a
  rubric with zero valid levels can't grade anything, and the blank-answer
  short-circuit below would `StopIteration` on `next(iter({}))`).
- **Order matters and is a documented convention, not incidental**: entries
  run worst -> best.
  - `grade_answer` (`grading/grading.py`) uses the *first* key verbatim as
    the level for a blank answer -- no LLM call needed to know a blank
    answer is the worst case, and this works for any scale (even one with
    no level literally named `not_attempted`, e.g. a pass/fail scale) precisely
    because it never hardcodes the identifier, just "whichever key is
    first."
  - The frontend's `GradeBadge` colors levels along a single-hue ordinal
    ramp by this same position (lightest = first/worst, darkest =
    last/best) -- see its own comment for the specific palette steps and
    why a single hue, not a red-to-green rainbow (per the dataviz skill's
    color-formula: this is an **ordinal** job -- "position in a sequence,"
    monotone lightness on one hue -- not a **status** job, which would
    assume a small fixed vocabulary this data deliberately doesn't have).
- **`Grade.level` is a plain `str`**, not an `Enum` -- which identifiers are
  valid depends on which rubric graded the answer, so it can't be a fixed
  type. Validation that an LLM call actually returned one of *this* rubric's
  configured levels happens per-call instead, via
  `grade_schema_for_scale(grading_scale)` (`grading/models.py`): a
  `pydantic.create_model()`-built one-off schema whose `level` field is a
  `Literal[...]` over that scale's keys, passed to
  `chat_model.with_structured_output(...)`. `grade_answer` then normalizes
  whatever comes back into a real `Grade` instance (`Grade(level=raw.level,
  feedback=raw.feedback)`) -- the dynamic schema instance is never stored or
  passed around beyond that one call, so nothing downstream needs to know
  it existed.
- **The prompt's grading-levels list is built, not hardcoded**:
  `build_grading_section(grading_scale)` renders `{level_id: description}`
  as a `- level_id: description` list, interpolated into
  `SYSTEM_PROMPT_TEMPLATE`'s new `# Grading` section (previously this text
  was a literal, unchangeable part of the template).
- This is deliberately *not* a `grading/scales/` plugin package the way
  `grading/extraction/` is for submission formats -- there's no per-scale
  *code* to dispatch to, just data (an ordered dict) that flows through the
  same prompt-building and structured-output-schema logic regardless of its
  contents. Don't add a scales-registry abstraction unless a genuinely
  different *mechanism* (not just different level names) shows up.
- **Frontend presets, not a fixed list.** Any `{id: description}` dict is a
  valid `grading_scale` -- there's no server-side enum of "allowed" scales.
  `frontend/src/api/gradingScalePresets.ts` offers a few starting points
  (including the original 4-level one, kept in sync with
  `DEFAULT_GRADING_SCALE` by hand -- there's no shared source of truth
  across the language boundary) in the "Build manually" rubric form's
  quick-pick dropdown, which then loads into a free-form, fully editable
  id/description list (`GradingScaleEditor` in `NewRubricForm.tsx`) -- picking
  a preset is a convenience starting point, not a constraint.
- No form-field equivalent on `POST /rubrics/upload` the way `format` has
  one (a dict doesn't fit a multipart field cleanly) -- only the YAML body's
  top-level `grading_scale:` key, or omit it for the default. See
  `configs/AGENTS.md`.

## Python sandbox for grading

Per-question opt-in, not a rubric-wide setting: `Question.needs_python_sandbox`
(`grading/models.py`, default `false`; see `configs/AGENTS.md` for the YAML
key). When set, `grade_answer` (`grading/grading.py`) gives the grading LLM a
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

## Creating rubrics (`api/routers/rubrics.py`)

Two creation paths, both creation-only (no update/edit endpoint; a duplicate
`slug` is rejected with `409` via the shared `_insert_rubric` helper):

- **`POST /rubrics`** -- JSON body (`RubricCreate`: `title`, optional
  `slug`, `course_id` **required**, optional `edition_id`, optional
  `format`/`grading_scale` (default `pdf`/the 4-level scale, see
  "Pluggable submission formats"/"Pluggable grading scales" below),
  `questions: list[Question]`). This is the frontend's "Build manually" form
  (`frontend/src/components/NewRubricForm.tsx`).
- **`POST /rubrics/upload`** -- multipart (`yaml_file`, optional `slug`/
  `course_id`/`edition_id`/`format` form fields). Parses the same YAML shape
  `configs/*.yaml` documents (`grading_scale` has no form-field equivalent,
  YAML key only). This is the frontend's "Upload YAML" form (same
  component).

Slug resolution order (`upload_rubric_yaml`): the `slug` form field, then a
top-level `slug:` key in the YAML body, then the uploaded filename with
`_questions` stripped and slugified (`slugify()`, a simple
lowercase-and-underscore regex, `src/aitana/slugify.py` -- not the same
thing as a MongoDB/Beanie feature). `create_rubric` (the JSON path) uses
`slug` if given, else slugifies `title`. Malformed YAML or a YAML missing
the top-level `questions` key returns `422`, not `500` -- see the
`try`/`except` around `yaml.safe_load` and `Question(**q)` construction in
`upload_rubric_yaml`.

Course resolution order (`upload_rubric_yaml`): the `course_id` form field,
then a top-level `course_slug:` key in the YAML body (looked up via
`Course.find_one(Course.slug == ...)`); if neither resolves, `422` (course
is required, unlike edition). Edition resolution is the same shape via
`edition_id`/`edition_slug:`, but optional -- no match found because
neither was given is fine, a matched-but-unknown value is still `404`.
`_require_yaml_string` guards both `course_slug` and `edition_slug` (and
the top-level `slug`) against a real YAML footgun: an unquoted scalar like
`2026_27` parses under PyYAML's default (YAML 1.1) resolver as the
*integer* `202627`, not the string `"2026_27"` -- underscores inside a
number are digit separators, same as Python's `1_000_000` literal. That's
exactly the shape `slugify("2026/27")` produces now that edition slugs no
longer have a course prefix to save them from looking numeric (see
"Creating courses and editions" above). Without the guard this fails at the
`Edition.find_one(...)` lookup with a baffling
`Edition 202627 not found` 404; with it, a `422` names the field and tells
you to quote it. `create_rubric` (the JSON path) just takes
`course_id`/`edition_id` directly -- untouched by this, since a JSON body
has no such ambiguity for numeric-looking strings. All paths pass the
resolved `Course`/`Edition` documents (or
`None` for edition) straight into `Rubric(course=..., edition=...)` --
since these are the in-memory objects from `await Course.get(...)`/
`find_one(...)`, not re-fetched ones, the API response's `course`/`edition`
fields come back fully resolved (name, slug, etc.) even though `mongomock`
can't resolve them on a subsequent *read* (sharp edge #5) -- see the
rubric-creation tests in `tests/test_rubrics_api.py` for why they can
assert `resp.json()["course"]["name"]` directly.

## Editing rubrics (`api/routers/rubrics.py`)

A separate `PUT`, not an upsert on the creation endpoints: `POST /rubrics`
and `POST /rubrics/upload` stay creation-only (a duplicate `slug` is still a
plain `409` there, unconditionally). Two edit paths, mirroring the two
creation paths and the frontend's two `NewRubricForm.tsx` modes:

- **`PUT /rubrics/{rubric_id}`** -- JSON body, same shape as `RubricCreate`.
  The "Build manually" edit path.
- **`PUT /rubrics/{rubric_id}/upload`** -- multipart, same fields as `POST
  /rubrics/upload`. The "upload updated version" edit path: the YAML
  file's contents *replace* the rubric's `title`/`course`/`edition`/
  `format`/`grading_scale`/`questions` wholesale, the same as re-running
  "Build manually" with every field repopulated from the file. Unlike
  creation, the slug (absent a form field or YAML `slug:` key) defaults to
  the rubric's *current* slug, not one derived from the uploaded filename --
  the point of this flow is updating an existing rubric's content, not
  accidentally renaming it because the new file happens to be named
  differently.

Both share `_apply_rubric_update` (mirrors `_insert_rubric`): it mutates the
existing `Rubric` document's fields directly and calls `.save()`, which
re-validates the whole document the same way `.insert()` does (`ValidationError`
-> `422`, same as creation) -- see `Document.save()`'s `validate_on_save`
behavior, already relied on by `worker/tasks.py`'s `_grade_answers`.
`updated_at` is bumped explicitly (unlike `created_at`, nothing does this
for you on a plain attribute-assignment + `save()`).

Two things an edit does differently from creation:

- **Slug conflicts only 409 against a *different* rubric**
  (`_check_slug_available(slug, exclude_id=rubric.id)`) -- saving a rubric
  back with its own unchanged slug, or changing every field except the
  slug, isn't a conflict with itself the way it would be during creation.
- **Format changes are guarded** (`_guard_format_change`) -- see
  `Rubric.format`'s docstring / "Pluggable submission formats" above for
  why: `409` if the rubric already has any `Submission` against it and the
  edit tries to change `format`. A rubric with zero submissions yet (still
  being drafted) can change format freely.

Nothing about `slug`/`course_id`/`edition_id` is protected from being
changed on edit *at the API level* -- `PUT /rubrics/{id}` still takes all
three in the body, same shape as creation, and `PUT /rubrics/{id}/upload`
still takes `course_id`/`edition_id` form fields (just not `course_slug:`/
`edition_slug:` YAML keys, see above) and a `slug` form field. The
*frontend* (`RubricForm.tsx`) locks all three to the rubric's current values
during edit -- greyed-out `<select>`s for course/edition, a disabled slug
input pre-filled with the current slug rather than left blank -- deliberately,
not because the API enforces it: changing a rubric's course/edition
mid-life is a bigger structural move than "edit this rubric's content," and
changing `slug` changes which `configs/<slug>_questions.yaml` a future
re-upload is expected to match. If a script/API caller genuinely needs to
move a rubric to a different course or rename its slug, `PUT /rubrics/{id}`
still allows it directly -- only the UI's manual/YAML edit forms don't
expose it.

## Deleting rubrics

`DELETE /rubrics/{rubric_id}` (`delete_rubric`) -- `204` on success, `404`
if the rubric doesn't exist. Blocked with `409` if any `Submission` or
`Batch` already references it (`Submission.rubric`/`Batch.rubric` are both
required `Link[Rubric]` fields that every submission/batch view resolves to
render `rubric.slug`/`.title`/`.questions` -- deleting out from under them
would leave dangling references, not just remove an unused definition). A
rubric nobody has submitted anything against yet (still being drafted, or
created and abandoned) deletes freely. Same `Link.id == ...` query shape
(and the same "can't positively unit-test this under mongomock" situation,
sharp edge #3) as `_guard_format_change`.

The frontend (`RubricDetailPage.tsx`) exposes this as a "Delete" button next
to "Edit", behind a native `confirm()` -- there's no undo, and a `409` here
means "go deal with the submissions/batches first," not "retry."

### Interactively testing a rubric (`POST /rubrics/{id}/test-answer`)

A "try it before you upload a real exam" endpoint: `{question_id, answer}`
in, a `Grade` (level + feedback) out, synchronously. No `Submission` is
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

## Batch student import (`api/routers/students.py`)

`POST /students/import` -- multipart (`file`, `format` form field). Bulk
creates/updates roster entries from a CSV, alongside the existing one-at-a-
time `POST /students`.

- **`StudentImportFormat`** is the enum of supported CSV shapes -- currently
  just `atenea` (UPC's Moodle instance's roster export). Same
  registered-parser-function shape as `SubmissionFormat`/`_EXTRACTORS`
  (`grading/extraction/__init__.py`, see "Pluggable submission formats"
  above): `_IMPORT_PARSERS: dict[StudentImportFormat, Callable[[bytes],
  list[_ImportedStudentRow]]]`. Add a new format by writing one
  `(bytes) -> list[_ImportedStudentRow]` function and registering it there --
  nothing else in the endpoint changes.
- **`_parse_atenea_csv`** expects `ID number` and `First name` columns
  (matched case-insensitively against the header row, see below for why
  both are required); `Last name`/`Username`/`Email address`/`Group` are all
  optional. `csv.Sniffer` picks the delimiter off the header line (comma or
  tab both seen in the wild from Atenea's different export paths), falling
  back to comma if sniffing is inconclusive.
- **`ID number` and `First name` are the only required columns**, for two
  different reasons, both enforced the same way (missing header column ->
  `422` before any row is even read; missing/blank value in a data row ->
  `422` that aborts the **whole** import, nothing written, not even rows
  before it):
  - `ID number` is the sole source of `Student.student_id` -- the unique
    roster key every other endpoint (`Submission.student`, `PUT
    /students/{id}/editions`, ...) already joins against. It can't be
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
  blank).
- **`username: str | None`** and **`group: str | None`** (`documents/
  student.py`) are both new, purely informational metadata -- neither is a
  lookup key (`student_id` fills that role) and `group` is not an
  `Edition`. Don't conflate `group` with an edition: a roster "group" here
  is typically a lab/seminar subdivision *within* one edition, not a term,
  and nothing else in the app (submission filtering, editions/courses)
  reads it today.
- **Existing-student handling is upsert-by-`student_id`, and it's a full
  overwrite, not a merge**: `name`/`username`/`email`/`group` are all set to
  whatever the row says, including clearing a field the row leaves blank. A
  re-export is expected to be the current source of truth for the roster,
  not something to be reconciled field-by-field against what's already
  there. `edition_ids` is never touched by this endpoint either way --
  enrollment stays a separate, explicit step (`PUT
  /students/{id}/editions`).
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
  `_resolve_edition` helper `create_submission` uses (the rubric's own
  edition if it has one, else the uploader must supply one) -- imported
  from `submissions.py` rather than duplicated.
- **`BatchType`** is the enum of supported zip layouts -- currently just
  `atenea` (UPC's Moodle instance's per-assignment "download all
  submissions" export). Same registered-parser-function shape as
  `StudentImportFormat`/`_IMPORT_PARSERS` above:
  `_BATCH_PARSERS: dict[BatchType, Callable[[zipfile.ZipFile],
  list[_BatchItem]]]`. Add a new layout by writing one
  `(zipfile.ZipFile) -> list[_BatchItem]` function and registering it there.
- **`_parse_atenea_zip`** expects one top-level folder per student
  submission, each folder containing exactly one file. An Atenea export
  names each folder `<surname(s)> <name>_<internal_id>_assignsubmission_
  file` -- this is stored **verbatim**, not parsed into its name/
  internal-id parts, as `Submission.batch_internal_id`. A later, separate
  feedback-export feature reads that id back off the submission to write
  grades into the originating Atenea assignment against the same item --
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
  zip before grading starts on every item.
- Created submissions have **no `student`** by default -- see
  `Submission.student`'s docstring in "Data model" above. Grading runs
  anyway (`worker/tasks.py` never touches `submission.student`); only
  per-student attribution is deferred.
- **`_match_student`** makes a best-effort auto-match right at upload time,
  so most items don't stay unmatched: it strips Atenea's fixed `_<internal_
  id>_assignsubmission_file` suffix off the folder name, tokenizes what's
  left into lowercase, accent-folded words (`_name_tokens`), and compares
  that *set* against each roster student's `Student.name` tokenized the
  same way. Set comparison, not string comparison, because Atenea's folder
  names are "surname(s) first name" while `Student.name` (see "Batch
  student import" above) is "first name surname(s)" -- token order differs,
  token *content* doesn't. Only commits when **exactly one** student's
  tokens match; zero or more-than-one (e.g. two students sharing a name)
  is left `None` rather than risk a wrong guess. The whole roster is loaded
  once (`Student.find_all()`) and matched in memory against every item,
  not queried per item.
- Batch items are stored under `storage.batch_object_key(rubric_slug,
  submission_id, extension)` (`f"{rubric_slug}/_batch/{submission_id}
  {extension}"`) rather than `storage.object_key`, since there's no student
  id to namespace by yet.
- Response (`BatchUploadResult`: `batch`, `created`, `submissions`) mirrors
  `StudentImportResult`'s shape.
- Whatever `_match_student` couldn't resolve (or got wrong) is fixed up
  through `PUT /submissions/{submission_id}/student` (`api/routers/
  submissions.py`, body `{student_id}`) -- works on any submission, not
  just a batch-created one. It fetches the submission once with
  `fetch_links=True` (same `_SHALLOW_LINKS` cap as every other submissions
  endpoint), overwrites `.student` with the freshly-`Student.get()`-fetched
  document, and returns that same in-memory object rather than re-fetching
  afterwards -- a second `Submission.get(..., fetch_links=True)` right
  after `.save()` is exactly the nested-`$lookup`-under-mongomock gap
  (sharp edge #6) again, and re-fetching buys nothing a plain in-memory
  assignment doesn't already give for free.
- `POST /batches/{batch_id}/regrade` is the bulk version of `POST
  /submissions/{id}/regrade` -- same per-submission reset (`status` back to
  `pending`, `error` cleared, re-enqueued), just applied to every submission
  `Submission.find(Submission.batch.id == batch_id)` returns instead of
  one. Response is `{regraded: <count>}`. This is the same Beanie Link-query
  pattern as the `batch_id` filter on `GET /submissions` above (verified
  against real MongoDB, not exercised end-to-end under mongomock -- see
  `test_regrade_batch` in `tests/test_batches_api.py`).

Both are creation-only, same `409`-on-duplicate-slug shape as rubrics.
`POST /courses` takes just `name` (+ optional `slug`). `POST /editions`
takes only `name` (+ optional `slug`, defaulting to `slugify(name)`) --
**no `course_id`**, since editions are global (see "Data model" above).
Note the slug default changed shape along with this: it used to be
`slugify(f"{course.slug}_{name}")` (e.g. `bdm_2026_27`), which was
incidentally never ambiguous to YAML. A bare `slugify(name)` for something
named "2026/27" produces `2026_27` -- a YAML footgun waiting to happen, see
`_require_yaml_string` in "Creating rubrics" below.

**Filtering submissions by course vs. by edition are genuinely different
relationships -- don't conflate them:**

- `course_id` filters via the **rubric's** course:
  `Rubric.find(Rubric.course.id == course_id)` -> rubric ids ->
  `In(Submission.rubric.id, rubric_ids)`. "Show me all BDM submissions"
  means "submissions whose rubric belongs to BDM," regardless of who
  submitted them or which edition it was submitted under.
- `edition_id` filters **directly** on the submission's own `edition`
  field: `Submission.find(Submission.edition.id == edition_id)`. No
  indirection through `Student.edition_ids` needed -- every submission
  already carries its own definitive edition (see "Creating a submission"
  below for how that's decided at creation time). This is simpler than the
  original design (which went via student enrollment) and was changed
  specifically because `Submission.edition` was introduced -- don't
  reintroduce the indirect version.

Both `course_id`'s two-step lookup and the `edition_id` `==` are the same
"Beanie Link query, verified against real MongoDB" pattern (see sharp edge
#3). Passing both `course_id` and `edition_id` together ANDs them, which
only returns results where the rubric's course and the submission's
edition *happen* to line up (the normal case, but not a foreign-key-enforced
one -- nothing stops a BDM rubric from being submitted under an edition no
BDM rubric otherwise uses, if the uploader picks it explicitly; this was
already true before editions became global, and is *more* clearly
by-design now that an edition never claimed to belong to one course in the
first place).

## Creating a submission (`api/routers/submissions.py`)

`POST /submissions` needs a definite `edition`, but not every rubric fixes
one (see "Data model" above). `_resolve_edition` decides it:

- If the rubric has a fixed `edition`, that edition is used, full stop --
  any `edition_id` the client sent is ignored. `rubric` is fetched without
  `fetch_links` here (`Rubric.get(rubric_id)`, no aggregation), so a set
  `rubric.edition` is an unresolved `Link` -- `.fetch()` on it does a plain
  get-by-id (no `$lookup`, so none of the sharp-edge-#6 nested-pipeline
  concerns apply here regardless).
- If the rubric has no fixed edition, the client's `edition_id` is
  required -- `422` if missing, `404` if it doesn't resolve.

The frontend mirrors this exactly (`UploadSubmissionPage.tsx`): picking a
rubric with a fixed edition shows it read-only; picking one without shows
an editable, required dropdown listing every edition (global, not scoped to
the rubric's course -- see "Data model" above).

`POST /submissions` does the fast part synchronously (validate student/
rubric exist, resolve the edition, upload the raw file to MinIO, insert a
`Submission` with `status=pending`) and enqueues
`grade_submission.delay(id)`. The Celery task does the slow part:

```
pending -> extracting (raw file -> {answer_key: text}, via whichever
                        extractor rubric.format selects)
        -> grading (answers populated with student_answer, grade=None;
                     saved BEFORE the LLM client is constructed, so a
                     missing API key / bad provider config still leaves the
                     extracted answers visible, not just an opaque failure)
        -> graded (each answer's grade filled in one LLM call at a time via
                    `_grade_answers`, saved after every single question --
                    not batched -- so polling clients see partial progress;
                    see sharp edge #1 below for a real bug this hit)
   or -> failed (error message captured on the document)
```

If you change this flow, preserve the "persist extracted answers before the
LLM step" ordering -- it was deliberately reordered during development after
noticing a missing-API-key failure left `answers: []` even though extraction
had already succeeded.

Beanie/PyMongo are async; Celery tasks are sync. Each task does
`asyncio.run(_grade_submission(...))` and calls `init_db()` (which creates a
*new* `pymongo.AsyncMongoClient` and re-runs `init_beanie`) fresh every
invocation, because an async Mongo client is bound to the event loop that
created it, and each `asyncio.run()` call gets a new loop. This means no
connection pooling across tasks -- acceptable for this app's volume, but if
grading throughput ever becomes a bottleneck, look here first (e.g. a
long-lived worker-process event loop via `celery_app.py`'s
`worker_process_init`/`worker_process_shutdown` signals, instead of
`asyncio.run` per task).

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

## Known-sharp-edges in the Mongo driver stack

These cost real debugging time once; if something in this area breaks again
after a dependency bump, start here.

**1. `Document.save()` replaces embedded list items with new objects --
never hold a reference into `submission.answers` across a `save()` call.**
This one actually shipped and was caught by a user report: every question
after the first came back with `grade: null` even though the worker's logs
showed the LLM was called and graded all of them correctly. Root cause:
Beanie's `save()` is decorated with `@validate_self_before`, which (when
`Settings.validate_on_save` is on, the default) does `new_model =
parse_model(self.__class__, get_model_dump(self)); merge_models(self,
new_model)` -- i.e. it re-serializes the whole document and re-parses it
into brand-new model instances, then swaps them into `self`, on *every*
`save()` call. `worker/tasks.py`'s original grading loop did:

```python
for question, answer in zip(rubric.questions, submission.answers, strict=True):
    answer.grade = grade_answer(chat_model, question, answer.student_answer)
    await submission.save()
```

`zip(...)` binds to the `submission.answers` list object *once*, before the
loop starts. After the first `save()`, `submission.answers` is silently
swapped for a list of new `AnsweredQuestion` instances (same values, new
identity) -- but the loop's `answer` variable for later iterations still
points at the *old*, now-orphaned objects. Mutating `answer.grade` on
iteration 2+ does nothing to what actually gets saved. The fix
(`_grade_answers` in `worker/tasks.py`) re-indexes into
`submission.answers[i]` fresh on every iteration instead of reusing a
captured reference. **The general rule: after any `await doc.save()`, treat
every previously-held reference into that document's nested
lists/sub-models as potentially stale -- re-fetch it from `doc` again.**
`tests/test_worker_tasks.py` regression-tests this against `mongomock`
(confirmed to fail with the old `zip()` loop and pass with the fix -- this
bug reproduces under `mongomock` too, since it's pure Pydantic/Beanie
behavior, not a driver quirk).

**2. Beanie + Motor is broken with current PyMongo -- use PyMongo's native
async client instead.** `src/aitana/db.py` uses `pymongo.AsyncMongoClient`,
not `motor.motor_asyncio.AsyncIOMotorClient`. Beanie's `init_beanie()` does
a driver-metadata handshake (`if callable(database.client.append_metadata):
database.client.append_metadata(...)`, gated on a PyMongo >=4.14 feature).
Motor's classic client `__getattr__` treats *any* unknown attribute access
as "give me a database with this name" (`client.append_metadata` silently
returns a `MotorDatabase` instead of raising `AttributeError`), and
`MotorDatabase` happens to define `__call__` (to raise a friendly error if
you try to invoke a database like a function) -- so `callable(...)`
evaluates `True`, Beanie calls it, and it crashes with `TypeError:
MotorDatabase object is not callable`. Verified against a real `mongod`
that swapping to `pymongo.AsyncMongoClient` (whose `.append_metadata` is a
real method) avoids this entirely. **If you ever see that exact error
message, this is why** -- don't chase it as a new bug, and don't switch back
to Motor without re-verifying against a real MongoDB first.

**3. Query a Document's Link fields with Beanie's query sugar, not raw
dicts.** Link fields (e.g. `Submission.student`/`.rubric`/`.edition`) are
stored as Mongo `DBRef`s. To filter by the referenced id, use
`Submission.find(Submission.edition.id == some_id)` (see
`api/routers/submissions.py::list_submissions`) -- not
`Submission.find({"edition.$id": some_id})`. The raw-dict form looks like
valid MongoDB DBRef query syntax and is *documented* as such, but it was
tried first here and silently returned zero results against `mongomock`
(and wasn't re-verified against real Mongo before being replaced, so treat
it as suspect there too). Beanie's `Link.id ==` form is verified working
against real MongoDB, including combined with `fetch_links=True`. The same
goes for `In(Submission.rubric.id, [id1, id2, ...])`
(`beanie.operators.In`, used by `list_submissions`' `course_id` filter --
see "Creating courses and editions") -- also verified against real
MongoDB, same field-path mechanics as `==`, just `$in` instead of an
implicit equality. Not exercised under `mongomock` for the same reason
`==` isn't (see the `course_id`/`edition_id` tests in
`tests/test_submissions_api.py`, which only assert the "no matches -> empty
list" case). Confirmed hands-on while building rubric editing's
`_guard_format_change` (`api/routers/rubrics.py`): even the exact filter
dict Beanie builds for `Submission.rubric.id == some_id`
(`{"rubric.$id": ObjectId(...)}`) returns zero rows against `mongomock` when
run as a raw `collection.find(...)`, for a document that demonstrably
matches it -- this is `mongomock` failing to match inside a stored `DBRef`,
not a Beanie translation bug. Same story for `fetch_links=True` combined
with `nesting_depths_per_field` capping (`sharp edge #6`'s workaround
doesn't help here either). If you add another endpoint that needs to check
"does any X reference this Y" via a Link filter, expect the same
can't-positively-unit-test-it-under-mongomock situation `_guard_format_change`
is in.

**4. `mongomock`/`mongomock-motor` need three test-only compatibility
shims** (`tests/conftest.py`, module-level, applied once on import):
   - `mongomock.Database.list_collection_names` doesn't accept the
     `authorizedCollections`/`nameOnly` kwargs current PyMongo passes
     through, which Beanie's startup collection scan needs.
   - `mongomock_motor.AsyncMongoMockCollection.aggregate` is a plain sync
     method; real Motor/PyMongo's `aggregate()` is awaitable. Beanie's
     `fetch_links=True` compiles to a `$lookup` aggregation and does `await
     collection.aggregate(...)`, which breaks without this shim.
   - `mongomock`'s `$lookup` handler hard-rejects a `pipeline` key even
     when it's an empty list (see sharp edge #6 below for why one shows up
     at all).

   These only patch the mongomock/mongomock-motor packages, never
   production code paths. If tests start failing after bumping
   `mongomock`/`mongomock-motor`/`pymongo` versions, check whether these
   shims are still needed (the upstream bug might be fixed) or need
   adjusting (the upstream signature might have changed again).

**5. Even with the shims above, `mongomock`'s `$lookup` emulation doesn't
actually resolve DBRefs.** `fetch_links=True` under `mongomock` runs
without crashing but leaves e.g. `submission.student`/`.rubric`/`.edition`
as unresolved `Link` objects (their JSON serializes back to the `{id,
collection}` reference shape) rather than the full document. This was
confirmed correct against a real `mongod` (full nested documents come back
as expected) and is a `mongomock` limitation, not a Beanie or app bug.
Tests in `tests/test_submissions_api.py`, `tests/test_rubrics_api.py`, and
`tests/test_editions_api.py` are written to only assert what `mongomock`
can actually exercise, with a comment explaining the gap. **Don't "fix"
this by weakening `fetch_links` in production code to work around a test
double's limitation** -- if you need to actually verify Link resolution,
spin up a real `mongo:7` container (`docker run --rm -p <port>:27017
mongo:7`) the way this was originally verified, rather than fighting
`mongomock` further.

**6. A Link field whose target type *itself* has a Link field makes
`mongomock` hard-crash, not just silently fail to resolve -- cap
`nesting_depths_per_field` on it.** This surfaced the moment `Rubric`
gained `course: Link[Course]`: every `fetch_links=True` query on
`Submission` (which links to `Rubric`, and separately to `Edition`, both of
which have their own `course: Link[Course]`) started raising
`NotImplementedError: Although 'pipeline' is a valid lookup operator... not
implemented in Mongomock`. Root cause, in `beanie/odm/utils/find.py`'s
`construct_query`: when the *target* of a Link field has Link fields of its
own (`link_info.nested_links is not None`), Beanie unconditionally adds a
`"pipeline": [...]` key to that `$lookup` stage to resolve the nested link
too, at unlimited depth by default -- `mongomock`'s lookup handler rejects
the mere *presence* of a `pipeline` key outright, even an empty one, real
or not. This is unrelated to sharp edge #5 above (that one fails to
resolve silently; this one crashes), and depth-limiting alone doesn't
dodge it -- an empty `pipeline: []` still has the key present.

The fix has two parts, and you need **both**:
- Production code caps the depth on any field whose target has nested
  links, wherever it isn't needed: `api/routers/submissions.py`'s
  `_SHALLOW_LINKS = {"rubric": 1}`, passed as `nesting_depths_per_field` to
  every `Submission.find(...)`/`.get(...)` call (and the same in
  `worker/tasks.py`'s `_grade_submission`). Depth 1 resolves `rubric` itself
  fully but stops before touching *its* nested `course`/`edition` links --
  which no submission view here needs anyway, so this is a genuine (if
  minor) production improvement, not just a test workaround. If you add
  another Link field whose target itself has Links, and you fetch it
  through a third document, give it the same treatment.
- `tests/conftest.py` has a matching test-only shim (`mongomock.aggregate.
  _handle_lookup_stage`) that strips a `pipeline` key *only when it's an
  empty list* before delegating to mongomock's real handler -- it still
  raises for a genuinely non-empty pipeline (i.e. an actual attempt to
  resolve nested links at real depth), which mongomock truly can't do.
  Depth-limiting in production is what makes the pipeline mongomock sees
  empty in the first place; the shim alone wouldn't be enough.

**Don't "fix" this by removing a nested Link (`Rubric.course`,
`Rubric.edition`) or by avoiding `fetch_links` -- fix the depth, as above.**
If you add a *new* Link field whose target type has its own Links and hit
this again, the fix is the same shape: `nesting_depths_per_field={"<field>":
<depth that stops before the nested link>}` on every fetch of it.

**7. A resolved Link's embedded document serializes its id as `id`, not
`_id` -- only the outer, top-level response document gets the `_id` alias.**
Verified directly: `POST /submissions`' response has `_id` at the top level
(`response_model=Submission`, FastAPI applies the alias there), but its
nested `student` object -- a real `Student` instance assigned straight to
the Link field, not fetched via `$lookup` -- comes back as `{"id": ...,
"student_id": ..., "name": ..., ...}`. This isn't a `mongomock`-only quirk
(sharp edge #5 above): it's the same whether the nested document came from
a direct in-memory assignment (`create_submission`, `_match_student` in
`batches.py`) or a resolved `fetch_links` Link. Nothing in this app relied
on a nested id before `_match_student`/`set_submission_student` -- every
existing frontend page reads `.name`/`.title`/`.student_id`/... off a
nested `student`/`rubric`, never `.student._id`. If you need to compare or
key by a nested document's id, use a business key that's unambiguous either
way (`Student.student_id`, `Rubric.slug`) rather than `_id`/`id`.


## Frontend/backend contract

- `frontend/src/api/schema.ts` is generated from the backend's own OpenAPI
  schema via `openapi-typescript` -- see `frontend/README.md` for the exact
  command. It is **not** regenerated automatically; if you add/change an API
  route or a Pydantic response model, regenerate it or the frontend's types
  will silently drift from reality.
- `openapi-typescript` is deliberately **not** a `package.json`
  dependency -- it's invoked via `npx openapi-typescript@latest` at codegen
  time only. It has a peer dependency on TypeScript ^5.x that conflicts with
  this project's TypeScript ^6.x; since it's never imported at runtime or
  build time, keeping it out of `package.json` sidesteps that conflict
  entirely rather than forcing `--legacy-peer-deps`.
- `frontend/src/api/types.ts` hand-declares `Edition`, `Rubric`, and
  `Submission` interfaces (plus the narrower `SubmissionRubric`) that
  override the generated ones. Reason: Beanie's `Link[T]` Pydantic schema
  can't statically express "this is either an unresolved reference or a
  fully resolved document," so `openapi-typescript` emits every Link field
  (`Rubric.course`/`.edition`, `Submission.student`/`.rubric`/`.edition`) as
  a leaky union (`{id, collection} | {[key: string]: unknown}`).
  Editions/rubrics endpoints always call Mongo with `fetch_links=True` (or,
  for rubric-creation endpoints, return the in-memory object that was never
  unresolved to begin with -- see "Creating rubrics"), so at runtime these
  fields are always the full document *at whatever depth the backend
  fetched them* -- the hand-written types encode that actual guarantee, no
  more and no less. If you ever add an endpoint with different resolution
  behavior, don't assume an existing type still holds for it; give it its
  own.
- `Edition` carries no Link field (see "Data model" above),
  so the hand-declared `Edition` interface is just
  its plain shape (`_id`, `name`, `slug`, `created_at`).
  Omitting fields (rather than typing
  them as the leaky union) means accessing e.g. `submission.rubric.course`
  from TypeScript is a compile error instead of a silent runtime
  `undefined`. If a backend depth cap ever changes, update the matching
  type -- don't leave it lying about what's actually available.
- `frontend/src/api/types.ts`'s `IN_PROGRESS_STATUSES` constant is a
  hand-maintained subset of the backend's `SubmissionStatus` enum (used to
  decide whether `useSubmission` should keep polling). It's small and
  unlikely to change, but it *is* a second place that knows what "still
  running" means -- if you add a new in-progress status server-side
  (`documents/submission.py`'s `SubmissionStatus`), update this too.

## Grading internals (`src/aitana/grading/`)

See file docstrings for the mechanics:
- system prompt = question + context + rubric + expected points + grading scale
- human message = verbatim student answer

See "Pluggable grading scales" above for why that schema is
built per-call rather than a fixed `Grade` type).

`build_system_prompt` (`grading.py`) composes, in order: `# Question`
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
`lower_snake_case`. The **hostnames differ between Docker and local runs**:
`.env.example`'s defaults (`mongo`, `minio`, `redis`) are the docker-compose
service names, correct for the `api`/`worker` containers but wrong if you
run `uvicorn`/`celery` directly on the host -- override
`MONGO_URI`/`MINIO_ENDPOINT`/`REDIS_URL` to `localhost:<published-port>` in
that case, e.g. with `docker compose up -d mongo minio redis` (which
publishes 27017/9000/6379 on localhost):

```bash
export MONGO_URI=mongodb://localhost:27017
export MINIO_ENDPOINT=localhost:9000
export REDIS_URL=redis://localhost:6379/0
uv run uvicorn aitana.api.main:app --reload
```

**If you ever test `POST /submissions` (or anything else that calls a
Celery task's `.delay()`) against an isolated setup, that setup needs an
isolated Redis too**, not just Mongo/MinIO -- `.delay()` tries to connect
to `settings.redis_url`, which defaults to `redis://localhost:6379/0`.
Without an isolated Redis, the request either hangs (nothing listening) or,
worse, silently reaches whatever real Redis happens to be on that port (see
"Verifying manually" below). `docker run -d --rm --name aitana-scratch-redis
-p <port>:6379 redis:7-alpine` plus `REDIS_URL=redis://localhost:<port>/0`
covers it.

## Testing strategy

`uv run pytest` -- everything runs against `mongomock`/`mongomock-motor`
plus in-process fakes for MinIO (`monkeypatch` on `storage.upload_file`/
`download_to_path`) and Celery (`monkeypatch` on
`submissions.grade_submission.delay`), so the suite needs no live services
and runs in well under a second. `tests/conftest.py` carries three
mongomock-compatibility shims for this (list_collection_names kwargs,
awaitable aggregate, tolerating an empty `$lookup` pipeline -- see sharp
edges #2/#4/#6 respectively). See "Known-sharp-edges" above for what this
setup still can't exercise even with the shims (actual Link *resolution*
via `fetch_links`, sharp edge #5) and why that's an accepted, documented gap
rather than an oversight.

`tests/test_worker_tasks.py` covers `_grade_answers` (the per-question
grade-and-save loop extracted out of `_grade_submission`, see sharp edge #1)
directly against `mongomock`, with a fake chat model in the same style as
`tests/test_grading.py`'s `_FakeChatModel`/`_FakeStructuredModel` -- but note
its `with_structured_output()` must return an object sharing the *same*
underlying grade queue across calls (`grade_answer` calls
`with_structured_output()` fresh per question), not a fresh
`iter(grades)` each time, or every question grades identically instead of in
sequence. `_grade_submission` itself (the outer function: raw file download +
extraction + status transitions + error handling) is still only verified
manually end-to-end against real Mongo/MinIO/Redis -- it wasn't brought
under `mongomock` because it depends on `fetch_links` resolving
`submission.rubric` (sharp edge #5), which `mongomock` can't do regardless
of the sharp-edge-#6 depth-limiting (that fix only stops the crash; it
doesn't make mongomock actually resolve anything). If you touch the outer
function, consider giving it the same real-`mongod` treatment described in
sharp edge #5 rather than fighting `mongomock`.

`tests/test_courses_api.py`, `tests/test_editions_api.py`, and the
edition-assignment tests in `tests/test_students_api.py` follow the same
`client`-fixture pattern as everything else. The `course_id`/`edition_id`
submission filters, `Edition`/`Rubric` course-filtering-by-id, and the file
download endpoint all depend on `fetch_links`/Link-query machinery
`mongomock` can't fully exercise (sharp edges #3 and #5), so only their
"empty/not-found" paths are asserted under `mongomock`; the positive-match
cases (a course/edition filter actually returning the right submissions; a
downloaded file's bytes/content-type/filename; `Rubric.course`/`.edition` resolving
on a fresh fetch; a rubric's own fixed edition being picked up
automatically at submission-creation time) were verified manually against
an isolated real Mongo+MinIO(+Redis, for the submission-creation path --
see "Environment variables" above) (see the next section for why
"isolated"). Rubric/course/edition-*creation* endpoints are the
exception -- since they return the in-memory object rather than
re-fetching, `resp.json()["course"]["name"]` **is** asserted directly in
`tests/test_rubrics_api.py`, no real-Mongo verification needed for those.

## Verifying manually: never touch a stack you didn't start

**Do not start the stack** and **do not run commands against running containers**.

Unless explicitly told by the user, there might be running processes started
by the user, and best to not conflict with them. Only start the docker stack
if the user explicitly asks you to do so.
