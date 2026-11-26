import { useState } from 'react'
import { useCreateStudent, useDeleteStudent, useImportStudents, useStudents } from '../api/hooks'
import { TrashIcon } from '../components/DeleteSubmissionButton'
import { STUDENT_IMPORT_FORMAT_HINTS, STUDENT_IMPORT_FORMAT_LABELS } from '../api/types'
import type { StudentImportFormat } from '../api/types'

const IMPORT_FORMAT_OPTIONS = Object.keys(STUDENT_IMPORT_FORMAT_LABELS) as StudentImportFormat[]

export default function StudentsPage() {
  const { data: students, isLoading, error } = useStudents()
  const createStudent = useCreateStudent()
  const deleteStudent = useDeleteStudent()

  function handleDelete(student: { id: number; name: string; student_id: string }) {
    if (!window.confirm(`Delete student "${student.name}" (${student.student_id})? This can't be undone.`)) return
    deleteStudent.mutate(student.id)
  }

  const [studentId, setStudentId] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    createStudent.mutate(
      { student_id: studentId, name, email: email || undefined },
      {
        onSuccess: () => {
          setStudentId('')
          setName('')
          setEmail('')
        },
      },
    )
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Students</h1>
        <p className="text-sm text-slate-500">The roster corrections are linked against.</p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4"
      >
        <div>
          <label className="block text-xs font-medium text-slate-600">Student ID</label>
          <input
            required
            value={studentId}
            onChange={(e) => setStudentId(e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Name</label>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Email (optional)</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
        <button
          type="submit"
          disabled={createStudent.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add student
        </button>
        {createStudent.isError && <p className="w-full text-sm text-red-600">Failed to add student.</p>}
      </form>

      <ImportStudentsForm />

      {isLoading && <p className="text-sm text-slate-500">Loading...</p>}
      {error && <p className="text-sm text-red-600">Failed to load students.</p>}
      {deleteStudent.isError && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
          {deleteStudent.error.message || 'Failed to delete the student.'}
        </p>
      )}

      {students && (
        <table className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Student ID</th>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Username</th>
              <th className="px-4 py-2">Email</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s.id} className="border-t border-slate-100">
                <td className="px-4 py-2 font-mono">{s.student_id}</td>
                <td className="px-4 py-2">{s.name}</td>
                <td className="px-4 py-2 text-slate-500">{s.username ?? '—'}</td>
                <td className="px-4 py-2 text-slate-500">{s.email ?? '—'}</td>
                <td className="px-4 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => handleDelete(s)}
                    disabled={deleteStudent.isPending}
                    title="Delete student"
                    className="inline-flex items-center rounded p-0.5 text-slate-400 hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
                  >
                    <TrashIcon />
                  </button>
                </td>
              </tr>
            ))}
            {students.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-slate-400">
                  No students yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}

function ImportStudentsForm() {
  const importStudents = useImportStudents()
  const [file, setFile] = useState<File | null>(null)
  const [format, setFormat] = useState<StudentImportFormat>(IMPORT_FORMAT_OPTIONS[0])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    importStudents.mutate(
      { file, format },
      {
        onSuccess: () => setFile(null),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <div>
        <h2 className="text-sm font-medium text-slate-900">Batch import</h2>
        <p className="text-sm text-slate-500">
          Upload a roster file to create or update several students at once. An existing student (matched by student ID)
          has its name/username/email overwritten with the file's values.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-600">Format</label>
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as StudentImportFormat)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            {IMPORT_FORMAT_OPTIONS.map((f) => (
              <option key={f} value={f}>
                {STUDENT_IMPORT_FORMAT_LABELS[f]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">
            {format === 'learnsql' ? 'ODS or CSV file' : 'CSV file'}
          </label>
          <input
            required
            type="file"
            accept={
              format === 'learnsql'
                ? '.ods,.csv,application/vnd.oasis.opendocument.spreadsheet,text/csv'
                : '.csv,text/csv'
            }
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 text-sm"
          />
        </div>
        <button
          type="submit"
          disabled={importStudents.isPending || !file}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Import
        </button>
      </div>
      <p className="text-xs text-slate-500">{STUDENT_IMPORT_FORMAT_HINTS[format]}</p>
      {importStudents.isSuccess && (
        <p className="text-sm text-emerald-600">
          Imported successfully: {importStudents.data.created} created, {importStudents.data.updated} updated.
        </p>
      )}
      {importStudents.isError && (
        <p className="text-sm text-red-600">
          {(importStudents.error as { message?: string })?.message ?? 'Import failed.'}
        </p>
      )}
    </form>
  )
}
