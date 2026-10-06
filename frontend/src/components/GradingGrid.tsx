import { useState } from 'react'
import AnswerGradeEditor from './AnswerGradeEditor'
import GradeBadge from './GradeBadge'
import { gradeColor, prettifyLevel } from '../lib/gradeColor'
import { effectiveGrade, IN_PROGRESS_STATUSES, type AnsweredQuestion, type Question, type Submission, type SubmissionRubric } from '../api/types'

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
 */
export default function GradingGrid({
  submissions,
  rubric,
}: {
  submissions: Submission[]
  rubric: SubmissionRubric
}) {
  // Ids only, not the objects themselves: the modal re-reads the answer from
  // the live `submissions` prop on every render, so a manual-grade save
  // (which refetches the submissions query) shows up in the open modal
  // instead of it displaying a stale snapshot.
  const [selected, setSelected] = useState<{ submissionId: string; questionId: string } | null>(null)
  const selectedSubmission = selected && submissions.find((s) => s._id === selected.submissionId)
  const selectedQuestion = selected && rubric.questions.find((q) => q.id === selected.questionId)
  const selectedAnswer = selected && selectedSubmission?.answers.find((a) => a.question_id === selected.questionId)

  const rows = [...submissions].sort((a, b) =>
    (a.student?.name ?? a.batch_internal_id ?? '').localeCompare(b.student?.name ?? b.batch_internal_id ?? ''),
  )

  if (rows.length === 0 || rubric.questions.length === 0) return null

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-medium text-slate-900">Grading overview</h2>
      <div className="max-h-[70vh] overflow-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-20 border-b border-r border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs font-medium uppercase text-slate-500">
                Student
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
              <tr key={s._id} className="border-b border-slate-100 last:border-0">
                <td className="sticky left-0 z-10 whitespace-nowrap border-r border-slate-200 bg-white px-3 py-2 font-medium text-slate-900">
                  {s.student ? s.student.name : <span className="text-slate-400">{s.batch_internal_id ?? 'Unmatched'}</span>}
                </td>
                {rubric.questions.map((q) => {
                  const answer = s.answers.find((a) => a.question_id === q.id)
                  const grade = answer ? effectiveGrade(answer) : null
                  const step = grade ? gradeColor(grade.level, rubric.grading_scale) : null
                  const clickable = Boolean(grade || (answer && !IN_PROGRESS_STATUSES.includes(s.status)))
                  return (
                    <td key={q.id} className="p-1 text-center">
                      <button
                        type="button"
                        disabled={!clickable}
                        onClick={() => clickable && setSelected({ submissionId: s._id, questionId: q.id })}
                        title={
                          grade
                            ? `${prettifyLevel(grade.level)}${answer?.manual_grade ? ' (edited by a TA)' : ''}`
                            : clickable
                              ? 'Not graded -- click to grade manually'
                              : 'Not graded yet'
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
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <GradeLegend scale={rubric.grading_scale} />

      {selectedSubmission && selectedQuestion && selectedAnswer && (
        <AnswerQuickView
          submission={selectedSubmission}
          question={selectedQuestion}
          answer={selectedAnswer}
          scale={rubric.grading_scale}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

function GradeLegend({ scale }: { scale: SubmissionRubric['grading_scale'] }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
      <span className="font-medium text-slate-600">Legend:</span>
      {Object.keys(scale).map((level) => (
        <span key={level} className="inline-flex items-center gap-1">
          <GradeBadge level={level} scale={scale} />
        </span>
      ))}
      <span className="inline-flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded bg-slate-50 ring-1 ring-inset ring-slate-200" />
        Not graded yet
      </span>
    </div>
  )
}

function AnswerQuickView({
  submission,
  question,
  answer,
  scale,
  onClose,
}: {
  submission: Submission
  question: Question
  answer: AnsweredQuestion
  scale: SubmissionRubric['grading_scale']
  onClose: () => void
}) {
  const grade = effectiveGrade(answer)
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="max-h-[80vh] w-full max-w-lg overflow-y-auto rounded-lg bg-white p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h2 className="font-medium text-slate-900">{question.title}</h2>
            <p className="text-xs text-slate-500">{submission.student ? submission.student.name : submission.batch_internal_id}</p>
          </div>
          <div className="flex items-center gap-2">
            {answer.manual_grade && (
              <span className="text-xs text-slate-500" title="Grade set manually by a TA">
                edited
              </span>
            )}
            {grade && <GradeBadge level={grade.level} scale={scale} />}
          </div>
        </div>
        <p className="mb-3 whitespace-pre-wrap rounded bg-slate-50 p-3 text-sm text-slate-700">
          {answer.student_answer || <span className="text-slate-400">No answer provided.</span>}
        </p>
        <AnswerGradeEditor
          submissionId={submission._id}
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
