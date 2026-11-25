import { Link, useNavigate, useParams } from 'react-router-dom'
import { API_URL } from '../api/client'
import { useRegradeSubmission, useSubmission } from '../api/hooks'
import AnswerGradeEditor from '../components/AnswerGradeEditor'
import AnswerImages from '../components/AnswerImages'
import DeleteSubmissionButton from '../components/DeleteSubmissionButton'
import GradeBadge from '../components/GradeBadge'
import { GradingInfoTooltip, SubmissionGradingInfoTooltip } from '../components/GradingInfo'
import MatchStudentDialog from '../components/MatchStudentDialog'
import RenameLabelDialog from '../components/RenameLabelDialog'
import StatusBadge from '../components/StatusBadge'
import { UntaggedCellsBadge, UntaggedCellsSummary } from '../components/UntaggedCells'
import { effectiveGrade, FORMAT_FILE_INFO, IN_PROGRESS_STATUSES, SEGMENTED_FORMATS, submissionName } from '../api/types'
import { parseId } from '../lib/ids'

export default function SubmissionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { data: submission, isLoading, error } = useSubmission(parseId(id))
  const regrade = useRegradeSubmission()
  const navigate = useNavigate()

  if (isLoading) return <p className="text-sm text-slate-500">Loading...</p>
  if (error || !submission) return <p className="text-sm text-red-600">Failed to load this submission.</p>

  const questionsById = new Map(submission.rubric.questions.map((q) => [q.id, q]))
  const inProgress = IN_PROGRESS_STATUSES.includes(submission.status)
  const fileInfo = FORMAT_FILE_INFO[submission.rubric.format]
  const segmented = SEGMENTED_FORMATS.includes(submission.rubric.format)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-1 text-xl font-semibold">
            {submission.student || submission.label ? submissionName(submission) : 'Unmatched'} &middot;{' '}
            {submission.rubric.title}
            {submission.label && !submission.student && (
              <RenameLabelDialog submissionId={submission.id} currentLabel={submission.label} />
            )}
            {submission.batch_internal_id && !submission.label && (
              <MatchStudentDialog submissionId={submission.id} currentStudentId={submission.student?.student_id} />
            )}
          </h1>
          <p className="text-sm text-slate-500">
            {/* A labelled submission's label is already the heading -- nothing to show before the edition. */}
            {!submission.student && submission.label ? null : (
              <>
                {submission.student ? submission.student.student_id : submission.batch_internal_id} &middot;{' '}
              </>
            )}
            {submission.edition.name}
            {submission.batch_id && (
              <>
                {' '}
                &middot;{' '}
                <Link to={`/?batch_id=${submission.batch_id}`} className="hover:underline">
                  batch
                </Link>
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <SubmissionGradingInfoTooltip answers={submission.answers} />
          <StatusBadge status={submission.status} />
          <a
            href={`${API_URL}/submissions/${submission.id}/file`}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700"
          >
            Download {fileInfo.fileLabel}
          </a>
          {submission.original_object_key && (
            <a
              href={`${API_URL}/submissions/${submission.id}/original`}
              title={`The file as uploaded${submission.original_filename ? ` (${submission.original_filename})` : ''}, before conversion to PDF`}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700"
            >
              Download original
            </a>
          )}
          <a
            href={`${API_URL}/submissions/${submission.id}/feedback.html`}
            title="Standalone HTML page with this submission's feedback -- no internet connection needed to view it"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700"
          >
            Export feedback
          </a>
          {segmented && submission.document && (
            <Link
              to={`/submissions/${submission.id}/segments`}
              title="Review where each answer starts and ends in the PDF"
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700"
            >
              Edit segmentation
            </Link>
          )}
          <button
            onClick={() => {
              // Not a hard block -- see SubmissionsPage's regrade bar.
              if (inProgress && !window.confirm('This submission is still in progress (or stuck if its job was lost). Re-grade anyway?'))
                return
              regrade.mutate(submission.id)
            }}
            disabled={regrade.isPending}
            title="Force re-grading: re-extracts the file and replaces the LLM grades/feedback (manual grades are kept)"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 disabled:opacity-50"
          >
            {regrade.isPending ? 'Re-grading...' : 'Re-grade'}
          </button>
          <DeleteSubmissionButton
            submission={submission}
            onDeleted={() => navigate(submission.batch_id ? `/?batch_id=${submission.batch_id}` : '/')}
          />
        </div>
      </div>

      {regrade.isError && <p className="text-sm text-red-600">Failed to trigger re-grading.</p>}

      {inProgress && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {submission.status === 'converting'
            ? 'Converting the PDF to text'
            : submission.status === 'segmenting'
              ? 'The LLM is looking for where each answer starts'
              : 'Grading in progress'}{' '}
          -- this page refreshes automatically.
        </p>
      )}
      {submission.status === 'awaiting_segmentation' && (
        <div className="flex items-center justify-between rounded-md bg-violet-50 px-3 py-2 text-sm text-violet-900">
          <span>
            {submission.segmentation_source === 'llm'
              ? 'The LLM suggested where each answer is. Review it before grading.'
              : 'Mark where each answer is in this PDF before grading.'}
            {submission.error && <span className="block text-red-700">{submission.error}</span>}
          </span>
          <Link
            to={`/submissions/${submission.id}/segments`}
            className="rounded-md bg-violet-700 px-3 py-1.5 font-medium text-white"
          >
            Segment answers
          </Link>
        </div>
      )}
      {submission.status === 'failed' && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{submission.error ?? 'Grading failed.'}</p>
      )}
      <UntaggedCellsSummary submission={submission} />

      <div className="space-y-4">
        {submission.answers.map((answer) => {
          const question = questionsById.get(answer.question_id)
          const grade = effectiveGrade(answer)
          return (
            <div key={answer.question_id} className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="font-medium">{question?.title ?? answer.question_id}</h2>
                <div className="flex items-center gap-2">
                  <UntaggedCellsBadge count={answer.untagged_cells} />
                  {answer.manual_grade && (
                    <span className="text-xs text-slate-500" title="Grade set manually by a TA">
                      edited
                    </span>
                  )}
                  {grade && <GradeBadge level={grade.level} scale={submission.rubric.grading_scale} />}
                  <GradingInfoTooltip answer={answer} />
                </div>
              </div>
              <p className="mb-3 whitespace-pre-wrap rounded bg-slate-50 p-3 text-sm text-slate-700">
                {answer.student_answer || <span className="text-slate-400">No answer provided.</span>}
              </p>
              <AnswerImages submissionId={submission.id} answer={answer} />
              <AnswerGradeEditor
                submissionId={submission.id}
                answer={answer}
                scale={submission.rubric.grading_scale}
                disabled={inProgress}
              />
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
