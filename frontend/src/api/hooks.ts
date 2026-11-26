import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useActiveModelId } from '../lib/activeModel'
import { client } from './client'
import { IN_PROGRESS_STATUSES } from './types'
import type {
  AnswerSegment,
  Batch,
  RegenerateGeneralFeedbackResult,
  RegradeResult,
  BatchType,
  BatchUploadResult,
  Course,
  Edition,
  Grade,
  GradingScale,
  LlmModel,
  LlmProvider,
  Question,
  ReasoningEffort,
  Rubric,
  StudentImportFormat,
  StudentImportResult,
  Submission,
  SubmissionDocument,
  SubmissionFormat,
  SubmissionStatus,
} from './types'

async function unwrap<T>(promise: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await promise
  if (error) throw error
  return data as T
}

/** Error thrown by `unwrapWithStatus`: carries the HTTP status (so a caller
 * can react to e.g. a `409` specifically) and FastAPI's `detail` message. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Like `unwrap`, for callers that need to tell error statuses apart. */
async function unwrapWithStatus<T>(promise: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await promise
  if (error !== undefined) {
    const detail = (error as { detail?: unknown })?.detail
    throw new ApiError(response.status, typeof detail === 'string' ? detail : `Request failed (${response.status})`)
  }
  return data as T
}

/** Newest-first by `created_at` -- used for every course/edition dropdown so
 * the thing someone most likely just set up (this term's course, this
 * term's edition) sorts to the top. */
function byMostRecentFirst<T extends { created_at: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
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

export function useImportStudents() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ file, format }: { file: File; format: StudentImportFormat }) => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('format', format)
      // See useCreateSubmission for why `body` is cast here: openapi-fetch
      // types multipart bodies from the JSON-ish schema, but the actual
      // wire format is a FormData instance, which it passes through
      // untouched.
      return unwrap(
        client.POST('/students/import', {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          body: formData as any,
        }),
      ) as Promise<StudentImportResult>
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['students'] }),
  })
}

// ---- Courses ----

export function useDeleteStudent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (studentId: number) =>
      unwrapWithStatus(client.DELETE('/students/{student_id}', { params: { path: { student_id: studentId } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['students'] }),
  })
}

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
// Global, not scoped to a course (see Edition's doc comment in ./types.ts):
// every caller wants the full list.

export function useEditions() {
  return useQuery({
    queryKey: ['editions'],
    queryFn: async () => byMostRecentFirst(await (unwrap(client.GET('/editions')) as Promise<Edition[]>)),
  })
}

export function useCreateEdition() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; slug?: string }) =>
      unwrap(client.POST('/editions', { body })) as Promise<Edition>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['editions'] }),
  })
}

// ---- Rubrics ----

export function useRubrics(filters?: { course_id?: number; edition_id?: number }) {
  return useQuery({
    queryKey: ['rubrics', 'list', filters],
    queryFn: () => unwrap(client.GET('/rubrics', { params: { query: filters ?? {} } })) as Promise<Rubric[]>,
  })
}

/** `{rubric_id: submission count}`; rubrics with none are absent. Keyed
 * under `submissions` so anything invalidating submissions refreshes it. */
export function useRubricSubmissionCounts() {
  return useQuery({
    queryKey: ['submissions', 'counts-by-rubric'],
    queryFn: () => unwrap(client.GET('/rubrics/submission-counts')) as Promise<Record<string, number>>,
  })
}

export function useRubric(id: number | undefined) {
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
      course_id: number
      edition_id?: number
      // Required here (unlike the YAML-upload path below) because the
      // generated `RubricIn` type -- despite the field being genuinely
      // optional server-side, defaulting to `pdf_form` -- comes out non-optional:
      // openapi-typescript treats a schema property carrying a `default` as
      // always-present, since that's true for *responses* but this same
      // generated type doubles as the request body shape here. The caller
      // (NewRubricForm's ManualRubricForm) always has a concrete value from
      // its own `format` state, so this isn't a real constraint in practice.
      format: SubmissionFormat
      grading_scale?: GradingScale
      context?: string | null
      questions: Question[]
    }) => unwrap(client.POST('/rubrics', { body })) as Promise<Rubric>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rubrics'] }),
  })
}

export function useUpdateRubric() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      rubricId,
      ...body
    }: {
      rubricId: number
      title: string
      slug?: string
      course_id: number
      edition_id?: number
      // See useCreateRubric for why this is required here rather than
      // optional despite defaulting server-side.
      format: SubmissionFormat
      grading_scale?: GradingScale
      context?: string | null
      questions: Question[]
    }) =>
      unwrap(
        client.PUT('/rubrics/{rubric_id}', { params: { path: { rubric_id: rubricId } }, body }),
      ) as Promise<Rubric>,
    onSuccess: (rubric) => {
      queryClient.setQueryData(['rubrics', rubric.id], rubric)
      queryClient.invalidateQueries({ queryKey: ['rubrics'] })
    },
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
      courseId?: number
      editionId?: number
      format?: SubmissionFormat
    }) => {
      const formData = new FormData()
      formData.append('yaml_file', file)
      if (slug) formData.append('slug', slug)
      if (courseId) formData.append('course_id', String(courseId))
      if (editionId) formData.append('edition_id', String(editionId))
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

export function useUpdateRubricYaml() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      rubricId,
      file,
      slug,
      courseId,
      editionId,
      format,
    }: {
      rubricId: number
      file: File
      slug?: string
      courseId?: number
      editionId?: number
      format?: SubmissionFormat
    }) => {
      const formData = new FormData()
      formData.append('yaml_file', file)
      if (slug) formData.append('slug', slug)
      if (courseId) formData.append('course_id', String(courseId))
      if (editionId) formData.append('edition_id', String(editionId))
      if (format) formData.append('format', format)
      // See useCreateSubmission for why `body` is cast here: openapi-fetch
      // types multipart bodies from the JSON-ish schema, but the actual wire
      // format is a FormData instance, which it passes through untouched.
      return unwrap(
        client.PUT('/rubrics/{rubric_id}/upload', {
          params: { path: { rubric_id: rubricId } },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          body: formData as any,
        }),
      ) as Promise<Rubric>
    },
    onSuccess: (rubric) => {
      queryClient.setQueryData(['rubrics', rubric.id], rubric)
      queryClient.invalidateQueries({ queryKey: ['rubrics'] })
    },
  })
}

export function useDeleteRubric() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (rubricId: number) =>
      unwrapWithStatus(client.DELETE('/rubrics/{rubric_id}', { params: { path: { rubric_id: rubricId } } })),
    onSuccess: (_data, rubricId) => {
      queryClient.removeQueries({ queryKey: ['rubrics', rubricId] })
      queryClient.invalidateQueries({ queryKey: ['rubrics'] })
    },
  })
}

export function useTestRubricAnswer() {
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: ({ rubricId, questionId, answer }: { rubricId: number; questionId: string; answer: string }) =>
      unwrap(
        client.POST('/rubrics/{rubric_id}/test-answer', {
          params: { path: { rubric_id: rubricId }, query: { model_id: modelId } },
          body: { question_id: questionId, answer },
        }),
      ) as Promise<Grade>,
    // No cache to invalidate -- this doesn't persist anything (see the
    // endpoint's own docstring), it's a "try it" call.
  })
}

// ---- Submissions ----

export function useSubmissions(filters?: {
  student_id?: number
  rubric_id?: number
  course_id?: number
  edition_id?: number
  batch_id?: number
  status?: SubmissionStatus
}) {
  return useQuery({
    queryKey: ['submissions', 'list', filters],
    queryFn: () =>
      unwrap(
        client.GET('/submissions', {
          params: { query: filters ?? {} },
        }),
      ) as unknown as Promise<Submission[]>,
    // Keep polling while anything listed is being worked on, so e.g. a
    // single-question regrade from the grading overview shows up when done.
    refetchInterval: (query) => (query.state.data?.some((s) => IN_PROGRESS_STATUSES.includes(s.status)) ? 3000 : false),
  })
}

export function useSubmission(id: number | undefined) {
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
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: (submissionId: number) =>
      unwrap(
        client.POST('/submissions/{submission_id}/regrade', {
          params: { path: { submission_id: submissionId }, query: { model_id: modelId } },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

/** `force` deletes even while the submission is still being processed
 * (the API answers `409` otherwise) -- for jobs that were lost. */
export function useDeleteSubmission() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ submissionId, force = false }: { submissionId: number; force?: boolean }) =>
      unwrapWithStatus(
        client.DELETE('/submissions/{submission_id}', {
          params: { path: { submission_id: submissionId }, query: { force } },
        }),
      ),
    onSuccess: (_data, { submissionId }) => {
      queryClient.removeQueries({ queryKey: ['submissions', submissionId] })
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useSetSubmissionStudent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ submissionId, studentId }: { submissionId: number; studentId: number }) =>
      unwrap(
        client.PUT('/submissions/{submission_id}/student', {
          params: { path: { submission_id: submissionId } },
          body: { student_id: studentId },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useSetSubmissionLabel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ submissionId, label }: { submissionId: number; label: string }) =>
      unwrap(
        client.PUT('/submissions/{submission_id}/label', {
          params: { path: { submission_id: submissionId } },
          body: { label },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useSetManualGrade() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ submissionId, questionId, grade }: { submissionId: number; questionId: string; grade: Grade }) =>
      unwrap(
        client.PUT('/submissions/{submission_id}/answers/{question_id}/grade', {
          params: { path: { submission_id: submissionId, question_id: questionId } },
          body: grade,
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useClearManualGrade() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ submissionId, questionId }: { submissionId: number; questionId: string }) =>
      unwrap(
        client.DELETE('/submissions/{submission_id}/answers/{question_id}/grade', {
          params: { path: { submission_id: submissionId, question_id: questionId } },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useSetManualGeneralFeedback() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ submissionId, feedback }: { submissionId: number; feedback: string }) =>
      unwrap(
        client.PUT('/submissions/{submission_id}/general-feedback', {
          params: { path: { submission_id: submissionId } },
          body: { feedback },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useClearManualGeneralFeedback() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (submissionId: number) =>
      unwrap(
        client.DELETE('/submissions/{submission_id}/general-feedback', {
          params: { path: { submission_id: submissionId } },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

/** Rewrite only the LLM's general overview from the current (effective)
 * grades -- nothing else is re-graded. */
export function useRegenerateGeneralFeedback() {
  const queryClient = useQueryClient()
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: (submissionId: number) =>
      unwrap(
        client.POST('/submissions/{submission_id}/general-feedback/regenerate', {
          params: { path: { submission_id: submissionId }, query: { model_id: modelId } },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useCreateSubmission() {
  const queryClient = useQueryClient()
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: async ({
      file,
      studentId,
      label,
      rubricId,
      editionId,
      llmSegmentation = true,
    }: {
      file: File
      /** Exactly one of `studentId` / `label` (labelled submission). */
      studentId?: number
      label?: string
      rubricId: number
      editionId?: number
      /** Generic-document rubrics only: ask the LLM for a segmentation suggestion. */
      llmSegmentation?: boolean
    }) => {
      const formData = new FormData()
      formData.append('file', file)
      if (studentId) formData.append('student_id', String(studentId))
      if (label) formData.append('label', label)
      formData.append('rubric_id', String(rubricId))
      if (editionId) formData.append('edition_id', String(editionId))
      formData.append('llm_segmentation', String(llmSegmentation))
      // openapi-fetch types `body` from the JSON-ish multipart schema; the
      // actual wire format is a FormData instance (see defaultBodySerializer
      // in openapi-fetch, which passes FormData through untouched).
      return unwrap(
        client.POST('/submissions', {
          params: { query: { model_id: modelId } },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          body: formData as any,
        }),
      ) as unknown as Promise<Submission>
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['submissions'] }),
  })
}

// ---- Batches ----

export function useBatches(filters?: { rubric_id?: number; edition_id?: number }) {
  return useQuery({
    queryKey: ['batches', 'list', filters],
    queryFn: async () =>
      byMostRecentFirst(
        await (unwrap(client.GET('/batches', { params: { query: filters ?? {} } })) as unknown as Promise<Batch[]>),
      ),
  })
}

export function useBatch(id: number | undefined) {
  return useQuery({
    queryKey: ['batches', id],
    queryFn: () =>
      unwrap(client.GET('/batches/{batch_id}', { params: { path: { batch_id: id! } } })) as unknown as Promise<Batch>,
    enabled: Boolean(id),
  })
}

/** Deletes the batch and every submission it created. Same `force`
 * semantics as `useDeleteSubmission`. */
export function useDeleteBatch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ batchId, force = false }: { batchId: number; force?: boolean }) =>
      unwrapWithStatus(
        client.DELETE('/batches/{batch_id}', { params: { path: { batch_id: batchId }, query: { force } } }),
      ),
    onSuccess: (_data, { batchId }) => {
      queryClient.removeQueries({ queryKey: ['batches', batchId] })
      queryClient.invalidateQueries({ queryKey: ['batches'] })
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useCreateBatch() {
  const queryClient = useQueryClient()
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: async ({
      file,
      rubricId,
      editionId,
      type,
      llmSegmentation = true,
      matchStudents = true,
    }: {
      file: File
      rubricId: number
      editionId?: number
      type: BatchType
      llmSegmentation?: boolean
      /** `false`: no roster matching -- each submission is labelled by its folder name (group projects). */
      matchStudents?: boolean
    }) => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('rubric_id', String(rubricId))
      if (editionId) formData.append('edition_id', String(editionId))
      formData.append('type', type)
      formData.append('llm_segmentation', String(llmSegmentation))
      formData.append('match_students', String(matchStudents))
      // See useCreateSubmission for why `body` is cast here: openapi-fetch
      // types multipart bodies from the JSON-ish schema, but the actual
      // wire format is a FormData instance, which it passes through
      // untouched.
      return unwrap(
        client.POST('/batches', {
          params: { query: { model_id: modelId } },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          body: formData as any,
        }),
      ) as unknown as Promise<BatchUploadResult>
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['batches'] })
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useRegradeSubmissions() {
  const queryClient = useQueryClient()
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: ({ submissionIds, questionId }: { submissionIds: number[]; questionId?: string }) =>
      unwrap(
        client.POST('/submissions/regrade', {
          params: { query: { model_id: modelId } },
          body: { submission_ids: submissionIds, question_id: questionId ?? null },
        }),
      ) as unknown as Promise<RegradeResult>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['submissions'] }),
  })
}

/** Bulk version of useRegenerateGeneralFeedback; ungraded submissions are
 * skipped server-side (counted in `skipped`). */
export function useRegenerateGeneralFeedbacks() {
  const queryClient = useQueryClient()
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: (submissionIds: number[]) =>
      unwrap(
        client.POST('/submissions/general-feedback/regenerate', {
          params: { query: { model_id: modelId } },
          body: { submission_ids: submissionIds },
        }),
      ) as unknown as Promise<RegenerateGeneralFeedbackResult>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['submissions'] }),
  })
}

// ---- Generic-document segmentation ----

export function useSubmissionDocument(id: number | undefined, enabled = true) {
  return useQuery({
    queryKey: ['submissions', id, 'document'],
    queryFn: () =>
      unwrap(
        client.GET('/submissions/{submission_id}/document', { params: { path: { submission_id: id! } } }),
      ) as Promise<SubmissionDocument>,
    enabled: Boolean(id) && enabled,
    // The converted document never changes once it exists.
    staleTime: Infinity,
  })
}

export function useSaveSegments() {
  const queryClient = useQueryClient()
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: ({
      submissionId,
      segments,
      grade,
    }: {
      submissionId: number
      segments: AnswerSegment[]
      grade: boolean
    }) =>
      unwrap(
        client.PUT('/submissions/{submission_id}/segments', {
          params: { path: { submission_id: submissionId }, query: { model_id: modelId } },
          body: { segments, grade },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useSuggestSegments() {
  const queryClient = useQueryClient()
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: (submissionId: number) =>
      unwrap(
        client.POST('/submissions/{submission_id}/segments/suggest', {
          params: { path: { submission_id: submissionId }, query: { model_id: modelId } },
        }),
      ) as unknown as Promise<Submission>,
    onSuccess: (submission) => {
      queryClient.setQueryData(['submissions', submission.id], submission)
      queryClient.invalidateQueries({ queryKey: ['submissions'] })
    },
  })
}

export function useSuggestBatchSegments() {
  const queryClient = useQueryClient()
  const modelId = useActiveModelId()
  return useMutation({
    mutationFn: (batchId: number) =>
      unwrap(
        client.POST('/batches/{batch_id}/segments/suggest', {
          params: { path: { batch_id: batchId }, query: { model_id: modelId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['submissions'] }),
  })
}

// ---- LLM models ----
// Grading-triggering hooks above send the active model (lib/activeModel.ts)
// as `?model_id=` themselves -- callers never pass it.

export function useLlmProviders() {
  return useQuery({
    queryKey: ['llm', 'providers'],
    queryFn: () => unwrap(client.GET('/llm/providers')) as Promise<LlmProvider[]>,
  })
}

export function useLlmModels() {
  return useQuery({
    queryKey: ['llm', 'models'],
    queryFn: () => unwrap(client.GET('/llm/models')) as Promise<LlmModel[]>,
  })
}

export function useCreateLlmModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      provider: string
      model: string
      reasoning_effort: ReasoningEffort | null
      is_default: boolean
      is_favorite: boolean
    }) => unwrap(client.POST('/llm/models', { body })) as Promise<LlmModel>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['llm', 'models'] }),
  })
}

export function useUpdateLlmModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ modelId, reasoningEffort }: { modelId: number; reasoningEffort: ReasoningEffort | null }) =>
      unwrap(
        client.PATCH('/llm/models/{model_id}', {
          params: { path: { model_id: modelId } },
          body: { reasoning_effort: reasoningEffort },
        }),
      ) as Promise<LlmModel>,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['llm', 'models'] }),
  })
}

export function useSetDefaultLlmModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (modelId: number) =>
      unwrap(client.PUT('/llm/models/{model_id}/default', { params: { path: { model_id: modelId } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['llm', 'models'] }),
  })
}

export function useSetFavoriteLlmModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ modelId, favorite }: { modelId: number; favorite: boolean }) => {
      const params = { params: { path: { model_id: modelId } } }
      return unwrap(
        favorite
          ? client.PUT('/llm/models/{model_id}/favorite', params)
          : client.DELETE('/llm/models/{model_id}/favorite', params),
      )
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['llm', 'models'] }),
  })
}

export function useDeleteLlmModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (modelId: number) =>
      unwrap(client.DELETE('/llm/models/{model_id}', { params: { path: { model_id: modelId } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['llm', 'models'] }),
  })
}
