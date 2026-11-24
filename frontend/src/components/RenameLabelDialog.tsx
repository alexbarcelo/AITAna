import { useState } from 'react'
import { useSetSubmissionLabel } from '../api/hooks'

/**
 * Icon button + dialog for renaming a labelled submission's `label`
 * (e.g. fixing a typo in a group name). Same pencil icon and modal shell
 * as `MatchStudentDialog`, which it replaces on labelled submissions.
 */
export default function RenameLabelDialog({ submissionId, currentLabel }: { submissionId: string; currentLabel: string }) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Rename identifier"
        className="inline-flex items-center rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
      >
        <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
          <path
            d="M14.166 2.5a1.18 1.18 0 0 1 1.667 1.667l-8.75 8.75-2.5.833.833-2.5z"
            stroke="currentColor"
            strokeWidth="1.35"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open && (
        <RenameLabelModal submissionId={submissionId} currentLabel={currentLabel} onClose={() => setOpen(false)} />
      )}
    </>
  )
}

function RenameLabelModal({
  submissionId,
  currentLabel,
  onClose,
}: {
  submissionId: string
  currentLabel: string
  onClose: () => void
}) {
  const setLabel = useSetSubmissionLabel()
  const [label, setLabelText] = useState(currentLabel)
  const trimmed = label.trim()

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!trimmed) return
    if (trimmed === currentLabel) return onClose()
    setLabel.mutate({ submissionId, label: trimmed }, { onSuccess: onClose })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-4">
          <h2 className="text-sm font-medium text-slate-900">Rename identifier</h2>
          <input
            autoFocus
            value={label}
            onChange={(e) => setLabelText(e.target.value)}
            placeholder="e.g. Group 3"
            className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
          {setLabel.isError && <p className="mt-2 text-sm text-red-600">Failed to rename.</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-slate-200 p-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!trimmed || setLabel.isPending}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {setLabel.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  )
}
