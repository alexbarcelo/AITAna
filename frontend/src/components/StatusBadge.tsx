import type { SubmissionStatus } from '../api/types'

const STYLES: Record<SubmissionStatus, string> = {
  pending: 'bg-slate-100 text-slate-700',
  extracting: 'bg-amber-100 text-amber-800',
  grading: 'bg-amber-100 text-amber-800',
  graded: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-800',
}

export default function StatusBadge({ status }: { status: SubmissionStatus }) {
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${STYLES[status]}`}>{status}</span>
  )
}
