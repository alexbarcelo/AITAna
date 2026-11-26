import { useDeleteSubmission } from '../api/hooks'
import { IN_PROGRESS_STATUSES, submissionName } from '../api/types'
import type { Submission } from '../api/types'

/**
 * Deletes one submission (with its stored file and grading traces) after a
 * native `confirm()` naming it. An in-progress submission gets a stronger
 * warning and is deleted with `force` -- it may be a lost job that will
 * never finish (same reasoning as the regrade confirmation).
 *
 * `iconOnly` renders a small trash icon for table rows; otherwise a regular
 * button for page headers.
 */
export default function DeleteSubmissionButton({
  submission,
  iconOnly = false,
  onDeleted,
}: {
  submission: Submission
  iconOnly?: boolean
  onDeleted?: () => void
}) {
  const deleteSubmission = useDeleteSubmission()

  function handleClick() {
    const inProgress = IN_PROGRESS_STATUSES.includes(submission.status)
    const what = `the submission of "${submissionName(submission)}" for "${submission.rubric.title}"`
    const message = inProgress
      ? `Delete ${what}? It is still being processed (or stuck if its job was lost). This can't be undone.`
      : `Delete ${what}? Its file and grades will be removed. This can't be undone.`
    if (!window.confirm(message)) return
    deleteSubmission.mutate(
      { submissionId: submission.id, force: inProgress },
      {
        onSuccess: () => onDeleted?.(),
        onError: (err) => window.alert(err.message || 'Failed to delete this submission.'),
      },
    )
  }

  if (iconOnly) {
    return (
      <button
        type="button"
        onClick={handleClick}
        disabled={deleteSubmission.isPending}
        title="Delete submission"
        className="inline-flex items-center rounded p-0.5 text-slate-400 hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
      >
        <TrashIcon />
      </button>
    )
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={deleteSubmission.isPending}
      className="whitespace-nowrap rounded-md border border-red-200 bg-white px-4 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
    >
      {deleteSubmission.isPending ? 'Deleting...' : 'Delete'}
    </button>
  )
}

export function TrashIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
      <path
        d="M3.5 5.5h13M8 5.5V3.75h4V5.5M5.25 5.5l.75 10.75h8l.75-10.75M8.5 8.5v5M11.5 8.5v5"
        stroke="currentColor"
        strokeWidth="1.35"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
