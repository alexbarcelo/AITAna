import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useCourses, useEditions, useRubrics } from '../api/hooks'
import RubricForm from '../components/RubricForm'
import { formatDate } from '../lib/date'

const FILTER_KEYS = ['course_id', 'edition_id']

export default function RubricsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const courseId = searchParams.get('course_id') ?? ''
  const editionId = searchParams.get('edition_id') ?? ''
  const hasFilters = FILTER_KEYS.some((k) => searchParams.get(k))

  function setParam(key: string, value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  function clearFilters() {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        FILTER_KEYS.forEach((k) => next.delete(k))
        return next
      },
      { replace: true },
    )
  }

  const { data: courses } = useCourses()
  const { data: editions } = useEditions()
  const {
    data: rubrics,
    isLoading,
    error,
  } = useRubrics({ course_id: courseId || undefined, edition_id: editionId || undefined })
  const [showForm, setShowForm] = useState(false)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Rubrics</h1>
          <p className="text-sm text-slate-500">One per lab or exam -- questions, grading instructions, and expected points.</p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
        >
          {showForm ? 'Cancel' : 'New rubric'}
        </button>
      </div>

      {showForm && <RubricForm onDone={() => setShowForm(false)} />}

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <label className="block text-xs font-medium text-slate-600">Course</label>
          <select
            value={courseId}
            onChange={(e) => setParam('course_id', e.target.value)}
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
            onChange={(e) => setParam('edition_id', e.target.value)}
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
        {hasFilters && (
          <button onClick={clearFilters} className="mb-1.5 text-sm text-slate-500 hover:underline">
            Clear filters
          </button>
        )}
      </div>

      {isLoading && <p className="text-sm text-slate-500">Loading...</p>}
      {error && <p className="text-sm text-red-600">Failed to load rubrics.</p>}

      {rubrics && (
        <table className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Title</th>
              <th className="px-4 py-2">Course</th>
              <th className="px-4 py-2">Edition</th>
              <th className="px-4 py-2">Questions</th>
              <th className="px-4 py-2">Created</th>
              <th className="px-4 py-2">Last updated</th>
            </tr>
          </thead>
          <tbody>
            {rubrics.map((rubric) => (
              <tr key={rubric._id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-4 py-2">
                  <Link to={`/rubrics/${rubric._id}`} className="text-slate-900 hover:underline">
                    {rubric.title}
                  </Link>
                  <div className="font-mono text-xs text-slate-400">{rubric.slug}</div>
                </td>
                <td className="px-4 py-2 text-slate-500">{rubric.course.name}</td>
                <td className="px-4 py-2 text-slate-500">{rubric.edition ? rubric.edition.name : 'any (reusable)'}</td>
                <td className="px-4 py-2 text-slate-500">{rubric.questions.length}</td>
                <td className="px-4 py-2 text-slate-500">{formatDate(rubric.created_at)}</td>
                <td className="px-4 py-2 text-slate-500">{formatDate(rubric.updated_at)}</td>
              </tr>
            ))}
            {rubrics.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-slate-400">
                  {hasFilters ? 'No rubrics match these filters.' : 'No rubrics yet -- create one above.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}
