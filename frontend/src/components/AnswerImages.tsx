import { API_URL } from '../api/client'
import type { AnsweredQuestion } from '../api/types'

/**
 * Thumbnails of the figures sent to the grading LLM with this answer
 * (`include_images` questions only -- `image_count` is 0 otherwise). Images
 * are never persisted: the endpoint re-extracts them from the stored file.
 */
export default function AnswerImages({ submissionId, answer }: { submissionId: string; answer: AnsweredQuestion }) {
  if (answer.image_count <= 0) return null
  return (
    <div className="mb-3 flex flex-wrap gap-2">
      {Array.from({ length: answer.image_count }, (_, n) => {
        const src = `${API_URL}/submissions/${submissionId}/answers/${answer.question_id}/images/${n}`
        return (
          <a key={n} href={src} target="_blank" rel="noreferrer" title="Figure sent to the corrector">
            <img src={src} alt={`Figure ${n + 1}`} className="h-40 rounded border border-slate-200 bg-white" />
          </a>
        )
      })}
    </div>
  )
}
