import { ReactNode } from 'react'

/**
 * Barre d'actions standard, designee pour etre placee dans le header
 * d'une card (a cote du titre h2) sur any les pages MurOS.
 *
 * Convention : bouton primary 'Apply' (busy: 'Applying...') a
 * droite, boutons secondaires (Cancel, Tester, ...) a sa gauche.
 *
 * Usage standard (en header de card) :
 *
 *   <div className="card">
 *     <CardHeader title="Configuration">
 *       <FormActions onApply={save} busy={busy} />
 *     </CardHeader>
 *     ...contenu...
 *   </div>
 *
 * Le composant n'ajoute nonee separation visuelle, c'est le CardHeader
 * (ou le parent) qui gere la mise en page.
 */
type Props = {
  onApply: () => void
  busy?: boolean
  disabled?: boolean
  label?: string
  busyLabel?: string
  extra?: ReactNode
  /**
   * When true, an orange dot is rendered on the Apply button to signal
   * that the form has uncommitted changes. Visual contract aligned with
   * ApplyNetworkButton so every page in MurOS uses the same affordance.
   */
  dirty?: boolean
  /**
   * Optional tooltip text shown on hover (typically lists the pending
   * changes when `dirty` is true).
   */
  title?: string
}

export default function FormActions({
  onApply,
  busy = false,
  disabled = false,
  label = 'Apply',
  busyLabel = 'Applying...',
  extra,
  dirty = false,
  title,
}: Props) {
  return (
    <div className="flex items-center gap-2">
      {extra}
      <button
        type="button"
        className="btn-apply relative"
        onClick={onApply}
        disabled={busy || disabled}
        title={title ?? (
          busy
            ? undefined
            : disabled
              ? 'Apply is unavailable. Resolve the highlighted form errors first.'
              : dirty
                ? 'Unsaved changes. Click Apply to commit.'
                : 'No pending changes. Edit a setting to enable Apply.'
        )}
      >
        {dirty && !busy && (
          <span
            className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 ring-2 ring-white"
            aria-hidden="true"
          />
        )}
        {busy ? busyLabel : label}
      </button>
    </div>
  )
}
