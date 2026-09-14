import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCreateEdition, useEditions, useRubrics } from '../api/hooks'
import { formatDate } from '../lib/date'
import { coursesForEdition } from '../lib/editions'

export default function EditionsPage() {
  const { data: editions, isLoading, error } = useEditions()
  const createEdition = useCreateEdition()
  const [expanded, setExpanded] = useState<string | null>(null)

  const [name, setName] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    createEdition.mutate({ name }, { onSuccess: () => setName('') })
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Editions</h1>
        <p className="text-sm text-slate-500">
          An edition is a term, e.g. "2026/27" -- global, shared by every course taught that term, not owned by any
          one of them. A single "2026/27" here can back a BDM rubric and an ML rubric alike; you don't need to
          recreate it per course.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <label className="block text-xs font-medium text-slate-600">Edition name</label>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="2026/27"
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
        <button
          type="submit"
          disabled={createEdition.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add edition
        </button>
        {createEdition.isError && <p className="w-full text-sm text-red-600">Failed to add edition.</p>}
      </form>

      {isLoading && <p className="text-sm text-slate-500">Loading...</p>}
      {error && <p className="text-sm text-red-600">Failed to load editions.</p>}

      <div className="space-y-3">
        {editions?.map((edition) => (
          <div key={edition._id} className="rounded-lg border border-slate-200 bg-white p-4">
            <button
              className="flex w-full items-center justify-between text-left"
              onClick={() => setExpanded(expanded === edition._id ? null : (edition._id ?? null))}
            >
              <div>
                <div className="font-medium">{edition.name}</div>
                <div className="font-mono text-xs text-slate-400">{edition.slug}</div>
              </div>
              <div className="flex items-center gap-3 text-xs text-slate-400">
                <span>Created {formatDate(edition.created_at)}</span>
                <span>{expanded === edition._id ? '▲' : '▼'}</span>
              </div>
            </button>

            {expanded === edition._id && edition._id && <EditionCourses editionId={edition._id} />}
          </div>
        ))}
        {editions && editions.length === 0 && <p className="text-slate-400">No editions yet -- add one above.</p>}
      </div>
    </div>
  )
}

/** Which courses currently use this edition -- derived, not stored: an
 * edition itself carries no course link (see Edition's doc comment in
 * api/types.ts), so "used by" means "at least one rubric pins this course
 * and this edition together." */
function EditionCourses({ editionId }: { editionId: string }) {
  const { data: rubrics } = useRubrics()
  const usingRubrics = rubrics?.filter((r) => r.edition?._id === editionId) ?? []
  const courses = rubrics ? coursesForEdition(rubrics, editionId) : []

  return (
    <div className="mt-4 border-t border-slate-100 pt-4">
      <p className="mb-2 text-xs font-medium uppercase text-slate-400">Used by</p>
      {courses.length === 0 ? (
        <p className="text-sm text-slate-400">No rubric uses this edition yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {courses.map((course) => (
            <li key={course._id}>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                {course.name}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-slate-400">
        Via {usingRubrics.length} rubric{usingRubrics.length === 1 ? '' : 's'} -- see{' '}
        <Link to="/rubrics" className="underline">
          Rubrics
        </Link>{' '}
        for details.
      </p>
    </div>
  )
}
