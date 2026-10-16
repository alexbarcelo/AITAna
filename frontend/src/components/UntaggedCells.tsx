import { Link } from 'react-router-dom'
import type { Submission, SubmissionRubric } from '../api/types'
import { totalUntaggedCells } from '../lib/untaggedCells'

/**
 * `AnsweredQuestion.untagged_cells` is how many cells of an answer the
 * notebook extractor picked up heuristically -- cells a student added or
 * recreated below a question, without the template's `aitana:answer` tag
 * (see `grading/extraction/notebook.py`'s "Untagged cells"). Nothing is
 * wrong per se, but a TA should double-check that the right cells were
 * joined into the answer, so every view that shows answers flags it.
 */

export function UntaggedCellsBadge({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <span
      className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800"
      title="Cells without the answer tag were joined into this answer (the student added or recreated cells below the question) -- double-check them"
    >
      {count} untagged cell{count === 1 ? '' : 's'}
    </span>
  )
}

/** Per-submission summary: which questions used untagged cells, how many each, and the total. */
export function UntaggedCellsSummary({ submission }: { submission: Submission }) {
  const affected = submission.answers.filter((a) => a.untagged_cells > 0)
  if (affected.length === 0) return null
  const titles = new Map(submission.rubric.questions.map((q) => [q.id, q.title]))
  return (
    <div className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
      <p>
        <span className="font-medium">{totalUntaggedCells(submission.answers)} untagged cell(s)</span> were joined into{' '}
        {affected.length} answer(s) -- the student added or recreated cells outside the template's tagged ones:
      </p>
      <ul className="mt-1 list-inside list-disc">
        {affected.map((a) => (
          <li key={a.question_id}>
            {titles.get(a.question_id) ?? a.question_id}: {a.untagged_cells}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Batch/rubric-level report over every listed submission: per question, how
 * many submissions needed the heuristic and how many cells in total, plus
 * which submissions (and questions) are involved. Renders nothing when no
 * submission used an untagged cell.
 */
export function UntaggedCellsReport({ submissions, rubric }: { submissions: Submission[]; rubric: SubmissionRubric }) {
  const affected = submissions.filter((s) => totalUntaggedCells(s.answers) > 0)
  if (affected.length === 0) return null

  const titles = new Map(rubric.questions.map((q) => [q.id, q.title]))
  const perQuestion = rubric.questions
    .map((q) => {
      const counts = submissions.map((s) => s.answers.find((a) => a.question_id === q.id)?.untagged_cells ?? 0)
      return {
        question: q,
        submissions: counts.filter((c) => c > 0).length,
        cells: counts.reduce((sum, c) => sum + c, 0),
      }
    })
    .filter((row) => row.cells > 0)
  const total = perQuestion.reduce((sum, row) => sum + row.cells, 0)

  return (
    <div className="space-y-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm">
      <div>
        <h2 className="font-medium text-amber-900">Extraction report: untagged cells</h2>
        <p className="text-amber-800">
          <span className="font-medium">{total}</span> cell(s) without the answer tag were joined into answers, across{' '}
          <span className="font-medium">{affected.length}</span> of {submissions.length} submission(s) and{' '}
          <span className="font-medium">{perQuestion.length}</span> question(s). Students added or recreated cells
          below these questions -- worth double-checking.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <table className="w-full self-start rounded border border-amber-200 bg-white">
          <thead className="text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-1.5">Question</th>
              <th className="px-3 py-1.5 text-right">Submissions</th>
              <th className="px-3 py-1.5 text-right">Cells</th>
            </tr>
          </thead>
          <tbody>
            {perQuestion.map((row) => (
              <tr key={row.question.id} className="border-t border-slate-100">
                <td className="px-3 py-1.5">{row.question.title}</td>
                <td className="px-3 py-1.5 text-right">{row.submissions}</td>
                <td className="px-3 py-1.5 text-right">{row.cells}</td>
              </tr>
            ))}
            <tr className="border-t border-slate-200 font-medium">
              <td className="px-3 py-1.5">Total</td>
              <td className="px-3 py-1.5 text-right">{affected.length}</td>
              <td className="px-3 py-1.5 text-right">{total}</td>
            </tr>
          </tbody>
        </table>

        <table className="w-full self-start rounded border border-amber-200 bg-white">
          <thead className="text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-1.5">Submission</th>
              <th className="px-3 py-1.5">Questions (cells)</th>
            </tr>
          </thead>
          <tbody>
            {affected.map((s) => (
              <tr key={s._id} className="border-t border-slate-100">
                <td className="px-3 py-1.5">
                  <Link to={`/submissions/${s._id}`} className="hover:underline">
                    {s.student?.name ?? s.batch_internal_id ?? 'Unmatched'}
                  </Link>
                </td>
                <td className="px-3 py-1.5 text-slate-600">
                  {s.answers
                    .filter((a) => a.untagged_cells > 0)
                    .map((a) => `${titles.get(a.question_id) ?? a.question_id} (${a.untagged_cells})`)
                    .join(', ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
