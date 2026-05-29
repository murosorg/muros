import { useEffect, useState } from 'react'
import { api } from '../lib/api'

type Pending = {
  count: number
  interfaces: { id: number; name: string; type: string; ip_mode: string; ip_address: string | null }[]
  routes: { id: number; destination: string; gateway: string | null; metric: number }[]
}

/**
 * Bouton jaune "Apply" pour les pages Network et Routes.
 *
 * Convention visuelle alignee sur pfSense / OPNsense : pas de compteur
 * dans le label (les autres firewalls ne le font pas, ca devient bruyant
 * des qu'on edite plusieurs choses), juste un petit point orange a cote
 * du label quand il y a des changements en attente. Le detail des
 * changements reste accessible via le tooltip (survol).
 */
export default function ApplyNetworkButton() {
  const [pending, setPending] = useState<Pending>({ count: 0, interfaces: [], routes: [] })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const reload = async () => {
    try {
      const r = await api.network.pending()
      setPending(r as Pending)
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  useEffect(() => {
    void reload()
    const id = setInterval(reload, 3000)
    return () => clearInterval(id)
  }, [])

  const apply = async () => {
    setBusy(true); setErr(null)
    try {
      await api.network.apply()
      await reload()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const { count, interfaces, routes } = pending
  const label = busy ? 'Applying...' : 'Apply'
  const dirty = count > 0 && !busy

  const tooltip = count === 0
    ? 'No pending changes'
    : [
        `${count} pending network change(s) :`,
        ...interfaces.map((i) => `- iface ${i.name} (${i.ip_mode}${i.ip_address ? ' ' + i.ip_address : ''})`),
        ...routes.map((r) => `- route ${r.destination} via ${r.gateway || '-'} metric ${r.metric}`),
      ].join('\n')

  return (
    <div className="flex items-center gap-2">
      {err && <span className="text-xs text-red-700">{err}</span>}
      <button
        className="btn-apply relative"
        onClick={apply}
        disabled={busy || count === 0}
        title={tooltip}
      >
        {dirty && (
          <span
            className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 ring-2 ring-white"
            aria-hidden="true"
          />
        )}
        {label}
      </button>
    </div>
  )
}
