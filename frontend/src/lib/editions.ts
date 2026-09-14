import type { Edition, Rubric } from '../api/types'

/**
 * Editions carry no course link (see `Edition`'s doc comment in
 * `api/types.ts`) -- "which courses use this edition" and "which editions
 * does this course use" are both derived from `Rubric.course`/`.edition`,
 * the one place a course and an edition are actually tied together. Shared
 * by `EditionsPage.tsx` and `CoursesPage.tsx` so the two directions of this
 * derivation can't quietly drift apart.
 */
export function editionsForCourse(rubrics: Pick<Rubric, 'course' | 'edition'>[], courseId: string): Edition[] {
  const matches = rubrics.filter((r) => r.course._id === courseId && r.edition !== null)
  return [...new Map(matches.map((r) => [r.edition!._id, r.edition!])).values()]
}

export function coursesForEdition(rubrics: Pick<Rubric, 'course' | 'edition'>[], editionId: string): Rubric['course'][] {
  const matches = rubrics.filter((r) => r.edition?._id === editionId)
  return [...new Map(matches.map((r) => [r.course._id, r.course])).values()]
}
