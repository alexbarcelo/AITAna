import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, useBatches, useCreateBatch, useDeleteBatch, useEditions, useRubrics, useSuggestBatchSegments } from '../api/hooks'
import { BATCH_TYPE_LABELS, SEGMENTED_FORMATS } from '../api/types'
import type { Batch, BatchType } from '../api/types'
import { TrashIcon } from '../components/DeleteSubmissionButton'
import LlmSegmentationCheckbox from '../components/LlmSegmentationCheckbox'
import { formatDateTime } from '../lib/date'
import { parseId } from '../lib/ids'

const BATCH_TYPE_OPTIONS = Object.keys(BATCH_TYPE_LABELS) as BatchType[]

export default function BatchesPage() {
  const { data: batches, isLoading, error } = useBatches()

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Batches</h1>
        <p className="text-sm text-slate-500">Bulk-upload a whole assignment's submissions from a single zip.</p>
      </div>

      <UploadBatchForm />

      {isLoading && <p className="text-sm text-slate-500">Loading...</p>}
      {error && <p className="text-sm text-red-600">Failed to load batches.</p>}

      {batches && (
        <table className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Rubric</th>
              <th className="px-4 py-2">Edition</th>
              <th className="px-4 py-2">Type</th>
              <th className="px-4 py-2">Items</th>
              <th className="px-4 py-2">Uploaded</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {batches.map((batch) => (
              <tr key={batch.id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-4 py-2">
                  <Link to={`/?batch_id=${batch.id}`} className="text-slate-900 hover:underline">
                    {batch.rubric.title}
                  </Link>
                </td>
                <td className="px-4 py-2 text-slate-500">{batch.edition.name}</td>
                <td className="px-4 py-2 text-slate-500">{BATCH_TYPE_LABELS[batch.type]}</td>
                <td className="px-4 py-2 text-slate-500">{batch.item_count}</td>
                <td className="px-4 py-2 text-slate-500">{formatDateTime(batch.created_at)}</td>
                <td className="px-4 py-2 text-right">
                  <span className="inline-flex items-center gap-2">
                    {SEGMENTED_FORMATS.includes(batch.rubric.format) && <SuggestSegmentsButton batch={batch} />}
                    <DeleteBatchButton batch={batch} />
                  </span>
                </td>
              </tr>
            ))}
            {batches.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-slate-400">
                  No batches uploaded yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}

/** Generic-document batches: LLM segmentation suggestion for every submission
 * not segmented yet (e.g. uploaded without LLM-assisted segmentation). */
function DeleteBatchButton({ batch }: { batch: Batch }) {
  const deleteBatch = useDeleteBatch()

  function handleClick() {
    const what = `the batch for "${batch.rubric.title}" (${batch.edition.name}, uploaded ${formatDateTime(batch.created_at)})`
    if (!window.confirm(`Delete ${what} and all ${batch.item_count} of its submissions? This can't be undone.`)) return
    deleteBatch.mutate(
      { batchId: batch.id },
      {
        onError: (err) => {
          // 409: some submissions are still being processed. Lost jobs never
          // finish, so offer to delete anyway instead of a dead end.
          if (err instanceof ApiError && err.status === 409) {
            if (window.confirm('Some submissions in this batch are still being processed (or stuck if their jobs were lost). Delete anyway?'))
              deleteBatch.mutate(
                { batchId: batch.id, force: true },
                { onError: (e) => window.alert(e.message || 'Failed to delete this batch.') },
              )
            return
          }
          window.alert(err.message || 'Failed to delete this batch.')
        },
      },
    )
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={deleteBatch.isPending}
      title="Delete batch and its submissions"
      className="inline-flex items-center rounded p-0.5 text-slate-400 hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
    >
      <TrashIcon />
    </button>
  )
}

function SuggestSegmentsButton({ batch }: { batch: Batch }) {
  const suggest = useSuggestBatchSegments()
  return (
    <span className="inline-flex items-center gap-2">
      <Link to={`/?batch_id=${batch.id}`} className="text-xs text-slate-600 hover:underline">
        submissions
      </Link>
      <button
        onClick={() => suggest.mutate(batch.id)}
        disabled={suggest.isPending}
        title="Ask the active model to suggest a segmentation for every submission in this batch that has none yet"
        className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 disabled:opacity-50"
      >
        {suggest.isSuccess ? `Suggesting ${suggest.data.suggested}` : 'Suggest segmentation'}
      </button>
    </span>
  )
}

function UploadBatchForm() {
  const { data: rubrics } = useRubrics()
  const { data: editions } = useEditions()
  const createBatch = useCreateBatch()

  const [rubricId, setRubricId] = useState('')
  const [editionId, setEditionId] = useState('')
  const [type, setType] = useState<BatchType>(BATCH_TYPE_OPTIONS[0])
  const [file, setFile] = useState<File | null>(null)
  const [llmSegmentation, setLlmSegmentation] = useState(true)
  const [matchStudents, setMatchStudents] = useState(true)

  // Same "rubric's own edition, else the uploader must pick one" logic as
  // UploadSubmissionPage -- a batch upload asks for exactly the same two
  // things a single submission upload does (rubric implies course).
  const selectedRubric = rubrics?.find((r) => String(r.id) === rubricId)
  const fixedEdition = selectedRubric?.edition ?? null
  const needsExplicitEdition = Boolean(selectedRubric) && !fixedEdition
  const canSubmit = Boolean(file && rubricId && (fixedEdition || editionId))
  const segmented = Boolean(selectedRubric && SEGMENTED_FORMATS.includes(selectedRubric.format))

  function handleRubricChange(nextRubricId: string) {
    setRubricId(nextRubricId)
    setEditionId('')
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit || !file) return
    createBatch.mutate(
      {
        file,
        rubricId: Number(rubricId),
        type,
        editionId: needsExplicitEdition ? parseId(editionId) : undefined,
        llmSegmentation,
        matchStudents,
      },
      {
        onSuccess: () => {
          setFile(null)
        },
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <div>
        <h2 className="text-sm font-medium text-slate-900">Upload a batch</h2>
        <p className="text-sm text-slate-500">
          Upload a zip with one folder per student submission -- everyone in the zip is graded against the same
          rubric. Folder names are auto-matched to roster students by name; turn that off for group projects to
          identify each submission by a label taken from its folder name instead.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-600">Rubric</label>
          <select
            required
            value={rubricId}
            onChange={(e) => handleRubricChange(e.target.value)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="" disabled>
              Select a rubric
            </option>
            {rubrics?.map((rubric) => (
              <option key={rubric.id} value={rubric.id ?? ''}>
                {rubric.title}
              </option>
            ))}
          </select>
        </div>

        {selectedRubric && (
          <div>
            <label className="block text-xs font-medium text-slate-600">Edition</label>
            {fixedEdition ? (
              <input
                disabled
                value={fixedEdition.name}
                className="mt-1 rounded-md border border-slate-300 bg-slate-50 px-2 py-1 text-sm text-slate-500"
              />
            ) : (
              <select
                required
                value={editionId}
                onChange={(e) => setEditionId(e.target.value)}
                className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="" disabled>
                  This rubric is reusable -- pick which edition
                </option>
                {editions?.map((edition) => (
                  <option key={edition.id} value={edition.id ?? ''}>
                    {edition.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-slate-600">Type</label>
          <select
            value={type}
            onChange={(e) => setType(e.target.value as BatchType)}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            {BATCH_TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {BATCH_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-600">Zip file</label>
          <input
            required
            type="file"
            accept=".zip,application/zip"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 text-sm"
          />
        </div>

        <button
          type="submit"
          disabled={!canSubmit || createBatch.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {createBatch.isPending ? 'Uploading...' : 'Upload batch'}
        </button>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" checked={matchStudents} onChange={(e) => setMatchStudents(e.target.checked)} />
        Match folders to roster students
        <span className="text-xs text-slate-400">(uncheck to label each submission by its folder name, e.g. groups)</span>
      </label>
      {segmented && <LlmSegmentationCheckbox checked={llmSegmentation} onChange={setLlmSegmentation} />}
      {createBatch.isSuccess && (
        <p className="text-sm text-emerald-600">
          Uploaded successfully: {createBatch.data.created} submission(s) created and queued for{' '}
          {createBatch.data.batch.rubric && SEGMENTED_FORMATS.includes(createBatch.data.batch.rubric.format)
            ? 'conversion -- each one needs its segmentation reviewed before grading'
            : 'grading'}
          .
        </p>
      )}
      {createBatch.isError && (
        <p className="text-sm text-red-600">{(createBatch.error as { message?: string })?.message ?? 'Upload failed.'}</p>
      )}
    </form>
  )
}
