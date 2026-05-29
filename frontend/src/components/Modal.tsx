import { ReactNode, useEffect, useRef, useState } from 'react'

type Props = {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg'
}

// Modal avec animation d'apparition/disparition sobre (opacity + scale 95
// -> 100, 150ms). Le composant reste monte pendant l'animation de sortie
// pour permettre la transition, puis se demonte une fois la 150ms ecoulee.
//
// On garde le pattern volontairement minimaliste : pas de portail, pas de
// stack de modaux empiles, juste un overlay clickable + Escape.

export default function Modal({ open, onClose, title, children, footer, size = 'md' }: Props) {
  const [mounted, setMounted] = useState(open)
  const [shown, setShown] = useState(false)
  const dialogRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (open) {
      setMounted(true)
      // requestAnimationFrame : on attend que le DOM soit pose avec
      // opacity-0 avant de basculer en opacity-100, sinon la transition
      // CSS ne s'applique pas (browser merge les classes).
      const r = requestAnimationFrame(() => {
        setShown(true)
        // Focus auto sur le premier input/textarea du modal (ou le
        // dialog lui-meme en fallback). Permet a l'admin de taper
        // directement sans avoir a cliquer dans le formulaire.
        const dlg = dialogRef.current
        if (dlg) {
          const first = dlg.querySelector<HTMLElement>(
            'input:not([readonly]):not([disabled]), textarea:not([readonly]):not([disabled]), select:not([disabled])'
          )
          ;(first || dlg).focus()
        }
      })
      return () => cancelAnimationFrame(r)
    } else {
      setShown(false)
      const t = window.setTimeout(() => setMounted(false), 150)
      return () => window.clearTimeout(t)
    }
  }, [open])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return }
      // Enter triggers the primary action button of the modal's footer
      // (Save / Apply / Confirm). Skipped when focus is in a textarea
      // (we want a real newline), on a button (default click handler
      // already handles it), inside an isolated subtree (data-no-enter),
      // or when any modifier key is pressed.
      if (
        e.key === 'Enter' &&
        !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey &&
        dialogRef.current
      ) {
        const active = document.activeElement as HTMLElement | null
        const tag = active?.tagName
        const isTextarea = tag === 'TEXTAREA'
        const isButton = tag === 'BUTTON' || (active?.getAttribute('role') === 'button')
        const isContentEditable = active?.isContentEditable
        const optedOut = active?.closest('[data-no-enter]')
        if (!isTextarea && !isButton && !isContentEditable && !optedOut) {
          // Look for the primary action button in the footer area.
          // Order of preference matches MurOS button conventions:
          // btn-apply (yellow) -> btn-primary -> btn-danger -> last
          // enabled button in the footer.
          const footer = dialogRef.current.querySelector<HTMLElement>('[data-modal-footer]')
          const scope = footer || dialogRef.current
          const buttons = Array.from(
            scope.querySelectorAll<HTMLButtonElement>('button:not([disabled])')
          )
          const primary =
            buttons.find((b) => b.classList.contains('btn-apply')) ||
            buttons.find((b) => b.classList.contains('btn-primary')) ||
            buttons.find((b) => b.classList.contains('btn-danger')) ||
            (footer ? buttons[buttons.length - 1] : null)
          if (primary) {
            e.preventDefault()
            primary.click()
            return
          }
        }
      }
      // Focus trap : si on Tab depuis le dernier element focusable, on
      // revient au premier. Pareil pour Shift+Tab depuis le premier.
      // Empeche de focuser le contenu derriere le modal.
      if (e.key === 'Tab' && dialogRef.current) {
        const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
        if (focusables.length === 0) return
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        const active = document.activeElement
        if (e.shiftKey && active === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus() }
      }
    }
    if (open) document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!mounted) return null

  const widths = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-3xl' }

  return (
    <div
      className={
        'fixed inset-0 z-50 flex items-center justify-center px-4 transition-opacity duration-150 ' +
        (shown ? 'bg-black/30 opacity-100' : 'bg-black/0 opacity-0 pointer-events-none')
      }
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className={
          `w-full ${widths[size]} bg-white border border-gray-200 rounded-md shadow-lg outline-none ` +
          `transition-all duration-150 ${shown ? 'opacity-100 scale-100' : 'opacity-0 scale-95'}`
        }
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          <button onClick={onClose} className="text-gray-700 hover:text-gray-900 text-sm">
            Close
          </button>
        </div>
        <div className="px-4 py-4 max-h-[70vh] overflow-y-auto">{children}</div>
        {footer && <div data-modal-footer className="px-4 py-3 border-t border-gray-200 flex justify-end gap-2 bg-gray-50">{footer}</div>}
      </div>
    </div>
  )
}
