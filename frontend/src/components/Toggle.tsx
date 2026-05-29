/**
 * Toggle iOS-like reutilisable. Remplace les checkbox Activer sur les pages
 * de modules (HTTP, SSH, SNMP, VPN, Notifications, HA).
 *
 * Pattern :
 *   <Toggle checked={form.enabled} onChange={(v) => setForm({...form, enabled: v})} />
 *
 * Avec label inline :
 *   <Toggle checked={...} onChange={...} label="Notifications mail activees" />
 */
export default function Toggle({
  checked,
  onChange,
  label,
  disabled,
  size = 'md',
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label?: string
  disabled?: boolean
  size?: 'sm' | 'md'
}) {
  const trackW = size === 'sm' ? 'w-8' : 'w-10'
  const trackH = size === 'sm' ? 'h-4' : 'h-5'
  const dotSize = size === 'sm' ? 'w-3 h-3' : 'w-4 h-4'
  const dotTr = size === 'sm' ? 'translate-x-4' : 'translate-x-5'

  const toggle = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={`relative inline-flex ${trackW} ${trackH} rounded-full transition-colors ${
        checked ? 'bg-emerald-500' : 'bg-slate-300'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 ${dotSize} bg-white rounded-full shadow transition-transform ${
          checked ? dotTr : 'translate-x-0'
        }`}
      />
    </button>
  )

  if (!label) return toggle

  return (
    <label className={`inline-flex items-center gap-2 text-sm ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
      {toggle}
      <span>{label}</span>
    </label>
  )
}
