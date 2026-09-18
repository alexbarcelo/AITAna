import { Link, useParams } from 'react-router-dom'
import { API_URL } from '../api/client'
import { useRegradeSubmission, useSubmission } from '../api/hooks'
import GradeBadge from '../components/GradeBadge'
import MatchStudentDialog from '../components/MatchStudentDialog'
import StatusBadge from '../components/StatusBadge'
import { FORMAT_FILE_INFO, IN_PROGRESS_STATUSES } from '../api/types'

export default function SubmissionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: submission, isLoading, error } = useSubmission(id)
  const regrade = useRegradeSubmission()

  if (isLoading) return <p className="text-sm text-slate-500">Loading...</p>
  if (error || !submission) return <p className="text-sm text-red-600">Failed to load this submission.</p>

  const questionsById = new Map(submission.rubric.questions.map((q) => [q.id, q]))
  const inProgress = IN_PROGRESS_STATUSES.includes(submission.status)
  const fileInfo = FORMAT_FILE_INFO[submission.rubric.format]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-1 text-xl font-semibold">
            {submission.student ? submission.student.name : 'Unmatched'} &middot; {submission.rubric.title}
            {submission.batch_internal_id && (
              <MatchStudentDialog submissionId={submission._id} currentStudentId={submission.student?.student_id} />
            )}
          </h1>
          <p className="text-sm text-slate-500">
            {submission.student ? submission.student.student_id : submission.batch_internal_id} &middot;{' '}
            {submission.edition.name}
            {submission.batch && (
              <>
                {' '}
                &middot;{' '}
                <Link to={`/?batch_id=${submission.batch._id}`} className="hover:underline">
                  batch
                </Link>
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={submission.status} />
          <a
            href={`${API_URL}/submissions/${submission._id}/file`}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700"
          >
            Download {fileInfo.label}
          </a>
          <button
            onClick={() => regrade.mutate(submission._id)}
            disabled={inProgress || regrade.isPending}
            title="Force re-grading: re-extracts the file and discards existing grades/feedback"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 disabled:opacity-50"
          >
            {regrade.isPending ? 'Re-grading...' : 'Re-grade'}
          </button>
        </div>
      </div>

      {regrade.isError && <p className="text-sm text-red-600">Failed to trigger re-grading.</p>}

      {inProgress && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          Grading in progress -- this page refreshes automatically.
        </p>
      )}
      {submission.status === 'failed' && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{submission.error ?? 'Grading failed.'}</p>
      )}

      <div className="space-y-4">
        {submission.answers.map((answer) => {
          const question = questionsById.get(answer.question_id)
          return (
            <div key={answer.question_id} className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="font-medium">{question?.title ?? answer.question_id}</h2>
                {answer.grade && <GradeBadge level={answer.grade.level} scale={submission.rubric.grading_scale} />}
              </div>
              <p className="mb-3 whitespace-pre-wrap rounded bg-slate-50 p-3 text-sm text-slate-700">
                {answer.student_answer || <span className="text-slate-400">No answer provided.</span>}
              </p>
              {answer.grade && <p className="text-sm text-slate-600">{answer.grade.feedback}</p>}
            </div>
          )
        })}
        {submission.answers.length === 0 && !inProgress && (
          <p className="text-slate-400">No answers extracted yet.</p>
        )}
      </div>
    </div>
  )
}
