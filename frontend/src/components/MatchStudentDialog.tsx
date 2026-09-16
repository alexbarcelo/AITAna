import { useState } from 'react'
import { useSetSubmissionStudent, useStudents } from '../api/hooks'

/**
 * Icon button + dialog for setting (or correcting) which student a
 * submission belongs to. Shown next to the student cell wherever a
 * submission is rendered -- most useful for a batch-created submission
 * whose folder name didn't auto-match anyone (see `_match_student` in
 * `api/routers/batches.py`), but works for any submission.
 */
export default function MatchStudentDialog({
  submissionId,
  currentStudentId,
}: {
  submissionId: string
  currentStudentId?: string
}) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Match to a student"
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
        <MatchStudentModal
          submissionId={submissionId}
          currentStudentId={currentStudentId}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}

function MatchStudentModal({
  submissionId,
  currentStudentId,
  onClose,
}: {
  submissionId: string
  currentStudentId?: string
  onClose: () => void
}) {
  const { data: students } = useStudents()
  const setStudent = useSetSubmissionStudent()
  const [search, setSearch] = useState('')

  const filtered = (students ?? []).filter((s) => {
    const q = search.trim().toLowerCase()
    if (!q) return true
    return s.name.toLowerCase().includes(q) || s.student_id.toLowerCase().includes(q)
  })

  function pick(studentId: string) {
    setStudent.mutate(
      { submissionId, studentId },
      {
        onSuccess: onClose,
      },
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="max-h-[80vh] w-full max-w-sm overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-200 p-4">
          <h2 className="text-sm font-medium text-slate-900">Match to a student</h2>
          <input
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or student ID..."
            className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
        </div>
        <div className="max-h-72 overflow-y-auto">
          {filtered.map((s) => (
            <button
              key={s._id}
              type="button"
              onClick={() => s._id && pick(s._id)}
              disabled={setStudent.isPending}
              className={`flex w-full items-center justify-between px-4 py-2 text-left text-sm hover:bg-slate-50 disabled:opacity-50 ${
                s.student_id === currentStudentId ? 'bg-slate-50 font-medium' : ''
              }`}
            >
              <span>{s.name}</span>
              <span className="font-mono text-xs text-slate-400">{s.student_id}</span>
            </button>
          ))}
          {filtered.length === 0 && <p className="px-4 py-6 text-center text-sm text-slate-400">No students found.</p>}
        </div>
        {setStudent.isError && <p className="px-4 py-2 text-sm text-red-600">Failed to set student.</p>}
        <div className="flex justify-end border-t border-slate-200 p-2">
          <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
