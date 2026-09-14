import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCourses, useEditions, useRubrics, useStudents, useSubmissions } from '../api/hooks'
import StatusBadge from '../components/StatusBadge'
import { formatDate } from '../lib/date'

export default function SubmissionsPage() {
  const { data: students } = useStudents()
  const { data: rubrics } = useRubrics()
  const { data: courses } = useCourses()

  const [studentId, setStudentId] = useState('')
  const [rubricId, setRubricId] = useState('')
  const [courseId, setCourseId] = useState('')
  const [editionId, setEditionId] = useState('')

  // Editions are global (see Edition's doc comment in api/types.ts), so
  // course and edition are independent filters -- picking one no longer
  // needs to reset the other.
  const { data: editions } = useEditions()

  const {
    data: submissions,
    isLoading,
    error,
  } = useSubmissions({
    student_id: studentId || undefined,
    rubric_id: rubricId || undefined,
    course_id: courseId || undefined,
    edition_id: editionId || undefined,
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Submissions</h1>
          <p className="text-sm text-slate-500">Every graded (or in-progress) student deliverable.</p>
        </div>
        <Link to="/upload" className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">
          Upload submission
        </Link>
      </div>

      <div className="flex flex-wrap gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <label className="block text-xs font-medium text-slate-600">Student</label>
          <select
            value={studentId}
            onChange={(e) => setStudentId(e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">All students</option>
            {students?.map((s) => (
              <option key={s._id} value={s._id ?? ''}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Rubric</label>
          <select
            value={rubricId}
            onChange={(e) => setRubricId(e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">All rubrics</option>
            {rubrics?.map((rubric) => (
              <option key={rubric._id} value={rubric._id ?? ''}>
                {rubric.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Course</label>
          <select
            value={courseId}
            onChange={(e) => setCourseId(e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">All courses</option>
            {courses?.map((course) => (
              <option key={course._id} value={course._id ?? ''}>
                {course.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Edition</label>
          <select
            value={editionId}
            onChange={(e) => setEditionId(e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">All editions</option>
            {editions?.map((edition) => (
              <option key={edition._id} value={edition._id ?? ''}>
                {edition.name}
              </option>
            ))}
          </select>
        </div>
        {(studentId || rubricId || courseId || editionId) && (
          <button
            onClick={() => {
              setStudentId('')
              setRubricId('')
              setCourseId('')
              setEditionId('')
            }}
            className="mt-5 text-sm text-slate-500 hover:underline"
          >
            Clear filters
          </button>
        )}
      </div>

      {isLoading && <p className="text-sm text-slate-500">Loading...</p>}
      {error && <p className="text-sm text-red-600">Failed to load submissions.</p>}

      {submissions && (
        <table className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Student</th>
              <th className="px-4 py-2">Rubric</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Created</th>
            </tr>
          </thead>
          <tbody>
            {submissions.map((s) => (
              <tr key={s._id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-4 py-2">
                  <Link to={`/submissions/${s._id}`} className="text-slate-900 hover:underline">
                    {s.student.name}
                  </Link>
                </td>
                <td className="px-4 py-2">{s.rubric.title}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={s.status} />
                </td>
                <td className="px-4 py-2 text-slate-500">{formatDate(s.created_at)}</td>
              </tr>
            ))}
            {submissions.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-slate-400">
                  No submissions match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}
