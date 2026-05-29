// Modal de confirmation unique pour MurOS.
//
// Remplace les window.confirm() natifs (moches, non-stylises, hors charte)
// et les modaux custom dupliques dans chaque page.
//
// Comportement :
//  - Fond opacifie cliquable pour fermer (annule).
//  - Touche Escape pour fermer (annule).
//  - Touche Enter pour confirmer.
//  - Focus auto sur le bouton de confirmation a l'ouverture.
//  - Si onConfirm est async, le bouton affiche 'In progress...' et est disable.

import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'

type Props = {
  open: boolean
  title: string
  message?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  // requireText : pour les actions tres destructrices (reboot, shutdown,
  // restauration de snapshot...), force l'utilisateur a taper exactement
  // ce mot avant que le bouton de confirmation ne s'active. Plus sur
  // qu'un double window.confirm() et beaucoup plus propre visuellement.
  requireText?: string
  onConfirm: () => void | Promise<void>
  onCancel: () => void
}

export default function ConfirmModal({
  open, title, message, confirmLabel, cancelLabel = 'Cancel',
  destructive = false, requireText, onConfirm, onCancel,
}: Props) {
  const dialogRef = useRef<HTMLDivElement | null>(null)
  const confirmRef = useRef<HTMLButtonElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [mounted, setMounted] = useState(open)
  const [shown, setShown] = useState(false)
  const [typed, setTyped] = useState('')

  // Quand on rouvre le modal, on vide le champ pour ne pas presenter
  // un texte deja valide d'une session precedente.
  useEffect(() => { if (open) setTyped('') }, [open])

  const textOk = !requireText || typed === requireText

  // Deferred mount/unmount to allow the exit animation (150ms).
  useEffect(() => {
    if (open) {
      setMounted(true)
      const r = requestAnimationFrame(() => setShown(true))
      return () => cancelAnimationFrame(r)
    } else {
      setShown(false)
      const t = window.setTimeout(() => setMounted(false), 150)
      return () => window.clearTimeout(t)
    }
  }, [open])

  useEffect(() => {
    if (!open) { setBusy(false); return }
    const t = window.setTimeout(() => confirmRef.current?.focus(), 30)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); if (!busy) onCancel(); return }
      if (e.key === 'Enter') { e.preventDefault(); if (!busy && textOk) doConfirm(); return }
      // Focus trap : on cycle Tab dans le modal pour empecher l'utilisateur
      // d'aller focuser des elements derriere (pas accessible, pas propre).
      if (e.key === 'Tab' && dialogRef.current) {
        const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
        if (focusables.length === 0) return
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        const active = document.activeElement
        if (e.shiftKey && active === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && active === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.clearTimeout(t)
      window.removeEventListener('keydown', onKey)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, busy])

  if (!mounted) return null

  const defaultLabel = destructive ? 'Delete' : 'Confirm'
  const label = confirmLabel || defaultLabel
  const busyLabel = destructive ? 'Deleting...' : 'In progress...'

  async function doConfirm() {
    setBusy(true)
    try { await onConfirm() } finally { setBusy(false) }
  }

  return (
    <div
      className={
        'fixed inset-0 z-50 flex items-center justify-center px-4 transition-opacity duration-150 ' +
        (shown ? 'bg-black/40 opacity-100' : 'bg-black/0 opacity-0 pointer-events-none')
      }
      onClick={() => { if (!busy) onCancel() }}
    >
      <div
        ref={dialogRef}
        className={
          'bg-white rounded-md shadow-lg max-w-md w-full p-5 transition-all duration-150 ' +
          (shown ? 'opacity-100 scale-100' : 'opacity-0 scale-95')
        }
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <h2 id="confirm-title" className="text-base font-semibold text-gray-900 mb-2">{title}</h2>
        {message && <div className="text-sm text-gray-700 mb-4">{message}</div>}
        {requireText && (
          <div className="mb-4">
            <label className="block text-xs text-gray-700 mb-1">
              To confirm, type <code className="font-mono bg-gray-100 px-1 rounded">{requireText}</code> :
            </label>
            <input
              className="input font-mono w-full"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoFocus
              autoComplete="off"
              spellCheck={false}
            />
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onCancel} disabled={busy} type="button">
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            className={destructive ? 'btn-danger' : 'btn-primary'}
            onClick={doConfirm}
            disabled={busy || !textOk}
            type="button"
          >
            {busy ? busyLabel : label}
          </button>
        </div>
      </div>
    </div>
  )
}

// Helper hook : remplace window.confirm() par une API Promise.
//
// Usage :
//   const { confirm, ConfirmHost } = useConfirm()
//   ...
//   <ConfirmHost />
//   ...
//   const ok = await confirm({ title: 'Delete ?', destructive: true })
//   if (!ok) return
//
// Equivalent stylise de `if (!window.confirm('...')) return`.

type AskOpts = {
  title: string
  message?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  requireText?: string
}

type State = AskOpts & {
  open: boolean
  resolve: ((v: boolean) => void) | null
}

export function useConfirm() {
  const [state, setState] = useState<State>({
    open: false, title: '', resolve: null,
  })

  const confirm = useCallback((opts: AskOpts): Promise<boolean> => {
    return new Promise((resolve) => {
      setState({ ...opts, open: true, resolve })
    })
  }, [])

  const onConfirm = useCallback(() => {
    state.resolve?.(true)
    setState((s) => ({ ...s, open: false, resolve: null }))
  }, [state])

  const onCancel = useCallback(() => {
    state.resolve?.(false)
    setState((s) => ({ ...s, open: false, resolve: null }))
  }, [state])

  const ConfirmHost = useMemo(() => {
    return () => (
      <ConfirmModal
        open={state.open}
        title={state.title}
        message={state.message}
        confirmLabel={state.confirmLabel}
        cancelLabel={state.cancelLabel}
        destructive={state.destructive}
        requireText={state.requireText}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    )
  }, [state, onConfirm, onCancel])

  return { confirm, ConfirmHost }
}
