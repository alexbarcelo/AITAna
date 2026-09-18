import { Link, useSearchParams } from 'react-router-dom'
import { useBatch, useBatches, useCourses, useEditions, useRegradeBatch, useRubrics, useStudents, useSubmissions } from '../api/hooks'
import GradingGrid from '../components/GradingGrid'
import MatchStudentDialog from '../components/MatchStudentDialog'
import StatusBadge from '../components/StatusBadge'
import { BATCH_TYPE_LABELS, IN_PROGRESS_STATUSES } from '../api/types'
import { formatDateTime } from '../lib/date'

const FILTER_KEYS = ['student_id', 'rubric_id', 'course_id', 'edition_id', 'batch_id']

export default function SubmissionsPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const studentId = searchParams.get('student_id') ?? ''
  const rubricId = searchParams.get('rubric_id') ?? ''
  const courseId = searchParams.get('course_id') ?? ''
  const editionId = searchParams.get('edition_id') ?? ''
  const batchId = searchParams.get('batch_id') ?? ''

  // A batch has exactly one rubric, so filtering by batch also pins the
  // rubric -- no point offering a separate (redundant, and potentially
  // contradictory) rubric selector once a batch is chosen.
  const rubricImpliedByBatch = Boolean(batchId)
  // Whenever the current filters can only ever match one rubric, the Rubric
  // column and the grading overview (which is keyed by a single rubric's
  // questions) become meaningful to toggle/enable.
  const singleRubricFilter = Boolean(rubricId || batchId)

  const showFolderParam = searchParams.get('show_folder')
  const showFolder = showFolderParam !== null ? showFolderParam === '1' : Boolean(batchId)

  const showRubricParam = searchParams.get('show_rubric')
  const showRubricColumn = showRubricParam !== null ? showRubricParam === '1' : !singleRubricFilter

  const showOverview = searchParams.get('overview') === '1' && singleRubricFilter

  function setParam(key: string, value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(key, value)
        else next.delete(key)
        if (key === 'batch_id' && value) next.delete('rubric_id')
        return next
      },
      { replace: true },
    )
  }

  function setBoolParam(key: string, value: boolean, defaultValue: boolean) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value === defaultValue) next.delete(key)
        else next.set(key, value ? '1' : '0')
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

  const { data: students } = useStudents()
  const { data: rubrics } = useRubrics()
  const { data: courses } = useCourses()
  const { data: batches } = useBatches()
  const { data: editions } = useEditions()
  const { data: batch } = useBatch(batchId || undefined)
  const regradeBatch = useRegradeBatch()

  const {
    data: submissions,
    isLoading,
    error,
  } = useSubmissions({
    student_id: studentId || undefined,
    rubric_id: rubricId || undefined,
    course_id: courseId || undefined,
    edition_id: editionId || undefined,
    batch_id: batchId || undefined,
  })

  const singleRubric = singleRubricFilter && submissions && submissions.length > 0 ? submissions[0].rubric : undefined
  const anyInProgress = submissions?.some((s) => IN_PROGRESS_STATUSES.includes(s.status)) ?? false
  const hasFilters = FILTER_KEYS.some((k) => searchParams.get(k))

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

      {batchId && batch && (
        <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-white p-4">
          <div>
            <h2 className="text-sm font-medium text-slate-900">
              {batch.rubric.title} &middot; {batch.edition.name}
            </h2>
            <p className="text-xs text-slate-500">
              {BATCH_TYPE_LABELS[batch.type]} &middot; {batch.item_count} item(s) &middot; uploaded{' '}
              {formatDateTime(batch.created_at)}
              {batch.original_filename && <> &middot; {batch.original_filename}</>}
            </p>
          </div>
          <button
            onClick={() => regradeBatch.mutate(batch._id)}
            disabled={!submissions?.length || anyInProgress || regradeBatch.isPending}
            title="Force re-grading every submission in this batch: re-extracts each file and discards existing grades/feedback"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 disabled:opacity-50"
          >
            {regradeBatch.isPending ? 'Re-grading...' : 'Regrade all'}
          </button>
        </div>
      )}
      {regradeBatch.isError && <p className="text-sm text-red-600">Failed to trigger re-grading.</p>}
      {regradeBatch.isSuccess && (
        <p className="text-sm text-emerald-600">Queued {regradeBatch.data.regraded} submission(s) for re-grading.</p>
      )}

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <label className="block text-xs font-medium text-slate-600">Student</label>
          <select
            value={studentId}
            onChange={(e) => setParam('student_id', e.target.value)}
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
        {!rubricImpliedByBatch && (
          <div>
            <label className="block text-xs font-medium text-slate-600">Rubric</label>
            <select
              value={rubricId}
              onChange={(e) => setParam('rubric_id', e.target.value)}
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
        )}
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
        <div>
          <label className="block text-xs font-medium text-slate-600">Batch</label>
          <select
            value={batchId}
            onChange={(e) => setParam('batch_id', e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">All batches</option>
            {batches?.map((b) => (
              <option key={b._id} value={b._id}>
                {b.rubric.title} ({formatDateTime(b.created_at)})
              </option>
            ))}
          </select>
        </div>

        <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-slate-600">
          <input
            type="checkbox"
            checked={showFolder}
            onChange={(e) => setBoolParam('show_folder', e.target.checked, Boolean(batchId))}
          />
          Show folder
        </label>

        <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-slate-600">
          <input
            type="checkbox"
            checked={showRubricColumn}
            onChange={(e) => setBoolParam('show_rubric', e.target.checked, !singleRubricFilter)}
          />
          Show rubric column
        </label>

        <button
          type="button"
          disabled={!singleRubricFilter}
          title={
            singleRubricFilter
              ? 'Toggle the grading overview grid'
              : 'Select a batch or a rubric to see the grading overview -- it doesn’t make sense across rubrics'
          }
          onClick={() => setBoolParam('overview', !showOverview, false)}
          className="mb-1.5 rounded-md border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {showOverview ? 'Hide grading overview' : 'Show grading overview'}
        </button>

        {hasFilters && (
          <button onClick={clearFilters} className="mb-1.5 text-sm text-slate-500 hover:underline">
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
              {showFolder && <th className="px-4 py-2">Folder</th>}
              {showRubricColumn && <th className="px-4 py-2">Rubric</th>}
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Created</th>
            </tr>
          </thead>
          <tbody>
            {submissions.map((s) => (
              <tr key={s._id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-4 py-2">
                  <div className="flex items-center gap-1">
                    <Link to={`/submissions/${s._id}`} className="text-slate-900 hover:underline">
                      {s.student ? s.student.name : <span className="text-slate-400">Unmatched</span>}
                    </Link>
                    {s.batch_internal_id && (
                      <MatchStudentDialog submissionId={s._id} currentStudentId={s.student?.student_id} />
                    )}
                  </div>
                </td>
                {showFolder && <td className="px-4 py-2 font-mono text-xs">{s.batch_internal_id}</td>}
                {showRubricColumn && <td className="px-4 py-2">{s.rubric.title}</td>}
                <td className="px-4 py-2">
                  <StatusBadge status={s.status} />
                </td>
                <td className="px-4 py-2 text-slate-500">{formatDateTime(s.created_at)}</td>
              </tr>
            ))}
            {submissions.length === 0 && (
              <tr>
                <td colSpan={2 + Number(showFolder) + Number(showRubricColumn) + 2} className="px-4 py-6 text-center text-slate-400">
                  No submissions match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {showOverview && singleRubric && submissions && (
        // Breaks out of the site-wide `max-w-5xl` shell on purpose: with one
        // column per rubric question this table is naturally wide, and
        // constraining it to the article width would make it need scrolling
        // twice over (page + table). `vw`-based margins measure against the
        // viewport, not the (narrower) parent, regardless of nesting.
        <div className="relative left-1/2 right-1/2 -mx-[50vw] w-screen px-4">
          <GradingGrid submissions={submissions} rubric={singleRubric} />
        </div>
      )}
    </div>
  )
}
