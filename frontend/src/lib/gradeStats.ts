import { effectiveGrade, type AnsweredQuestion, type GradingScale } from '../api/types'
import { findLevel, maxScore, orderedLevels } from './gradingScale'

/**
 * The score of a grade level in this rubric's scale. A level not in `scale`
 * (e.g. removed after grading) scores `null` and is left out of every
 * statistic, same as an ungraded answer.
 */
export function levelScore(level: string, scale: GradingScale): number | null {
  return findLevel(scale, level)?.score ?? null
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
  /** Sum of the graded answers' scores over the best possible sum (every one
   * at the scale's top level): 1 = all top marks. `null` if nothing is
   * scoreable. */
  meanScore: number | null
}

export function gradeStats(answers: AnsweredQuestion[], scale: GradingScale): GradeStats {
  const counts: Record<string, number> = Object.fromEntries(orderedLevels(scale).map((level) => [level.id, 0]))
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
    meanScore: scored > 0 && maxScore(scale) > 0 ? scoreSum / (scored * maxScore(scale)) : null,
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
