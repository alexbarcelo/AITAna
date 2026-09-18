import { useState } from 'react'
import { useCreateStudent, useImportStudents, useStudents } from '../api/hooks'
import { STUDENT_IMPORT_FORMAT_LABELS } from '../api/types'
import type { StudentImportFormat } from '../api/types'

const IMPORT_FORMAT_OPTIONS = Object.keys(STUDENT_IMPORT_FORMAT_LABELS) as StudentImportFormat[]

export default function StudentsPage() {
  const { data: students, isLoading, error } = useStudents()
  const createStudent = useCreateStudent()

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

      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
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

      {students && (
        <table className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Student ID</th>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Username</th>
              <th className="px-4 py-2">Email</th>
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s._id} className="border-t border-slate-100">
                <td className="px-4 py-2 font-mono">{s.student_id}</td>
                <td className="px-4 py-2">{s.name}</td>
                <td className="px-4 py-2 text-slate-500">{s.username ?? '—'}</td>
                <td className="px-4 py-2 text-slate-500">{s.email ?? '—'}</td>
              </tr>
            ))}
            {students.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-slate-400">
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
          Upload a roster CSV to create or update several students at once. ID number and First name are required
          columns. An existing student (matched by ID number) has its name/username/email overwritten with
          the file's values.
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
          <label className="block text-xs font-medium text-slate-600">CSV file</label>
          <input
            required
            type="file"
            accept=".csv,text/csv"
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

