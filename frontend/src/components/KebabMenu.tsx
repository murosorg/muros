import { useEffect, useRef, useState } from 'react'

type Item = {
  label: string
  onClick: () => void
  destructive?: boolean
  hint?: string
  disabled?: boolean
}

// Bouton trois-points (kebab) qui deroule un menu d'actions secondaires.
// Utilise pour les actions rares ou avancees qu'on ne veut pas mettre en
// gros bouton sur la page (ex: Re-import kernel, Reset, etc.).
export default function KebabMenu({ items }: { items: Item[] }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDoc)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDoc)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="px-2 py-1 rounded text-gray-700 hover:bg-gray-100 text-lg leading-none"
        onClick={() => setOpen((v) => !v)}
        aria-label="More actions"
        title="More actions"
      >⋮</button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-30 min-w-[200px] bg-white border border-gray-200 rounded shadow-lg py-1">
          {items.map((it, idx) => (
            <button
              key={idx}
              className={`block w-full text-left px-3 py-1.5 text-sm ${it.destructive ? 'text-red-700 hover:bg-red-50' : 'text-gray-800 hover:bg-gray-100'} ${it.disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
              onClick={() => { if (it.disabled) return; setOpen(false); it.onClick() }}
              disabled={it.disabled}
              title={it.hint}
            >{it.label}</button>
          ))}
        </div>
      )}
    </div>
  )
}
