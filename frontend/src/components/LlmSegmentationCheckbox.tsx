/**
 * Upload-form option for generic-document rubrics (`SEGMENTED_FORMATS`): ask
 * the active model where each answer starts right after conversion. Either
 * way nothing is graded until a professor confirms the segmentation.
 */
export default function LlmSegmentationCheckbox({
  checked,
  onChange,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <div>
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        LLM-assisted segmentation
      </label>
      <p className="mt-1 text-xs text-slate-500">
        Generic document: it's converted to text, then split into one answer per question. With this on, the active
        model suggests the split. Either way, you review it before anything is graded.
      </p>
    </div>
  )
}
