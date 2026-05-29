// Composants reutilisables pour messages d'etat (erreur, succes, info, warning).
// Pattern unique pour toute l'app : couleur sobre, bordure fine, texte clair.
//
// Usage :
//   {error && <ErrorBlock message={error} />}
//   {saved && <SuccessBlock message="Conf enregistree" />}
//
// On prefere toujours ces composants a :
//   - un alert(...) natif (moche, bloquant)
//   - un <div className="text-red-..."> custom (heterogene)

import { ReactNode, useEffect } from 'react'
import { toast } from './Toast'

type Props = {
  message: string
  children?: ReactNode
  // Optional dismiss callback. When provided, a small "x" button is
  // rendered and (for SuccessBlock) the block auto-hides after
  // `autoHideMs`. Errors are not auto-dismissed by default.
  onDismiss?: () => void
  autoHideMs?: number
}

// Shared compact row layout. We keep vertical footprint minimal
// (py-1.5) because these blocks often stack with other notices in
// the same scroll viewport.
const BASE = 'flex items-start gap-3 border px-3 py-1.5 rounded text-sm'

function DismissButton({ onClick, tone }: { onClick: () => void; tone: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Dismiss"
      className={`ml-auto -mr-1 leading-none text-lg font-light opacity-60 hover:opacity-100 ${tone}`}
    >
      &times;
    </button>
  )
}

export function ErrorBlock({ message, children, onDismiss }: Props) {
  return (
    <div className={`${BASE} border-red-300 bg-red-50 text-red-800`}>
      <div className="flex-1 min-w-0">
        {message}
        {children}
      </div>
      {onDismiss && <DismissButton onClick={onDismiss} tone="text-red-800" />}
    </div>
  )
}

// Success notices are now rendered as transient toasts (bottom-right) so
// they do not take vertical space on every page. The component keeps the
// same API as before to avoid touching every caller: it just forwards the
// message to the toast host and immediately clears the parent state.
//
// If `children` are provided (rare, richer content), we fall back to the
// inline green block so we do not lose that information silently.
export function SuccessBlock({ message, children, onDismiss }: Props) {
  useEffect(() => {
    if (children) return
    toast.success(message)
    if (onDismiss) onDismiss()
    // We only fire once on mount: parent unmounts us right after.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!children) return null
  return (
    <div className={`${BASE} border-green-300 bg-green-50 text-green-800`}>
      <div className="flex-1 min-w-0">
        {message}
        {children}
      </div>
      {onDismiss && <DismissButton onClick={onDismiss} tone="text-green-800" />}
    </div>
  )
}

export function WarnBlock({ message, children, onDismiss }: Props) {
  return (
    <div className={`${BASE} border-amber-300 bg-amber-50 text-amber-900`}>
      <div className="flex-1 min-w-0">
        {message}
        {children}
      </div>
      {onDismiss && <DismissButton onClick={onDismiss} tone="text-amber-900" />}
    </div>
  )
}
