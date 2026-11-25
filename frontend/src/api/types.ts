import type { components } from './schema'

export type Student = components['schemas']['StudentOut']
export type Question = components['schemas']['Question']
export type Grade = components['schemas']['Grade']
export type AnsweredQuestion = components['schemas']['AnsweredQuestion']
/** Generic documents: where one question's answer lives, as 1-based inclusive
 * Markdown line numbers (`grading/models.py`'s `AnswerSegment`).
 * `start_line: null` = not answered; `end_line: null` = until the next
 * segment starts (or the end of the document). */
export type AnswerSegment = components['schemas']['AnswerSegment']
export type ConvertedDocument = components['schemas']['ConvertedDocument']
/** `GET /submissions/{id}/document` -- the converted Markdown, for the
 * segmentation editor. */
export type SubmissionDocument = components['schemas']['DocumentView']

/**
 * What the student should see for an answer: the TA's manual override if
 * any, else the LLM's grade. Mirrors the backend's
 * `AnsweredQuestion.effective_grade` (`db/models.py`) -- the LLM's own
 * `grade` is never edited in place, so both stay available.
 */
export function effectiveGrade(answer: AnsweredQuestion): Grade | null {
  return answer.manual_grade ?? answer.grade ?? null
}
export type SubmissionStatus = components['schemas']['SubmissionStatus']
export type SubmissionFormat = components['schemas']['SubmissionFormat']
export type StudentImportFormat = components['schemas']['StudentImportFormat']
export type StudentImportResult = components['schemas']['StudentImportResult']
export type BatchType = components['schemas']['BatchType']
/** A registered grading model, with this user's favorite flag and whether
 * its provider is usable right now (see api/routers/llm.py). */
export type LlmModel = components['schemas']['LlmModelView']
export type LlmProvider = components['schemas']['LlmProvider']
export type ReasoningEffort = NonNullable<LlmModel['reasoning_effort']>
/** Mirrors the backend's `ReasoningEffort` (`grading/llm.py`); `null` (no
 * entry here) leaves the provider's own default. */
export const REASONING_EFFORTS: ReasoningEffort[] = ['low', 'medium', 'high']

/**
 * One level of a rubric's grading scale: `id` (what a `Grade.level` holds),
 * the `description` the LLM grades against, and a numeric `score`.
 */
export type GradingLevel = components['schemas']['GradingLevel']
/**
 * A rubric's levels. Exactly one level scores 0 -- the one a blank answer
 * gets -- and levels are ranked by score (lowest = worst), never by list
 * position: see `lib/gradingScale.ts`. `Grade.level` is a plain `string`,
 * not a fixed union -- which values are valid depends on the rubric.
 */
export type GradingScale = GradingLevel[]

/**
 * Per-format file-picker metadata, kept next to the backend's own
 * `FORMAT_FILE_INFO` (`grading/extraction/__init__.py`) so the two don't
 * drift -- update both together if a format's extension/label ever changes.
 * `label` names the format (rubric form), `uploadLabel` what the upload
 * accepts (the backend's `accepted_upload_extensions`: a generic document
 * may also be a .docx, converted to PDF on upload), `fileLabel` what's
 * stored and downloaded (always the format's own extension).
 */
export const FORMAT_FILE_INFO: Record<
  SubmissionFormat,
  { extension: string; accept: string; label: string; uploadLabel: string; fileLabel: string }
> = {
  pdf_form: { extension: '.pdf', accept: 'application/pdf', label: 'PDF form', uploadLabel: 'PDF', fileLabel: 'PDF' },
  notebook: {
    extension: '.ipynb',
    accept: '.ipynb,application/x-ipynb+json',
    label: 'Jupyter notebook (.ipynb)',
    uploadLabel: 'Jupyter notebook (.ipynb)',
    fileLabel: 'notebook',
  },
  generic_document: {
    extension: '.pdf',
    accept: '.pdf,application/pdf,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    label: 'Generic document',
    uploadLabel: 'PDF or Word .docx',
    fileLabel: 'PDF',
  },
}

/** Mirrors the backend's `SEGMENTED_FORMATS` (`grading/extraction/__init__.py`):
 * formats converted to Markdown and split into answers by a professor
 * before grading. */
export const SEGMENTED_FORMATS: SubmissionFormat[] = ['generic_document']
/** Mirrors the backend's `IMAGE_FORMATS` / `OUTPUT_FORMATS`: which formats
 * `Question.include_images` / `.include_outputs` are allowed for. */
export const IMAGE_FORMATS: SubmissionFormat[] = ['notebook', 'generic_document']
export const OUTPUT_FORMATS: SubmissionFormat[] = ['notebook']

/**
 * Human labels for `StudentImportFormat`, kept next to the backend's own
 * enum (`api/routers/students.py`'s `StudentImportFormat`) so the two don't
 * drift -- add a label here whenever a new import format is registered
 * there.
 */
export const STUDENT_IMPORT_FORMAT_LABELS: Record<StudentImportFormat, string> = {
  atenea: 'Atenea export',
}

/**
 * Human labels for `BatchType`, kept next to the backend's own enum
 * (`db/models.py`'s `BatchType`) so the two don't drift -- add a label here
 * whenever a new batch zip layout is registered there.
 */
export const BATCH_TYPE_LABELS: Record<BatchType, string> = {
  atenea: 'Atenea (per-assignment submissions export)',
}

export type Course = components['schemas']['CourseOut']

/**
 * Global -- a term like "2026/27", shared across every course taught that
 * term. Which course(s) an edition applies to is derived, not stored --
 * `EditionsPage.tsx`/`CoursesPage.tsx` compute it from rubrics (a rubric is
 * the one place a course and an edition are tied together).
 */
export type Edition = components['schemas']['EditionOut']

/** A rubric with its course and edition. */
export type Rubric = components['schemas']['RubricOut']

/** A rubric nested in a submission or batch: course/edition by id only. */
export type SubmissionRubric = components['schemas']['RubricSummaryOut']

/**
 * One zip upload (`api/routers/batches.py`) -- see AGENTS.md's "Batch
 * submission import" for the zip layout this produces submissions from.
 */
export type Batch = components['schemas']['BatchOut']

/**
 * `student` is null for a labelled submission or a batch item not matched
 * to a roster student yet; `batch_id` is null unless it came from a batch.
 */
export type Submission = components['schemas']['SubmissionOut']

/** Who/what a submission is, for display -- same fallback chain as the
 * backend's `Submission.display_name`: student, label, batch
 * folder name. `null` when none applies (shouldn't happen in practice). */
export function submissionName(s: Pick<Submission, 'student' | 'label' | 'batch_internal_id'>): string | null {
  return s.student?.name ?? s.label ?? s.batch_internal_id ?? null
}

/** Response of `POST /batches` -- mirrors `StudentImportResult`'s shape. */
export type BatchUploadResult = components['schemas']['BatchUploadResult']

/** Response of `POST /submissions/regrade` (and `POST /batches/{batch_id}/regrade`). */
export type RegradeResult = components['schemas']['BulkRegradeResult']

export const IN_PROGRESS_STATUSES: SubmissionStatus[] = ['pending', 'converting', 'segmenting', 'extracting', 'grading']
