import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCreateSubmission, useEditions, useRubrics, useStudents } from '../api/hooks'
import { FORMAT_FILE_INFO } from '../api/types'

export default function UploadSubmissionPage() {
  const { data: students } = useStudents()
  const { data: rubrics } = useRubrics()
  const createSubmission = useCreateSubmission()
  const navigate = useNavigate()

  const [studentId, setStudentId] = useState('')
  const [rubricId, setRubricId] = useState('')
  const [editionId, setEditionId] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const selectedRubric = rubrics?.find((r) => r._id === rubricId)
  // A rubric with a fixed edition (e.g. an exam) determines the edition
  // automatically; one without (e.g. a lab, reused across editions) needs
  // the professor to pick which edition this submission belongs to. Editions
  // are global, not scoped to the rubric's course -- any edition is a valid pick.
  const fixedEdition = selectedRubric?.edition ?? null
  const { data: editions } = useEditions()

  // The rubric dictates the expected file format (see Rubric.format), not
  // the uploader -- falls back to the PDF picker before a rubric is chosen.
  const fileInfo = FORMAT_FILE_INFO[selectedRubric?.format ?? 'pdf']

  const needsExplicitEdition = Boolean(selectedRubric) && !fixedEdition
  const canSubmit = Boolean(file && studentId && rubricId && (fixedEdition || editionId))

  function handleRubricChange(nextRubricId: string) {
    setRubricId(nextRubricId)
    setEditionId('') // a different rubric may have a different (or no) fixed edition
    setFile(null) // a different rubric may expect a different file format
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit || !file) return
    createSubmission.mutate(
      { file, studentId, rubricId, editionId: needsExplicitEdition ? editionId : undefined },
      {
        onSuccess: (submission) => navigate(`/submissions/${submission._id}`),
      },
    )
  }

  return (
    <div className="max-w-md space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Upload a submission</h1>
        <p className="text-sm text-slate-500">Grading runs in the background -- you'll land on a live status page.</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <label className="block text-xs font-medium text-slate-600">Student</label>
          <select
            required
            value={studentId}
            onChange={(e) => setStudentId(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="" disabled>
              Select a student
            </option>
            {students?.map((s) => (
              <option key={s._id} value={s._id ?? ''}>
                {s.name} ({s.student_id})
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">Rubric</label>
          <select
            required
            value={rubricId}
            onChange={(e) => handleRubricChange(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="" disabled>
              Select a rubric
            </option>
            {rubrics?.map((rubric) => (
              <option key={rubric._id} value={rubric._id ?? ''}>
                {rubric.title}
              </option>
            ))}
          </select>
        </div>

        {selectedRubric && (
          <div>
            <label className="block text-xs font-medium text-slate-600">Edition</label>
            {fixedEdition ? (
              <input
                disabled
                value={fixedEdition.name}
                className="mt-1 w-full rounded-md border border-slate-300 bg-slate-50 px-2 py-1.5 text-sm text-slate-500"
              />
            ) : (
              <select
                required
                value={editionId}
                onChange={(e) => setEditionId(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                <option value="" disabled>
                  This rubric is reusable -- pick which edition
                </option>
                {editions?.map((edition) => (
                  <option key={edition._id} value={edition._id ?? ''}>
                    {edition.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-slate-600">Answer sheet ({fileInfo.label})</label>
          <input
            required
            type="file"
            accept={fileInfo.accept}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 w-full text-sm"
          />
        </div>

        <button
          type="submit"
          disabled={!canSubmit || createSubmission.isPending}
          className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {createSubmission.isPending ? 'Uploading...' : 'Grade this submission'}
        </button>

        {createSubmission.isError && (
          <p className="text-sm text-red-600">Upload failed. Check the student/rubric/edition and try again.</p>
        )}
      </form>
    </div>
  )
}
