import { useParams, Link } from 'react-router-dom'
import { useBatch, useRegradeBatch, useSubmissions } from '../api/hooks'
import GradingGrid from '../components/GradingGrid'
import MatchStudentDialog from '../components/MatchStudentDialog'
import StatusBadge from '../components/StatusBadge'
import { BATCH_TYPE_LABELS, IN_PROGRESS_STATUSES } from '../api/types'
import { formatDate } from '../lib/date'

export default function BatchDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: batch, isLoading, error } = useBatch(id)
  const { data: submissions } = useSubmissions({ batch_id: id })
  const regradeBatch = useRegradeBatch()

  if (isLoading) return <p className="text-sm text-slate-500">Loading...</p>
  if (error || !batch) return <p className="text-sm text-red-600">Failed to load this batch.</p>

  const anyInProgress = submissions?.some((s) => IN_PROGRESS_STATUSES.includes(s.status)) ?? false

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">
            {batch.rubric.title} &middot; {batch.edition.name}
          </h1>
          <p className="text-sm text-slate-500">
            {BATCH_TYPE_LABELS[batch.type]} &middot; {batch.item_count} item(s) &middot; uploaded{' '}
            {formatDate(batch.created_at)}
            {batch.original_filename && <> &middot; {batch.original_filename}</>}
          </p>
        </div>
        <button
          onClick={() => regradeBatch.mutate(batch._id)}
          disabled={!submissions?.length || anyInProgress || regradeBatch.isPending}
          title="Force re-grading every submission in this batch: re-extracts each file and discards existing grades/feedback"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 disabled:opacity-50"
        >
          {regradeBatch.isPending ? 'Re-grading...' : 'Regrade all'}
        </button>
      </div>

      {regradeBatch.isError && <p className="text-sm text-red-600">Failed to trigger re-grading.</p>}
      {regradeBatch.isSuccess && (
        <p className="text-sm text-emerald-600">Queued {regradeBatch.data.regraded} submission(s) for re-grading.</p>
      )}

      <table className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-4 py-2">Folder</th>
            <th className="px-4 py-2">Student</th>
            <th className="px-4 py-2">Status</th>
            <th className="px-4 py-2">Created</th>
          </tr>
        </thead>
        <tbody>
          {submissions?.map((s) => (
            <tr key={s._id} className="border-t border-slate-100 hover:bg-slate-50">
              <td className="px-4 py-2 font-mono text-xs">{s.batch_internal_id}</td>
              <td className="px-4 py-2">
                <div className="flex items-center gap-1">
                  <Link to={`/submissions/${s._id}`} className="text-slate-900 hover:underline">
                    {s.student ? s.student.name : <span className="text-slate-400">Unmatched</span>}
                  </Link>
                  <MatchStudentDialog submissionId={s._id} currentStudentId={s.student?.student_id} />
                </div>
              </td>
              <td className="px-4 py-2">
                <StatusBadge status={s.status} />
              </td>
              <td className="px-4 py-2 text-slate-500">{formatDate(s.created_at)}</td>
            </tr>
          ))}
          {submissions?.length === 0 && (
            <tr>
              <td colSpan={4} className="px-4 py-6 text-center text-slate-400">
                No submissions in this batch.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {submissions && <GradingGrid submissions={submissions} rubric={batch.rubric} />}
    </div>
  )
}
