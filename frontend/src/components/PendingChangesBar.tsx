import { useEffect, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { api } from '../lib/api'

// Barre flottante affichee en bas de page quand il y a des changements
// reseau en attente d'apply. Discrete (slide-in), pas intrusive : un
// resume du nombre + 2 boutons (Review/Apply). Polls every 15s.
//
// Source de verite : /api/network/pending qui agrege les interfaces +
// routes avec dirty=true (et pending_delete=true pour les VLAN). Les
// rules nft ont leur propre apply via la page Rules (preview puis safe
// apply avec rollback), donc on ne les compte pas ici pour ne pas
// dupliquer le bouton.
//
// Cache la barre sur les pages qui ont deja un bouton Apply visible.
export default function PendingChangesBar() {
  const navigate = useNavigate()
  const location = useLocation()
  const [count, setCount] = useState(0)
  const [details, setDetails] = useState<{ ifaces: number; routes: number }>({ ifaces: 0, routes: 0 })
  const [applying, setApplying] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let stopped = false
    const tick = async () => {
      try {
        const r = await api.network.pending()
        if (stopped) return
        setCount(r.count)
        setDetails({ ifaces: r.interfaces?.length ?? 0, routes: r.routes?.length ?? 0 })
      } catch { /* silent */ }
    }
    tick()
    const id = window.setInterval(tick, 15000)
    return () => { stopped = true; window.clearInterval(id) }
  }, [location.pathname])

  const hide = location.pathname === '/login'
    || location.pathname === '/network'
    || location.pathname === '/routes'
    || location.pathname.startsWith('/firewall')
  if (hide || count === 0) return null

  const onApply = async () => {
    setApplying(true); setErr(null)
    try {
      const r = await api.network.apply()
      if (r.errors?.length) {
        setErr(r.errors.slice(0, 3).join(' . '))
      } else {
        const r2 = await api.network.pending()
        setCount(r2.count)
      }
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setApplying(false)
    }
  }

  const parts: string[] = []
  if (details.ifaces) parts.push(details.ifaces + ' interface' + (details.ifaces > 1 ? 's' : ''))
  if (details.routes) parts.push(details.routes + ' route' + (details.routes > 1 ? 's' : ''))

  return (
    <div className="fixed bottom-0 left-60 right-0 z-40 pointer-events-none">
      <div className="mx-auto max-w-5xl px-4 pb-3 pointer-events-auto">
        <div className="bg-amber-50 border border-amber-300 rounded-lg shadow-lg px-4 py-2.5 flex items-center gap-3 flex-wrap">
          <span className="inline-flex items-center gap-2">
            <span className="relative inline-flex w-2 h-2">
              <span className="absolute inset-0 rounded-full bg-amber-500 opacity-60 animate-ping" />
              <span className="relative inline-flex rounded-full w-2 h-2 bg-amber-500" />
            </span>
            <strong className="text-sm text-amber-900">
              {count} pending change{count > 1 ? 's' : ''}
            </strong>
          </span>
          {parts.length > 0 && (
            <span className="text-xs text-amber-800">({parts.join(', ')})</span>
          )}
          {err && (
            <span className="text-xs text-red-700 font-mono truncate max-w-xs" title={err}>{err}</span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              className="text-xs text-amber-900 hover:text-amber-700 underline"
              onClick={() => navigate('/network')}
            >Review</button>
            <button
              className="btn-primary py-1 text-xs"
              onClick={onApply}
              disabled={applying}
            >{applying ? 'Applying...' : 'Apply now'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}
