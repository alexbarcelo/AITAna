import { effectiveGrade, type AnsweredQuestion, type GradingScale } from '../api/types'

/**
 * Grade levels are ordinal labels, not numbers (see `GradingScale`), so the
 * only "average" that makes sense across any rubric's scale is the mean
 * *position* in that scale: 0 = the first (worst) level, 1 = the last
 * (best). It's a relative indicator for comparing students/questions within
 * one rubric -- not a mark, and not comparable across rubrics with
 * different scales. A level not in `scale` (e.g. removed after grading)
 * scores `null` and is left out of the mean, same as an ungraded answer.
 */
export function levelScore(level: string, scale: GradingScale): number | null {
  const levelIds = Object.keys(scale)
  const index = levelIds.indexOf(level)
  if (index === -1) return null
  return levelIds.length > 1 ? index / (levelIds.length - 1) : 1
}

export interface GradeStats {
  /** Effective-grade level -> number of answers at that level, in scale order (every level present, possibly 0). */
  counts: Record<string, number>
  /** Answers with an effective grade (LLM or manual). */
  graded: number
  /** Answers with no effective grade yet. */
  ungraded: number
  /** Answers whose effective grade is a TA's manual override. */
  manual: number
  /** Answers whose extracted text is blank. */
  blank: number
  /** Mean `levelScore` over graded answers, or `null` if none are scoreable. */
  meanScore: number | null
}

export function gradeStats(answers: AnsweredQuestion[], scale: GradingScale): GradeStats {
  const counts: Record<string, number> = Object.fromEntries(Object.keys(scale).map((level) => [level, 0]))
  let graded = 0
  let manual = 0
  let blank = 0
  let scoreSum = 0
  let scored = 0
  for (const answer of answers) {
    if (!answer.student_answer.trim()) blank++
    const grade = effectiveGrade(answer)
    if (!grade) continue
    graded++
    if (answer.manual_grade) manual++
    counts[grade.level] = (counts[grade.level] ?? 0) + 1
    const score = levelScore(grade.level, scale)
    if (score !== null) {
      scoreSum += score
      scored++
    }
  }
  return {
    counts,
    graded,
    ungraded: answers.length - graded,
    manual,
    blank,
    meanScore: scored > 0 ? scoreSum / scored : null,
  }
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

export function formatScore(score: number | null): string {
  return score === null ? '—' : `${Math.round(score * 100)}%`
}
