import { useState } from 'react'
import { useClearManualGrade, useSetManualGrade } from '../api/hooks'
import { effectiveGrade, type AnsweredQuestion, type GradingScale } from '../api/types'
import { prettifyLevel } from '../lib/gradeColor'
import GradeBadge from './GradeBadge'

/**
 * Feedback area of one answer card on the submission page, plus the TA's
 * manual override editor. An override is stored as `manual_grade` next to
 * the LLM's own `grade` (never replacing it -- see `AnsweredQuestion` in
 * `documents/submission.py`), so when one exists the LLM's suggestion is
 * still shown underneath; after a re-grade that's how a TA notices the LLM
 * now disagrees with their override (overrides survive re-grading).
 */
export default function AnswerGradeEditor({
  submissionId,
  answer,
  scale,
  disabled,
}: {
  submissionId: string
  answer: AnsweredQuestion
  scale: GradingScale
  disabled: boolean
}) {
  const [editing, setEditing] = useState(false)
  const clear = useClearManualGrade()
  const grade = effectiveGrade(answer)

  if (editing) {
    return <EditForm submissionId={submissionId} answer={answer} scale={scale} onDone={() => setEditing(false)} />
  }

  return (
    <div className="space-y-2">
      {grade && <p className="text-sm text-slate-600">{grade.feedback}</p>}
      {answer.manual_grade && answer.grade && (
        <div className="rounded border border-dashed border-slate-200 p-2 text-xs text-slate-500">
          <span className="mr-2 font-medium">LLM suggested:</span>
          <GradeBadge level={answer.grade.level} scale={scale} />
          <p className="mt-1">{answer.grade.feedback}</p>
        </div>
      )}
      <div className="flex items-center gap-3 text-xs">
        <button
          type="button"
          onClick={() => setEditing(true)}
          disabled={disabled}
          className="font-medium text-slate-600 hover:underline disabled:opacity-50"
        >
          {answer.manual_grade ? 'Edit grade' : grade ? 'Override grade' : 'Grade manually'}
        </button>
        {answer.manual_grade && (
          <button
            type="button"
            onClick={() => clear.mutate({ submissionId, questionId: answer.question_id })}
            disabled={disabled || clear.isPending}
            className="text-slate-500 hover:underline disabled:opacity-50"
          >
            {answer.grade ? 'Revert to LLM grade' : 'Remove manual grade'}
          </button>
        )}
        {clear.isError && <span className="text-red-600">Failed to revert.</span>}
      </div>
    </div>
  )
}

function EditForm({
  submissionId,
  answer,
  scale,
  onDone,
}: {
  submissionId: string
  answer: AnsweredQuestion
  scale: GradingScale
  onDone: () => void
}) {
  const initial = effectiveGrade(answer)
  const levels = Object.keys(scale)
  const [level, setLevel] = useState(initial?.level && initial.level in scale ? initial.level : levels[0])
  const [feedback, setFeedback] = useState(initial?.feedback ?? '')
  const save = useSetManualGrade()

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(
          { submissionId, questionId: answer.question_id, grade: { level, feedback } },
          { onSuccess: onDone },
        )
      }}
    >
      <select
        value={level}
        onChange={(e) => setLevel(e.target.value)}
        className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
      >
        {levels.map((id) => (
          <option key={id} value={id}>
            {prettifyLevel(id)} — {scale[id]}
          </option>
        ))}
      </select>
      <textarea
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        rows={3}
        placeholder="Feedback for the student"
        className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
      />
      {save.isError && <p className="text-sm text-red-600">Failed to save the grade.</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onDone} className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
          Cancel
        </button>
        <button
          type="submit"
          disabled={save.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {save.isPending ? 'Saving...' : 'Save'}
        </button>
      </div>
    </form>
  )
}
