import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { LlmModel } from '../api/types'
import { modelLabel, useActiveModel } from '../lib/activeModel'

/** Header "active model" picker: favorites first, then everything else.
 * Managing the list (add, default, favorite, delete) lives on /models. */
export default function ModelSelector() {
  const { models, activeModel, setActiveModelId } = useActiveModel()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(e: PointerEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const favorites = models?.filter((m) => m.is_favorite) ?? []
  const others = models?.filter((m) => !m.is_favorite) ?? []

  function pick(model: LlmModel) {
    setActiveModelId(model.id)
    setOpen(false)
  }

  return (
    <div ref={rootRef} className="relative w-full max-w-md">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-md border border-slate-200 px-2.5 py-1.5 text-left text-sm hover:bg-slate-50"
      >
        <span className="text-xs text-slate-500">Model</span>
        {activeModel ? (
          <span className="flex-1 truncate font-medium">{modelLabel(activeModel)}</span>
        ) : (
          <span className="flex-1 truncate font-medium text-amber-700">{models ? 'none set' : '...'}</span>
        )}
        <span className="text-slate-400">▾</span>
      </button>

      {open && (
        <div role="menu" className="absolute right-0 z-20 mt-1 max-h-[70vh] w-full overflow-y-auto rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
          {models && models.length === 0 && (
            <p className="px-3 py-2 text-sm text-slate-500">No models yet -- add one to start grading.</p>
          )}
          {favorites.length > 0 && <ModelGroup title="Favorites" models={favorites} active={activeModel} onPick={pick} />}
          {others.length > 0 && (
            <ModelGroup title={favorites.length > 0 ? 'Other models' : 'Models'} models={others} active={activeModel} onPick={pick} />
          )}
          <div className="mt-1 border-t border-slate-100 pt-1">
            <Link
              to="/models"
              onClick={() => setOpen(false)}
              className="block rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
            >
              Manage models...
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}

function ModelGroup({
  title,
  models,
  active,
  onPick,
}: {
  title: string
  models: LlmModel[]
  active?: LlmModel
  onPick: (model: LlmModel) => void
}) {
  return (
    <div className="py-1">
      <p className="px-3 py-1 text-xs font-medium uppercase text-slate-400">{title}</p>
      {models.map((model) => (
        <button
          key={model.id}
          type="button"
          role="menuitemradio"
          aria-checked={model.id === active?.id}
          disabled={!model.available}
          title={model.unavailable_reason ?? undefined}
          onClick={() => onPick(model)}
          className={`flex w-full items-start gap-2 rounded-md px-3 py-1.5 text-left disabled:cursor-not-allowed disabled:opacity-40 ${
            model.id === active?.id ? 'bg-slate-100' : 'hover:bg-slate-50'
          }`}
        >
          <span className="w-3 pt-0.5 text-sm text-slate-700">{model.id === active?.id ? '✓' : ''}</span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5 text-xs text-slate-500">
              {model.provider}
              {model.reasoning_effort && (
                <span className="rounded bg-slate-100 px-1.5 text-slate-500">effort: {model.reasoning_effort}</span>
              )}
              {model.is_default && <span className="rounded bg-slate-100 px-1.5 text-slate-500">default</span>}
            </span>
            <span className={`block break-all font-mono text-sm ${model.id === active?.id ? 'font-semibold' : ''}`}>
              {model.model}
            </span>
            {!model.available && <span className="block text-xs text-amber-700">{model.unavailable_reason}</span>}
          </span>
        </button>
      ))}
    </div>
  )
}
