import { ReactNode, useEffect, useState } from 'react'

// Bloc d'info persistant entre sessions via localStorage, dismissable.
// Utilise pour aider les newbies sur les pages vides sans polluer apres
// que l'admin a compris.
export default function DismissibleNote({ id, children, variant = 'info' }: {
  id: string
  children: ReactNode
  variant?: 'info' | 'tip'
}) {
  const key = 'muros.note.dismissed.' + id
  const [dismissed, setDismissed] = useState(true)
  useEffect(() => {
    setDismissed(localStorage.getItem(key) === '1')
  }, [key])
  if (dismissed) return null
  const colorCls = variant === 'tip'
    ? 'bg-sky-50 border-sky-200 text-sky-900'
    : 'bg-slate-50 border-slate-200 text-gray-800'
  return (
    <div className={`border rounded px-3 py-2 text-xs flex items-start gap-3 ${colorCls}`}>
      <div className="flex-1">{children}</div>
      <button
        type="button"
        className="text-gray-500 hover:text-gray-800 shrink-0"
        onClick={() => { localStorage.setItem(key, '1'); setDismissed(true) }}
        aria-label="Dismiss"
        title="Dismiss"
      >×</button>
    </div>
  )
}
