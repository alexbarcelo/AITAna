import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useRubrics } from '../api/hooks'
import NewRubricForm from '../components/NewRubricForm'
import { formatDate } from '../lib/date'

export default function RubricsPage() {
  const { data: rubrics, isLoading, error } = useRubrics()
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

      {showForm && <NewRubricForm onCreated={() => setShowForm(false)} />}

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
                  No rubrics yet -- create one above.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}
