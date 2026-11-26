import { useState } from 'react'
import { useClearManualGeneralFeedback, useRegenerateGeneralFeedback, useSetManualGeneralFeedback } from '../api/hooks'
import { effectiveGeneralFeedback, type Submission } from '../api/types'
import { GradingDetailsTooltip } from './GradingInfo'

/**
 * The submission's general feedback (AGENTS.md "General
 * feedback"): the LLM's text plus the TA's own override, stored next to
 * each other like an answer's `grade`/`manual_grade` -- with an override,
 * the LLM's version stays visible underneath.
 *
 * "Regenerate" rewrites only the LLM's text from the current effective
 * grades, which is what's needed after overriding some grades: the
 * general feedback is not updated automatically when a grade is edited, so a
 * notice says when it's older than the latest grade edit.
 */
export default function GeneralFeedbackEditor({ submission, disabled }: { submission: Submission; disabled: boolean }) {
  const [editing, setEditing] = useState(false)
  const clear = useClearManualGeneralFeedback()
  const regenerate = useRegenerateGeneralFeedback()

  const hasInstructions = !!submission.rubric.general_feedback_instructions?.trim()
  const text = effectiveGeneralFeedback(submission)
  // Nothing to show or do: the rubric asks for no general feedback and no one wrote one.
  if (!hasInstructions && !text) return null

  const llm = submission.general_feedback
  const writtenAt = typeof llm?.info?.graded_at === 'string' ? new Date(llm.info.graded_at).getTime() : null
  const lastGradeEdit = Math.max(
    0,
    ...submission.answers.map((a) => (a.manual_graded_at ? new Date(a.manual_graded_at).getTime() : 0)),
  )
  const stale = llm != null && writtenAt != null && lastGradeEdit > writtenAt
  const canRegenerate = hasInstructions && submission.status === 'graded'

  return (
    <div className="mb-4 rounded-lg border border-slate-200 border-l-4 border-l-blue-600 bg-white p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-medium">General feedback</h2>
        <div className="flex items-center gap-2">
          {submission.manual_general_feedback && (
            <span className="text-xs text-slate-500" title="General feedback written by a TA">
              edited
            </span>
          )}
          {llm && <GradingDetailsTooltip info={llm.info ?? {}} />}
        </div>
      </div>

      {editing ? (
        <EditForm submissionId={submission.id} initial={text ?? ''} onDone={() => setEditing(false)} />
      ) : (
        <div className="space-y-2">
          {text ? (
            <p className="whitespace-pre-wrap text-sm text-slate-700">{text}</p>
          ) : (
            <p className="text-sm text-slate-400">
              {submission.status === 'graded' ? 'No general feedback yet.' : 'Written once every question is graded.'}
            </p>
          )}
          {submission.manual_general_feedback && llm && (
            <div className="rounded border border-dashed border-slate-200 p-2 text-xs text-slate-500">
              <span className="font-medium">LLM suggested:</span>
              <p className="mt-1 whitespace-pre-wrap">{llm.feedback}</p>
            </div>
          )}
          {stale && (
            <p className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800">
              Grades were edited after the LLM wrote this general feedback -- regenerate it to take them into account.
            </p>
          )}
          <div className="flex items-center gap-3 text-xs">
            <button
              type="button"
              onClick={() => setEditing(true)}
              disabled={disabled}
              className="font-medium text-slate-600 hover:underline disabled:opacity-50"
            >
              {submission.manual_general_feedback
                ? 'Edit general feedback'
                : text
                  ? 'Override general feedback'
                  : 'Write manually'}
            </button>
            {submission.manual_general_feedback && (
              <button
                type="button"
                onClick={() => clear.mutate(submission.id)}
                disabled={disabled || clear.isPending}
                className="text-slate-500 hover:underline disabled:opacity-50"
              >
                {llm ? 'Revert to LLM general feedback' : 'Remove general feedback'}
              </button>
            )}
            {hasInstructions && (
              <button
                type="button"
                onClick={() => regenerate.mutate(submission.id)}
                disabled={disabled || !canRegenerate || regenerate.isPending}
                title={
                  canRegenerate
                    ? 'Ask the LLM to write the general feedback again from the current grades (manual grades included). Nothing else is re-graded' +
                      (submission.manual_general_feedback ? '; your own general feedback stays in place.' : '.')
                    : 'Only a graded submission can have its general feedback regenerated.'
                }
                className="text-slate-500 hover:underline disabled:opacity-50"
              >
                {regenerate.isPending
                  ? 'Regenerating...'
                  : llm
                    ? 'Regenerate general feedback'
                    : 'Generate general feedback'}
              </button>
            )}
            {clear.isError && <span className="text-red-600">Failed to revert.</span>}
            {regenerate.isError && <span className="text-red-600">Failed to trigger regeneration.</span>}
          </div>
        </div>
      )}
    </div>
  )
}

function EditForm({ submissionId, initial, onDone }: { submissionId: number; initial: string; onDone: () => void }) {
  const [feedback, setFeedback] = useState(initial)
  const save = useSetManualGeneralFeedback()

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate({ submissionId, feedback }, { onSuccess: onDone })
      }}
    >
      <textarea
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        rows={4}
        placeholder="General feedback for the student"
        className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
      />
      {save.isError && <p className="text-sm text-red-600">Failed to save the general feedback.</p>}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onDone}
          className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={save.isPending || !feedback.trim()}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {save.isPending ? 'Saving...' : 'Save'}
        </button>
      </div>
    </form>
  )
}
