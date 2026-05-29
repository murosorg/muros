import { ReactNode } from 'react'

/**
 * Standard empty state for MurOS lists / tables.
 *
 * Pattern volontairement sobre : pas d'illustration, pas d'icone XXL,
 * juste une ligne neutre + optionnellement un bouton d'action. L'idee est
 * de ne pas occuper l'ecran avec du faux contenu, juste de dire "il n'y a
 * rien ici" et inviter a faire l'action attendue.
 *
 * Usage table (dans un <tr><td colSpan={N}>) :
 *   <tr><td colSpan={5} className="px-3 py-8"><EmptyState text="..." /></td></tr>
 *
 * Usage bloc :
 *   {items.length === 0 ? (
 *     <EmptyState text="No peer yet" action={...} />
 *   ) : (...)}
 */
type Props = {
  text: string
  action?: ReactNode
  hint?: string
  variant?: 'inline' | 'block'
  icon?: ReactNode
}

export default function EmptyState({ text, action, hint, variant = 'block', icon }: Props) {
  if (variant === 'inline') {
    return <span className="text-sm text-gray-600">{text}</span>
  }
  return (
    <div className="px-3 py-10 text-center">
      {icon && (
        <div className="inline-flex items-center justify-center w-10 h-10 rounded-full bg-gray-100 text-gray-500 mb-3">
          {icon}
        </div>
      )}
      <div className="text-sm text-gray-800 font-medium">{text}</div>
      {hint && <div className="text-xs text-gray-600 mt-1 max-w-md mx-auto">{hint}</div>}
      {action && <div className="mt-4 inline-flex">{action}</div>}
    </div>
  )
}
