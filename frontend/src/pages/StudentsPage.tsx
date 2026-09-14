import { useState } from 'react'
import { useCreateStudent, useEditions, useSetStudentEditions, useStudents } from '../api/hooks'
import type { Edition, Student } from '../api/types'

export default function StudentsPage() {
  const { data: students, isLoading, error } = useStudents()
  const { data: editions } = useEditions()
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

      {isLoading && <p className="text-sm text-slate-500">Loading...</p>}
      {error && <p className="text-sm text-red-600">Failed to load students.</p>}

      {students && (
        <table className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Student ID</th>
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Email</th>
              <th className="px-4 py-2">Editions</th>
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s._id} className="border-t border-slate-100">
                <td className="px-4 py-2 font-mono">{s.student_id}</td>
                <td className="px-4 py-2">{s.name}</td>
                <td className="px-4 py-2 text-slate-500">{s.email ?? '—'}</td>
                <td className="px-4 py-2">
                  <StudentEditions student={s} editions={editions ?? []} />
                </td>
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

function StudentEditions({ student, editions }: { student: Student; editions: Edition[] }) {
  const setStudentEditions = useSetStudentEditions()
  const enrolled = new Set(student.edition_ids ?? [])

  function toggle(editionId: string) {
    const next = enrolled.has(editionId)
      ? [...enrolled].filter((id) => id !== editionId)
      : [...enrolled, editionId]
    setStudentEditions.mutate({ studentId: student._id!, editionIds: next })
  }

  if (editions.length === 0) {
    return <span className="text-xs text-slate-400">No editions yet</span>
  }

  return (
    <div className="flex flex-wrap gap-1">
      {editions.map((edition) => {
        const isEnrolled = edition._id ? enrolled.has(edition._id) : false
        return (
          <button
            key={edition._id}
            onClick={() => edition._id && toggle(edition._id)}
            disabled={setStudentEditions.isPending}
            title={edition.name}
            className={`rounded-full px-2 py-0.5 text-xs font-medium disabled:opacity-50 ${
              isEnrolled ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
            }`}
          >
            {edition.name}
          </button>
        )
      })}
    </div>
  )
}
