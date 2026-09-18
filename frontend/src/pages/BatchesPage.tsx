import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useBatches, useCreateBatch, useEditions, useRubrics } from '../api/hooks'
import { BATCH_TYPE_LABELS } from '../api/types'
import type { BatchType } from '../api/types'
import { formatDateTime } from '../lib/date'

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
            </tr>
          </thead>
          <tbody>
            {batches.map((batch) => (
              <tr key={batch._id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-4 py-2">
                  <Link to={`/?batch_id=${batch._id}`} className="text-slate-900 hover:underline">
                    {batch.rubric.title}
                  </Link>
                </td>
                <td className="px-4 py-2 text-slate-500">{batch.edition.name}</td>
                <td className="px-4 py-2 text-slate-500">{BATCH_TYPE_LABELS[batch.type]}</td>
                <td className="px-4 py-2 text-slate-500">{batch.item_count}</td>
                <td className="px-4 py-2 text-slate-500">{formatDateTime(batch.created_at)}</td>
              </tr>
            ))}
            {batches.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-slate-400">
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

function UploadBatchForm() {
  const { data: rubrics } = useRubrics()
  const { data: editions } = useEditions()
  const createBatch = useCreateBatch()

  const [rubricId, setRubricId] = useState('')
  const [editionId, setEditionId] = useState('')
  const [type, setType] = useState<BatchType>(BATCH_TYPE_OPTIONS[0])
  const [file, setFile] = useState<File | null>(null)

  // Same "rubric's own edition, else the uploader must pick one" logic as
  // UploadSubmissionPage -- a batch upload asks for exactly the same two
  // things a single submission upload does (rubric implies course).
  const selectedRubric = rubrics?.find((r) => r._id === rubricId)
  const fixedEdition = selectedRubric?.edition ?? null
  const needsExplicitEdition = Boolean(selectedRubric) && !fixedEdition
  const canSubmit = Boolean(file && rubricId && (fixedEdition || editionId))

  function handleRubricChange(nextRubricId: string) {
    setRubricId(nextRubricId)
    setEditionId('')
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit || !file) return
    createBatch.mutate(
      { file, rubricId, type, editionId: needsExplicitEdition ? editionId : undefined },
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
          rubric. Each submission starts unmatched to a student until a later step links them up.
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
              <option key={rubric._id} value={rubric._id ?? ''}>
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
                  <option key={edition._id} value={edition._id ?? ''}>
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
      {createBatch.isSuccess && (
        <p className="text-sm text-emerald-600">
          Uploaded successfully: {createBatch.data.created} submission(s) created and queued for grading.
        </p>
      )}
      {createBatch.isError && (
        <p className="text-sm text-red-600">{(createBatch.error as { message?: string })?.message ?? 'Upload failed.'}</p>
      )}
    </form>
  )
}
