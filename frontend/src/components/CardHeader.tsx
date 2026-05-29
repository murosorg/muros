import { ReactNode } from 'react'

/**
 * Header standard d'une card : titre a gauche, actions a droite.
 * Permet d'uniformiser le placement du bouton Apply (via
 * FormActions) en haut a droite de chaque panel.
 *
 * Usage :
 *   <div className="card">
 *     <CardHeader title="Configuration">
 *       <FormActions onApply={save} busy={busy} />
 *     </CardHeader>
 *     ...contenu...
 *   </div>
 */
type Props = {
  title: ReactNode
  children?: ReactNode
  className?: string
}

export default function CardHeader({ title, children, className = '' }: Props) {
  return (
    <div className={`flex items-center justify-between gap-3 mb-3 ${className}`}>
      <h2 className="text-lg font-semibold">{title}</h2>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  )
}
