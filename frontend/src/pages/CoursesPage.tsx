import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCourses, useCreateCourse, useRubrics } from '../api/hooks'
import { editionsForCourse } from '../lib/editions'

export default function CoursesPage() {
  const { data: courses, isLoading, error } = useCourses()
  const createCourse = useCreateCourse()
  const [expanded, setExpanded] = useState<string | null>(null)

  const [name, setName] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    createCourse.mutate({ name }, { onSuccess: () => setName('') })
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Courses</h1>
        <p className="text-sm text-slate-500">
          A course is a subject, e.g. "BDM". Editions (terms like "2026/27") are global and shared across courses --
          manage them on the <Link to="/editions" className="underline">Editions</Link> page. Students enroll in
          editions, and rubrics (labs, exams) belong to a course and optionally pin one edition.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <label className="block text-xs font-medium text-slate-600">Course name</label>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="BDM"
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
        <button
          type="submit"
          disabled={createCourse.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Add course
        </button>
        {createCourse.isError && <p className="w-full text-sm text-red-600">Failed to add course.</p>}
      </form>

      {isLoading && <p className="text-sm text-slate-500">Loading...</p>}
      {error && <p className="text-sm text-red-600">Failed to load courses.</p>}

      <div className="space-y-3">
        {courses?.map((course) => (
          <div key={course._id} className="rounded-lg border border-slate-200 bg-white p-4">
            <button
              className="flex w-full items-center justify-between text-left"
              onClick={() => setExpanded(expanded === course._id ? null : (course._id ?? null))}
            >
              <div>
                <div className="font-medium">{course.name}</div>
                <div className="font-mono text-xs text-slate-400">{course.slug}</div>
              </div>
              <span className="text-slate-400">{expanded === course._id ? '▲' : '▼'}</span>
            </button>

            {expanded === course._id && course._id && <CourseEditions courseId={course._id} />}
          </div>
        ))}
        {courses && courses.length === 0 && <p className="text-slate-400">No courses yet -- add one above.</p>}
      </div>
    </div>
  )
}

/** Read-only: which editions this course's rubrics actually use, derived
 * the same way as EditionsPage.tsx's mirror-image view (a rubric is the
 * only place a course and an edition are tied together -- see Edition's doc
 * comment in api/types.ts). Editions themselves are created on the Editions
 * page, not here -- there's no "add edition to this course" action anymore. */
function CourseEditions({ courseId }: { courseId: string }) {
  const { data: rubrics } = useRubrics()
  const editions = rubrics ? editionsForCourse(rubrics, courseId) : []

  return (
    <div className="mt-4 border-t border-slate-100 pt-4">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-medium uppercase text-slate-400">Editions in use</p>
        <Link to={`/rubrics?course_id=${courseId}`} className="text-xs text-slate-500 underline">
          See rubrics
        </Link>
      </div>
      {editions.length === 0 ? (
        <p className="text-sm text-slate-400">
          No rubric of this course pins a specific edition yet --{' '}
          <Link to="/editions" className="underline">
            add one
          </Link>{' '}
          and set it when creating or viewing a rubric.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {editions.map((edition) => (
            <li key={edition._id}>
              <Link
                to={`/rubrics?course_id=${courseId}&edition_id=${edition._id}`}
                className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 hover:bg-slate-200"
              >
                {edition.name}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
