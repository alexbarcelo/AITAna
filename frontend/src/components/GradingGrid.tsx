import { useState } from 'react'
import GradeBadge from './GradeBadge'
import { gradeColor, prettifyLevel } from '../lib/gradeColor'
import type { AnsweredQuestion, Question, Submission, SubmissionRubric } from '../api/types'

/**
 * Students (rows) x questions (columns) grading overview for a batch --
 * cells are colored by grade level (same ordinal ramp as `GradeBadge`, so
 * this reads consistently with every other grade indicator in the app), an
 * empty cell means not graded yet, and clicking a graded cell opens a
 * single-answer quick view (no prev/next -- close it and click another
 * cell instead).
 */
export default function GradingGrid({
  submissions,
  rubric,
}: {
  submissions: Submission[]
  rubric: SubmissionRubric
}) {
  const [selected, setSelected] = useState<{
    submission: Submission
    question: Question
    answer: AnsweredQuestion
  } | null>(null)

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
                  const step = answer?.grade ? gradeColor(answer.grade.level, rubric.grading_scale) : null
                  return (
                    <td key={q.id} className="p-1 text-center">
                      <button
                        type="button"
                        disabled={!answer?.grade}
                        onClick={() => answer?.grade && setSelected({ submission: s, question: q, answer })}
                        title={answer?.grade ? prettifyLevel(answer.grade.level) : 'Not graded yet'}
                        className="flex h-8 w-full min-w-20 items-center justify-center rounded disabled:cursor-default"
                        style={step ? { backgroundColor: step.hex } : undefined}
                      >
                        {!step && <span className="text-xs text-slate-300">—</span>}
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

      {selected && <AnswerQuickView {...selected} scale={rubric.grading_scale} onClose={() => setSelected(null)} />}
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
          {answer.grade && <GradeBadge level={answer.grade.level} scale={scale} />}
        </div>
        <p className="mb-3 whitespace-pre-wrap rounded bg-slate-50 p-3 text-sm text-slate-700">
          {answer.student_answer || <span className="text-slate-400">No answer provided.</span>}
        </p>
        {answer.grade && <p className="text-sm text-slate-600">{answer.grade.feedback}</p>}
        <div className="mt-4 flex justify-end">
          <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
