import type { ReactNode } from 'react'
import type { Question } from '../api/types'

/**
 * The question's grading aids (rubric-wide context, question context,
 * instructions, model answer, expected points), each collapsed by default:
 * wherever this shows up the focus is the student's answer, these are just
 * one click away when the grade needs checking. Used by the grading
 * overview's cell modal and the submission page.
 */
export default function QuestionDetails({
  question,
  rubricContext,
}: {
  question: Question
  rubricContext?: string | null
}) {
  const sections: { label: string; content: ReactNode }[] = []
  if (rubricContext?.trim()) {
    sections.push({ label: 'Assignment context', content: <p className="whitespace-pre-wrap">{rubricContext}</p> })
  }
  if (question.context)
    sections.push({ label: 'Context', content: <p className="whitespace-pre-wrap">{question.context}</p> })
  sections.push({ label: 'Grading instructions', content: <p className="whitespace-pre-wrap">{question.rubric}</p> })
  if (question.model_answer) {
    sections.push({ label: 'Model answer', content: <p className="whitespace-pre-wrap">{question.model_answer}</p> })
  }
  if ((question.expected_points?.length ?? 0) > 0) {
    sections.push({
      label: 'Expected points',
      content: (
        <ul className="list-inside list-disc">
          {question.expected_points!.map((point, idx) => (
            <li key={idx}>{point}</li>
          ))}
        </ul>
      ),
    })
  }
  return (
    <div className="mb-3 divide-y divide-slate-100 rounded border border-slate-200">
      {sections.map(({ label, content }) => (
        <details key={label} className="px-3 py-1.5">
          <summary className="cursor-pointer text-xs font-medium uppercase text-slate-400 hover:text-slate-600">
            {label}
          </summary>
          <div className="mt-1 pb-1 text-sm text-slate-600">{content}</div>
        </details>
      ))}
    </div>
  )
}
