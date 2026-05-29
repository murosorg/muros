// Notifications transitoires (toasts) pour MurOS.
//
// Remplace window.alert() pour les messages courts type "Snapshot sent"
// ou "Sync failed". Affichage en bas a droite, auto-hide apres 4s pour
// les succes, 6s pour les erreurs. Pas plus de 3 toasts simultanes.
//
// API globale : `toast.success('msg')`, `toast.error('msg')`, `toast.info('msg')`.
// Le <ToastHost /> doit etre monte une fois dans App.tsx.

import { useEffect, useState } from 'react'

type Kind = 'success' | 'error' | 'info'
type Item = { id: number; kind: Kind; message: string }

type Listener = (items: Item[]) => void
const listeners = new Set<Listener>()
let items: Item[] = []
let nextId = 1

function emit() {
  for (const l of listeners) l(items)
}

function dismiss(id: number) {
  items = items.filter((x) => x.id !== id)
  emit()
}

function push(kind: Kind, message: string) {
  const item: Item = { id: nextId++, kind, message }
  items = [...items, item].slice(-3) // max 3 a l'ecran
  emit()
  const ttl = kind === 'error' ? 6000 : 4000
  window.setTimeout(() => dismiss(item.id), ttl)
}

export const toast = {
  success: (m: string) => push('success', m),
  error: (m: string) => push('error', m),
  info: (m: string) => push('info', m),
}

export function ToastHost() {
  const [list, setList] = useState<Item[]>([])
  useEffect(() => {
    listeners.add(setList)
    return () => { listeners.delete(setList) }
  }, [])
  if (list.length === 0) return null
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {list.map((t) => (
        <div
          key={t.id}
          className={
            'px-3 py-2 rounded shadow-md text-sm border flex items-start gap-3 ' +
            'animate-fadeInRight ' +
            (t.kind === 'success' ? 'bg-green-50 border-green-300 text-green-800'
             : t.kind === 'error' ? 'bg-red-50 border-red-300 text-red-800'
             : 'bg-blue-50 border-blue-200 text-blue-900')
          }
          role={t.kind === 'error' ? 'alert' : 'status'}
        >
          <span className="flex-1">{t.message}</span>
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            className="text-current opacity-50 hover:opacity-100 leading-none text-base"
            aria-label="Close"
          >
            x
          </button>
        </div>
      ))}
    </div>
  )
}
