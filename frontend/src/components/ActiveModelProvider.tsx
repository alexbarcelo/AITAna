import { useMemo, useState, type ReactNode } from 'react'
import { useLlmModels } from '../api/hooks'
import { ActiveModelContext, resolveActiveModel } from '../lib/activeModel'

const STORAGE_KEY = 'aitana.activeModelId'

// Storage can be unavailable (private window, blocked site data) -- the
// active model then just doesn't survive a reload, falling back to the default.
function readStoredId(): number | null {
  try {
    const stored = Number(localStorage.getItem(STORAGE_KEY))
    return Number.isInteger(stored) && stored > 0 ? stored : null
  } catch {
    return null
  }
}

function writeStoredId(id: number | null) {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, String(id))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // see readStoredId
  }
}

export default function ActiveModelProvider({ children }: { children: ReactNode }) {
  const { data: models } = useLlmModels()
  const [storedId, setStoredId] = useState(readStoredId)

  const value = useMemo(
    () => ({
      models,
      activeModel: resolveActiveModel(models, storedId),
      setActiveModelId: (id: number | null) => {
        writeStoredId(id)
        setStoredId(id)
      },
    }),
    [models, storedId],
  )

  return <ActiveModelContext.Provider value={value}>{children}</ActiveModelContext.Provider>
}
