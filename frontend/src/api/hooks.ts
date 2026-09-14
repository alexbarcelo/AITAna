import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { client } from './client'
import { IN_PROGRESS_STATUSES } from './types'
import type { Course, Edition, Grade, Question, Rubric, Student, Submission, SubmissionFormat, SubmissionStatus } from './types'

async function unwrap<T>(promise: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await promise
  if (error) throw error
  return data as T
}

/** Newest-first by `created_at` -- used for every course/edition dropdown so
 * the thing someone most likely just set up (this term's course, this
 * term's edition) sorts to the top instead of wherever Mongo's natural
 * insertion order happens to put it. `created_at` is optional in the
 * generated type only because Beanie populates it via a `default_factory`
 * pydantic can't express as a static JSON Schema `default` (see the
 * hand-declared `Course`/`Edition` interfaces for the same
 * looser-than-reality generated-type pattern) -- it's always present in a
 * real response, but this sorts defensively rather than assuming that. */
function byMostRecentFirst<T extends { created_at?: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
}

// ---- Students ----

export function useStudents() {
  return useQuery({
    queryKey: ['students'],
    queryFn: () => unwrap(client.GET('/students')),
  })
}

export function useCreateStudent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { student_id: string; name: string; email?: string }) =>
      unwrap(client.POST('/students', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['students'] }),
  })
}

export function useSetStudentEditions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ studentId, editionIds }: { studentId: string; editionIds: string[] }) =>
      unwrap(
        client.PUT('/students/{student_id}/editions', {
          params: { path: { student_id: studentId } },
          body: { edition_ids: editionIds },
        }),
      ) as Promise<Student>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['students'] }),
  })
}

// ---- Courses ----

export function useCourses() {
  return useQuery({
    queryKey: ['courses'],
    queryFn: async () => byMostRecentFirst(await (unwrap(client.GET('/courses')) as Promise<Course[]>)),
  })
}

export function useCreateCourse() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; slug?: string }) => unwrap(client.POST('/courses', { body })) as Promise<Course>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['courses'] }),
  })
}

// ---- Editions ----
// Global, not scoped to a course (see Edition's doc comment in ./types.ts)
// -- GET /editions no longer takes a course_id, and every caller here just
// wants the full list.

export function useEditions() {
  return useQuery({
    queryKey: ['editions'],
    queryFn: async () => byMostRecentFirst(await (unwrap(client.GET('/editions')) as Promise<Edition[]>)),
  })
}

export function useCreateEdition() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; slug?: string }) => unwrap(client.POST('/editions', { body })) as Promise<Edition>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['editions'] }),
  })
}

// ---- Rubrics ----

export function useRubrics() {
  return useQuery({
    queryKey: ['rubrics'],
    queryFn: () => unwrap(client.GET('/rubrics')) as Promise<Rubric[]>,
  })
}

export function useRubric(id: string | undefined) {
  return useQuery({
    queryKey: ['rubrics', id],
    queryFn: () =>
      unwrap(client.GET('/rubrics/{rubric_id}', { params: { path: { rubric_id: id! } } })) as Promise<Rubric>,
    enabled: Boolean(id),
  })
}

export function useCreateRubric() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      title: string
      slug?: string
      course_id: string
      edition_id?: string
      // Required here (unlike the YAML-upload path below) because the
      // generated `RubricCreate` type -- despite the field being genuinely
      // optional server-side, defaulting to `pdf` -- comes out non-optional:
      // openapi-typescript treats a schema property carrying a `default` as
      // always-present, since that's true for *responses* but this same
      // generated type doubles as the request body shape here. The caller
      // (NewRubricForm's ManualRubricForm) always has a concrete value from
      // its own `format` state, so this isn't a real constraint in practice.
      format: SubmissionFormat
      grading_scale?: Record<string, string>
      questions: Question[]
    }) => unwrap(client.POST('/rubrics', { body })) as Promise<Rubric>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rubrics'] }),
  })
}

export function useUploadRubricYaml() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      file,
      slug,
      courseId,
      editionId,
      format,
    }: {
      file: File
      slug?: string
      courseId?: string
      editionId?: string
      format?: SubmissionFormat
    }) => {
      const formData = new FormData()
      formData.append('yaml_file', file)
      if (slug) formData.append('slug', slug)
      if (courseId) formData.append('course_id', courseId)
      if (editionId) formData.append('edition_id', editionId)
      if (format) formData.append('format', format)
      // See useCreateSubmission for why `body` is cast here: openapi-fetch
      // types multipart bodies from the JSON-ish schema, but the actual wire
      // format is a FormData instance, which it passes through untouched.
      return unwrap(
        client.POST('/rubrics/upload', {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          body: formData as any,
        }),
      ) as Promise<Rubric>
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rubrics'] }),
  })
}

export function useTestRubricAnswer() {
  return useMutation({
    mutationFn: ({ rubricId, questionId, answer }: { rubricId: string; questionId: string; answer: string }) =>
      unwrap(
        client.POST('/rubrics/{rubric_id}/test-answer', {
          params: { path: { rubric_id: rubricId } },
          body: { question_id: questionId, answer },
        }),
      ) as Promise<Grade>,
    // No cache to invalidate -- this doesn't persist anything (see the
    // endpoint's own docstring), it's a "try it" call.
  })
}

// ---- Submissions ----

export function useSubmissions(filters?: {
  student_id?: string
  rubric_id?: string
  course_id?: string
  edition_id?: string
  status?: SubmissionStatus
}) {
  return useQuery({
    queryKey: ['submissions', filters],
    queryFn: () =>
      unwrap(
        client.GET('/submissions', {
          params: { query: filters ?? {} },
        }),
      ) as unknown as Promise<Submission[]>,
  })
}

export function useSubmission(id: string | undefined) {
  return useQuery({
    queryKey: ['submissions', id],
    queryFn: () =>
      unwrap(
        client.GET('/submissions/{submission_id}', {
          params: { path: { submission_id: id! } },
        }),
      ) as unknown as Promise<Submission>,
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && IN_PROGRESS_STATUSES.includes(status) ? 2000 : false
    },
  })
}

export function useRegradeSubmission() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (submissionId: string) =>
      unwrap(
        client.POST('/submissions/{submission_id}/regrade', {
          params: { path: { submission_id: submissionId } },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission._id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useCreateSubmission() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      file,
      studentId,
      rubricId,
      editionId,
    }: {
      file: File
      studentId: string
      rubricId: string
      editionId?: string
    }) => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('student_id', studentId)
      formData.append('rubric_id', rubricId)
      if (editionId) formData.append('edition_id', editionId)
      // openapi-fetch types `body` from the JSON-ish multipart schema; the
      // actual wire format is a FormData instance (see defaultBodySerializer
      // in openapi-fetch, which passes FormData through untouched).
      return unwrap(
        client.POST('/submissions', {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          body: formData as any,
        }),
      ) as unknown as Promise<Submission>
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['submissions'] }),
  })
}
