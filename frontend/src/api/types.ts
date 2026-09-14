import type { components } from './schema'

export type Student = components['schemas']['Student']
export type Question = components['schemas']['Question']
export type Grade = components['schemas']['Grade']
export type AnsweredQuestion = components['schemas']['AnsweredQuestion']
export type SubmissionStatus = components['schemas']['SubmissionStatus']
export type SubmissionFormat = components['schemas']['SubmissionFormat']

/**
 * `{level_id: description}`, worst -> best -- see `Rubric.grading_scale`'s
 * docstring (`documents/rubric.py`) for why order matters (it's not just a
 * display detail: `GradeBadge` colors by position, and the backend's
 * blank-answer short-circuit picks the first key). `Grade.level` (and thus
 * `AnsweredQuestion.grade.level`) is a plain `string`, not a fixed union --
 * which values are valid depends on which rubric produced the grade.
 */
export type GradingScale = Record<string, string>

/**
 * Per-format file-picker metadata, kept next to the backend's own
 * `FORMAT_FILE_INFO` (`grading/extraction/__init__.py`) so the two don't
 * drift -- update both together if a format's extension/label ever changes.
 */
export const FORMAT_FILE_INFO: Record<SubmissionFormat, { extension: string; accept: string; label: string }> = {
  pdf: { extension: '.pdf', accept: 'application/pdf', label: 'PDF' },
  notebook: { extension: '.ipynb', accept: '.ipynb,application/x-ipynb+json', label: 'Jupyter notebook (.ipynb)' },
}

export type Course = components['schemas']['Course']

/**
 * Global -- a term like "2026/27", shared across every course taught that
 * term. Deliberately has no `course` field: an edition used to belong to
 * exactly one course, which meant the same term name had to be recreated
 * once per course and then looked like unrelated, identically-named
 * editions everywhere in the UI. Which course(s) an edition actually
 * applies to is derived, not stored -- `EditionsPage.tsx`/`CoursesPage.tsx`
 * both compute it by scanning `Rubric.course`/`.edition` (a rubric is the
 * one place a course and an edition are actually tied together).
 */
export interface Edition {
  _id: string
  name: string
  slug: string
  created_at: string
}

export interface Rubric {
  _id: string
  slug: string
  title: string
  course: Course
  edition: Edition | null
  format: SubmissionFormat
  grading_scale: GradingScale
  questions: Question[]
  created_at: string
  updated_at: string
}

/**
 * `Submission.rubric` is narrower than the standalone `Rubric` above: the
 * backend caps its fetch_links depth on this field to 1 (`_SHALLOW_LINKS`
 * in api/routers/submissions.py) since submission views never use
 * rubric.course/rubric.edition, only rubric.slug/title/questions.
 */
export type SubmissionRubric = Omit<Rubric, 'course' | 'edition'>

/**
 * The generated `Submission.student`/`.rubric`/`.edition` types are a leaky
 * union in the raw OpenAPI schema -- Beanie's `Link[T]` schema can't
 * statically express "this is either an unresolved reference or a fully
 * resolved document." Every submissions endpoint used here calls Mongo with
 * `fetch_links=True`, so at runtime these are always full embedded
 * documents (modulo the narrower `SubmissionRubric` shape noted above) --
 * this type reflects that actual shape.
 */
export interface Submission {
  _id: string
  student: Student
  rubric: SubmissionRubric
  edition: Edition
  file_object_key: string
  status: SubmissionStatus
  answers: AnsweredQuestion[]
  error?: string | null
  created_at: string
  updated_at: string
}

export const IN_PROGRESS_STATUSES: SubmissionStatus[] = ['pending', 'extracting', 'grading']
