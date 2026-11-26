import { useState } from 'react'
import AnswerGradeEditor from './AnswerGradeEditor'
import AnswerImages from './AnswerImages'
import GeneralFeedbackEditor from './GeneralFeedbackEditor'
import GradeBadge from './GradeBadge'
import QuestionDetails from './QuestionDetails'
import { GradeDistributionBar, GradingStatsSummary } from './GradeStats'
import { UntaggedCellsBadge } from './UntaggedCells'
import { gradeColor, prettifyLevel } from '../lib/gradeColor'
import { formatScore, gradeStats, type GradeStats } from '../lib/gradeStats'
import { orderedLevels } from '../lib/gradingScale'
import {
  effectiveGeneralFeedback,
  effectiveGrade,
  IN_PROGRESS_STATUSES,
  submissionName,
  type AnsweredQuestion,
  type Question,
  type Submission,
  type SubmissionRubric,
} from '../api/types'

/**
 * Students (rows) x questions (columns) grading overview for a batch --
 * cells are colored by grade level (same ordinal ramp as `GradeBadge`, so
 * this reads consistently with every other grade indicator in the app), an
 * empty cell means not graded yet, and clicking a cell opens a
 * single-answer quick view (no prev/next -- close it and click another
 * cell instead) where a TA can also override the grade (same
 * `AnswerGradeEditor` as the submission page). An ungraded cell is
 * clickable too once its submission is no longer being graded, so an
 * answer the LLM never graded (e.g. a failed run) can be graded by hand.
 *
 * Statistics ride along at three levels, all computed client-side from the
 * listed submissions (no stats endpoint): a summary panel above the grid
 * (`GradingStatsSummary`), a per-student "Score" column (sortable), and a
 * per-question footer row -- each with a grade-distribution bar and the
 * score: total over the best possible total (`gradeStats`, `lib/gradeStats.ts`).
 *
 * A submission with a general feedback gets an icon next to its name: hover
 * previews the text, click opens it in a dialog (same `GeneralFeedbackEditor`
 * as the submission page, so it can be edited/regenerated from here too).
 */
export default function GradingGrid({ submissions, rubric }: { submissions: Submission[]; rubric: SubmissionRubric }) {
  // Ids only, not the objects themselves: the modal re-reads the answer from
  // the live `submissions` prop on every render, so a manual-grade save
  // (which refetches the submissions query) shows up in the open modal
  // instead of it displaying a stale snapshot.
  const [selected, setSelected] = useState<{ submissionId: number; questionId: string } | null>(null)
  const [feedbackFor, setFeedbackFor] = useState<number | null>(null)
  const feedbackSubmission = feedbackFor !== null ? submissions.find((s) => s.id === feedbackFor) : undefined
  const [sortBy, setSortBy] = useState<'name' | 'score-desc' | 'score-asc'>('name')
  const selectedSubmission = selected && submissions.find((s) => s.id === selected.submissionId)
  const selectedQuestion = selected && rubric.questions.find((q) => q.id === selected.questionId)
  const selectedAnswer = selected && selectedSubmission?.answers.find((a) => a.question_id === selected.questionId)

  const scale = rubric.grading_scale
  const studentStats = new Map(submissions.map((s) => [s.id, gradeStats(s.answers, scale)]))
  const rowName = (s: Submission) => submissionName(s) ?? ''
  const rows = [...submissions].sort((a, b) => {
    if (sortBy !== 'name') {
      // Unscored rows always sink to the bottom, whichever direction.
      const sa = studentStats.get(a.id)?.meanScore ?? null
      const sb = studentStats.get(b.id)?.meanScore ?? null
      if (sa !== sb) {
        if (sa === null) return 1
        if (sb === null) return -1
        return sortBy === 'score-desc' ? sb - sa : sa - sb
      }
    }
    return rowName(a).localeCompare(rowName(b))
  })
  const questionStats = new Map(
    rubric.questions.map((q) => [
      q.id,
      gradeStats(
        submissions.flatMap((s) => s.answers.filter((a) => a.question_id === q.id)),
        scale,
      ),
    ]),
  )
  const nextSort = { name: 'score-desc', 'score-desc': 'score-asc', 'score-asc': 'name' } as const

  if (rows.length === 0 || rubric.questions.length === 0) return null

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-medium text-slate-900">Grading overview</h2>
      <GradingStatsSummary submissions={submissions} rubric={rubric} />
      <div className="max-h-[70vh] overflow-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-20 border-b border-r border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs font-medium uppercase text-slate-500">
                Student
              </th>
              <th className="sticky top-0 z-10 whitespace-nowrap border-b border-r border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs font-medium uppercase text-slate-500">
                <button
                  type="button"
                  onClick={() => setSortBy(nextSort[sortBy])}
                  className="uppercase hover:text-slate-900"
                  title="This student's total score over the best possible total for their graded answers. Click to sort."
                >
                  Score {sortBy === 'score-desc' ? '↓' : sortBy === 'score-asc' ? '↑' : ''}
                </button>
              </th>
              {rubric.questions.map((q) => (
                <th
                  key={q.id}
                  className="sticky top-0 z-10 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs font-medium uppercase text-slate-500"
                >
                  {q.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} className="border-b border-slate-100 last:border-0">
                <td className="sticky left-0 z-10 whitespace-nowrap border-r border-slate-200 bg-white px-3 py-2 font-medium text-slate-900">
                  <div className="flex items-center justify-between gap-2">
                    {s.student || s.label ? (
                      submissionName(s)
                    ) : (
                      <span className="text-slate-400">{s.batch_internal_id ?? 'Unmatched'}</span>
                    )}
                    <GeneralFeedbackButton submission={s} onOpen={() => setFeedbackFor(s.id)} />
                  </div>
                </td>
                <td className="border-r border-slate-200 px-3 py-2">
                  <ScoreCell stats={studentStats.get(s.id)!} scale={scale} />
                </td>
                {rubric.questions.map((q) => {
                  const answer = s.answers.find((a) => a.question_id === q.id)
                  const grade = answer ? effectiveGrade(answer) : null
                  const step = grade ? gradeColor(grade.level, rubric.grading_scale) : null
                  const clickable = Boolean(grade || (answer && !IN_PROGRESS_STATUSES.includes(s.status)))
                  const untagged = answer?.untagged_cells ?? 0
                  const untaggedNote = untagged > 0 ? ` -- ${untagged} untagged cell(s) joined into this answer` : ''
                  return (
                    <td key={q.id} className="p-1 text-center">
                      <button
                        type="button"
                        disabled={!clickable}
                        onClick={() => clickable && setSelected({ submissionId: s.id, questionId: q.id })}
                        title={
                          (grade
                            ? `${prettifyLevel(grade.level)}${answer?.manual_grade ? ' (edited by a TA)' : ''}`
                            : clickable
                              ? 'Not graded -- click to grade manually'
                              : 'Not graded yet') + untaggedNote
                        }
                        className="relative flex h-8 w-full min-w-20 items-center justify-center rounded disabled:cursor-default"
                        style={step ? { backgroundColor: step.hex } : undefined}
                      >
                        {!step && (
                          <span className={`text-xs ${clickable ? 'text-slate-400' : 'text-slate-300'}`}>—</span>
                        )}
                        {answer?.manual_grade && (
                          <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-white ring-1 ring-slate-500" />
                        )}
                        {untagged > 0 && (
                          <span className="absolute bottom-0.5 left-1 text-[10px] font-semibold leading-none text-amber-600">
                            +{untagged}
                          </span>
                        )}
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th className="sticky bottom-0 left-0 z-20 border-r border-t border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs font-medium uppercase text-slate-500">
                Per question
              </th>
              <td className="sticky bottom-0 z-10 border-r border-t border-slate-200 bg-slate-50" />
              {rubric.questions.map((q) => (
                <td key={q.id} className="sticky bottom-0 z-10 border-t border-slate-200 bg-slate-50 px-2 py-2">
                  <ScoreCell stats={questionStats.get(q.id)!} scale={scale} />
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>

      <GradeLegend scale={rubric.grading_scale} />

      {selectedSubmission && selectedQuestion && selectedAnswer && (
        <AnswerQuickView
          submission={selectedSubmission}
          question={selectedQuestion}
          answer={selectedAnswer}
          scale={rubric.grading_scale}
          rubricContext={rubric.context}
          onClose={() => setSelected(null)}
        />
      )}

      {feedbackSubmission && (
        <GeneralFeedbackDialog submission={feedbackSubmission} onClose={() => setFeedbackFor(null)} />
      )}
    </div>
  )
}

const FEEDBACK_PREVIEW_CHARS = 400

/** Speech-bubble icon, only when the submission has a general feedback:
 * the (native) tooltip previews it, a click opens the full dialog. A native
 * `title` rather than a styled popover, since the grid's scroll container
 * would clip one. */
function GeneralFeedbackButton({ submission, onOpen }: { submission: Submission; onOpen: () => void }) {
  const text = effectiveGeneralFeedback(submission)
  if (!text) return null
  const preview = text.length > FEEDBACK_PREVIEW_CHARS ? `${text.slice(0, FEEDBACK_PREVIEW_CHARS).trimEnd()}…` : text
  const edited = submission.manual_general_feedback ? ' (edited by a TA)' : ''
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`General feedback${edited} -- click to open:\n\n${preview}`}
      aria-label="Show general feedback"
      className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-blue-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
    >
      <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4" aria-hidden="true">
        <path
          fillRule="evenodd"
          d="M3.43 2.524A41.29 41.29 0 0 1 10 2c2.236 0 4.43.18 6.57.524 1.437.231 2.43 1.49 2.43 2.902v5.148c0 1.413-.993 2.67-2.43 2.902a41.202 41.202 0 0 1-5.183.501.78.78 0 0 0-.528.224l-3.579 3.58A.75.75 0 0 1 6 17.25v-3.443a41.033 41.033 0 0 1-2.57-.33C1.993 13.244 1 11.986 1 10.573V5.426c0-1.413.993-2.67 2.43-2.902Z"
          clipRule="evenodd"
        />
      </svg>
    </button>
  )
}

function GeneralFeedbackDialog({ submission, onClose }: { submission: Submission; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="mb-3 text-sm font-medium text-slate-900">{submissionName(submission)}</p>
        <GeneralFeedbackEditor submission={submission} disabled={IN_PROGRESS_STATUSES.includes(submission.status)} />
        <div className="flex justify-end">
          <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

/** Score + distribution bar -- one student's row, or one question's column. */
function ScoreCell({ stats, scale }: { stats: GradeStats; scale: SubmissionRubric['grading_scale'] }) {
  return (
    <div className="flex min-w-24 items-center gap-2">
      <span className="w-9 text-right text-xs font-medium tabular-nums text-slate-900">
        {formatScore(stats.meanScore)}
      </span>
      <div className="flex-1">
        <GradeDistributionBar stats={stats} scale={scale} className="h-2" />
      </div>
    </div>
  )
}

function GradeLegend({ scale }: { scale: SubmissionRubric['grading_scale'] }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
      <span className="font-medium text-slate-600">Legend:</span>
      {orderedLevels(scale).map((level) => (
        <span key={level.id} className="inline-flex items-center gap-1">
          <GradeBadge level={level.id} scale={scale} />
        </span>
      ))}
      <span className="inline-flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded bg-slate-50 ring-1 ring-inset ring-slate-200" />
        Not graded yet
      </span>
      <span className="inline-flex items-center gap-1">
        <span className="text-[10px] font-semibold text-amber-600">+N</span>
        Untagged cells joined into the answer
      </span>
    </div>
  )
}

function AnswerQuickView({
  submission,
  question,
  answer,
  scale,
  rubricContext,
  onClose,
}: {
  submission: Submission
  question: Question
  answer: AnsweredQuestion
  scale: SubmissionRubric['grading_scale']
  rubricContext?: string | null
  onClose: () => void
}) {
  const grade = effectiveGrade(answer)
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h2 className="font-medium text-slate-900">{question.title}</h2>
            <p className="text-xs text-slate-500">{submissionName(submission)}</p>
          </div>
          <div className="flex items-center gap-2">
            <UntaggedCellsBadge count={answer.untagged_cells} />
            {answer.manual_grade && (
              <span className="text-xs text-slate-500" title="Grade set manually by a TA">
                edited
              </span>
            )}
            {grade && <GradeBadge level={grade.level} scale={scale} />}
          </div>
        </div>
        <p className="mb-2 whitespace-pre-wrap text-sm text-slate-700">{question.question}</p>
        <QuestionDetails question={question} rubricContext={rubricContext} />
        <p className="mb-1 text-xs font-medium uppercase text-slate-400">Answer</p>
        <p className="mb-3 whitespace-pre-wrap rounded bg-slate-50 p-3 text-sm text-slate-700">
          {answer.student_answer || <span className="text-slate-400">No answer provided.</span>}
        </p>
        <AnswerImages submissionId={submission.id} answer={answer} />
        <AnswerGradeEditor
          submissionId={submission.id}
          answer={answer}
          scale={scale}
          disabled={IN_PROGRESS_STATUSES.includes(submission.status)}
        />
        <div className="mt-4 flex justify-end">
          <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
