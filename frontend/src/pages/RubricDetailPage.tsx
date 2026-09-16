import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useDeleteRubric, useRubric, useTestRubricAnswer } from '../api/hooks'
import type { GradingScale } from '../api/types'
import GradeBadge from '../components/GradeBadge'
import RubricForm from '../components/RubricForm'
import { formatDate } from '../lib/date'

function QuestionTestPanel({
  rubricId,
  questionId,
  gradingScale,
}: {
  rubricId: string
  questionId: string
  gradingScale: GradingScale
}) {
  const [expanded, setExpanded] = useState(false)
  const [answer, setAnswer] = useState('')
  const testAnswer = useTestRubricAnswer()

  function handleAnswerChange(value: string) {
    setAnswer(value)
    if (testAnswer.data || testAnswer.isError) testAnswer.reset() // a stale result would misleadingly look current
  }

  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <button type="button" onClick={() => setExpanded((v) => !v)} className="text-xs font-medium text-slate-500 hover:underline">
        {expanded ? 'Hide test panel' : 'Test this question'}
      </button>

      {expanded && (
        <div className="mt-2 space-y-2">
          <p className="text-xs text-slate-500">
            Type a sample answer and grade it exactly as a real submission would be -- nothing here is saved.
          </p>
          <textarea
            value={answer}
            onChange={(e) => handleAnswerChange(e.target.value)}
            placeholder="Sample student answer..."
            rows={3}
            className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => testAnswer.mutate({ rubricId, questionId, answer })}
              disabled={testAnswer.isPending}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {testAnswer.isPending ? 'Grading...' : 'Grade this answer'}
            </button>
            {testAnswer.isError && (
              <span className="text-xs text-red-600">
                {(testAnswer.error as { message?: string })?.message ?? 'Grading failed.'}
              </span>
            )}
          </div>
          {testAnswer.data && (
            <div className="rounded-md bg-slate-50 p-3">
              <div className="mb-1">
                <GradeBadge level={testAnswer.data.level} scale={gradingScale} />
              </div>
              <p className="text-sm text-slate-600">{testAnswer.data.feedback}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function RubricDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { data: rubric, isLoading, error } = useRubric(id)
  const [editing, setEditing] = useState(false)
  const deleteRubric = useDeleteRubric()

  if (isLoading) return <p className="text-sm text-slate-500">Loading...</p>
  if (error || !rubric) return <p className="text-sm text-red-600">Failed to load this rubric.</p>

  function handleDelete() {
    if (!rubric) return
    if (!window.confirm(`Delete "${rubric.title}"? This can't be undone.`)) return
    deleteRubric.mutate(rubric._id, { onSuccess: () => navigate('/rubrics') })
  }

  if (editing) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold">Edit {rubric.title}</h1>
          <button
            onClick={() => setEditing(false)}
            className="rounded-md bg-slate-100 px-3 py-1.5 text-sm font-medium text-slate-600"
          >
            Cancel
          </button>
        </div>
        <RubricForm rubric={rubric} onDone={() => setEditing(false)} />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold">{rubric.title}</h1>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-500">
            <span>
              Slug: <code className="rounded bg-slate-100 px-1 font-mono text-xs">{rubric.slug}</code>
            </span>
            <span>Course: {rubric.course.name}</span>
            <span>Edition: {rubric.edition ? rubric.edition.name : 'any (reusable across editions)'}</span>
            <span>Created: {formatDate(rubric.created_at)}</span>
            <span>Last updated: {formatDate(rubric.updated_at)}</span>
          </div>
          {deleteRubric.isError && (
            <p className="mt-1 text-sm text-red-600">
              {(deleteRubric.error as { message?: string })?.message ?? 'Failed to delete this rubric.'}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setEditing(true)}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
          >
            Edit
          </button>
          <button
            onClick={handleDelete}
            disabled={deleteRubric.isPending}
            className="rounded-md bg-red-50 px-3 py-1.5 text-sm font-medium text-red-600 disabled:opacity-50"
          >
            {deleteRubric.isPending ? 'Deleting...' : 'Delete'}
          </button>
        </div>
      </div>

      <div className="space-y-4">
        {rubric.questions.map((q, i) => (
          <div key={q.id} className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="flex items-center gap-2 font-medium">
                {i + 1}. {q.title}
                {q.needs_python_sandbox && (
                  <span
                    className="rounded bg-slate-100 px-1.5 py-0.5 text-xs font-normal text-slate-500"
                    title="The grading LLM gets a sandboxed Python code-execution tool for this question."
                  >
                    Python sandbox
                  </span>
                )}
              </h2>
              <span className="font-mono text-xs text-slate-400">
                {q.id}
                {q.field && q.field !== q.id ? ` (field: ${q.field})` : ''}
              </span>
            </div>
            <p className="mb-3 whitespace-pre-wrap text-sm text-slate-700">{q.question}</p>
            {q.context && (
              <div className="mb-3 rounded bg-slate-50 p-3">
                <p className="text-xs font-medium uppercase text-slate-400">Context</p>
                <p className="whitespace-pre-wrap text-sm text-slate-600">{q.context}</p>
              </div>
            )}
            <div className="mb-3">
              <p className="text-xs font-medium uppercase text-slate-400">Grading instructions</p>
              <p className="whitespace-pre-wrap text-sm text-slate-600">{q.rubric}</p>
            </div>
            {q.model_answer && (
              <div className="mb-3 rounded bg-slate-50 p-3">
                <p className="text-xs font-medium uppercase text-slate-400">Model answer</p>
                <p className="whitespace-pre-wrap text-sm text-slate-600">{q.model_answer}</p>
              </div>
            )}
            {(q.expected_points?.length ?? 0) > 0 && (
              <div>
                <p className="text-xs font-medium uppercase text-slate-400">Expected points</p>
                <ul className="mt-1 list-inside list-disc text-sm text-slate-600">
                  {q.expected_points!.map((point, idx) => (
                    <li key={idx}>{point}</li>
                  ))}
                </ul>
              </div>
            )}
            <QuestionTestPanel rubricId={rubric._id} questionId={q.id} gradingScale={rubric.grading_scale} />
          </div>
        ))}
        {rubric.questions.length === 0 && <p className="text-slate-400">No questions.</p>}
      </div>
    </div>
  )
}
