import type { Rubric } from '../api/types'

/**
 * Editions carry no course link (see `Edition`'s doc comment in
 * `api/types.ts`) -- "which courses use this edition" and "which editions
 * does this course use" are both derived from `Rubric.course`/`.edition`,
 * the one place a course and an edition are actually tied together. Shared
 * by `EditionsPage.tsx` and `CoursesPage.tsx` so the two directions of this
 * derivation can't quietly drift apart.
 */
export function editionsForCourse(rubrics: Pick<Rubric, 'course' | 'edition'>[], courseId: string): NonNullable<Rubric['edition']>[] {
  // rubric.course/.edition are nested Links -- keyed by `.id`, not `._id`
  // (types.ts's NestedCourse/NestedEdition doc comment, AGENTS.md sharp
  // edge #7). `courseId`/`editionId` themselves come from the top-level
  // Course/Edition lists, which do use `_id`.
  const matches = rubrics.filter((r) => r.course.id === courseId && r.edition !== null)
  return [...new Map(matches.map((r) => [r.edition!.id, r.edition!])).values()]
}

export function coursesForEdition(rubrics: Pick<Rubric, 'course' | 'edition'>[], editionId: string): Rubric['course'][] {
  const matches = rubrics.filter((r) => r.edition?.id === editionId)
  return [...new Map(matches.map((r) => [r.course.id, r.course])).values()]
}
