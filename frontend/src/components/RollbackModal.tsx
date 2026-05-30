// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useRef, useState } from 'react'
import { api, ApplyStatus, PendingChange } from '../lib/api'

/**
 * Modale unique de confirmation post-apply pour tous les changements
 * sensibles (firewall, interface, route, vlan, http, ssh, tls).
 *
 * Sources surveillees (poll 2s) :
 *  - api.apply.status()        -> ruleset nftables
 *  - api.pending.list()        -> safe_apply (in-memory : interface/route/vlan)
 *  - api.pendingApply.list()   -> pending_apply (DB : http/ssh/tls/interface/route)
 *
 * Default timer = the configurable `apply_confirm_timeout` setting (60s
 * by default; DEFAULT_TIMEOUT_SECONDS = 60 in the backend rollback
 * manager). The TOTAL duration of each source is read from its
 * `timeout_seconds` field returned by the API: the progress bar and the
 * danger zone are based on that value, so a custom timeout is always
 * represented correctly by the UI.
 */

type Source =
  | { kind: 'nft'; key: string; expires_at: string; total: number; dryRun: boolean }
  | { kind: 'pending'; key: string; expires_at: string; total: number; change: PendingChange }
  | { kind: 'apply'; key: string; expires_at: string; total: number; id: number; applyType: string; summary: string | null }

type PendingApplyItem = {
  id: number
  apply_type: string
  status: string
  summary: string | null
  expires_at: string
  timeout_seconds: number
}

export default function RollbackModal() {
  const [status, setStatus] = useState<ApplyStatus | null>(null)
  const [pending, setPending] = useState<PendingChange[]>([])
  const [pendingApply, setPendingApply] = useState<PendingApplyItem[]>([])
  const [now, setNow] = useState(Date.now())
  const [working, setWorking] = useState(false)
  const dismissedRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const [s, p, pa] = await Promise.all([
          api.apply.status(),
          api.pending.list(),
          api.pendingApply.list().catch(() => [] as PendingApplyItem[]),
        ])
        if (!cancelled) { setStatus(s); setPending(p); setPendingApply(pa) }
      } catch { /* ignore */ }
    }
    tick()
    const interval = setInterval(tick, 2000)
    const clock = setInterval(() => setNow(Date.now()), 500)
    return () => { cancelled = true; clearInterval(interval); clearInterval(clock) }
  }, [])

  const sources: Source[] = []
  if (status && status.state === 'pending' && status.expires_at) {
    // Chaque source porte son propre timeout_seconds : c'est la duree totale
    // de la fenetre de confirmation. Le pct est calcule par rapport a ce
    // total-LA, pas une constante front, sinon le ruleset et une autre
    // source avec un timeout different auraient des barres incoherentes.
    sources.push({
      kind: 'nft', key: 'nft',
      expires_at: status.expires_at as string,
      total: status.timeout_seconds || 60,
      dryRun: !!status.dry_run,
    })
  }
  for (const c of pending) {
    if (c.state === 'pending') {
      sources.push({
        kind: 'pending', key: 'p' + c.id,
        expires_at: c.expires_at,
        total: c.timeout_seconds || 60,
        change: c,
      })
    }
  }
  for (const a of pendingApply) {
    if (a.status === 'pending') {
      sources.push({
        kind: 'apply', key: 'a' + a.id,
        expires_at: a.expires_at,
        total: a.timeout_seconds || 60,
        id: a.id, applyType: a.apply_type, summary: a.summary,
      })
    }
  }

  const current = sources.find((s) => !dismissedRef.current.has(s.key)) || null
  if (!current) return null

  const remaining = Math.max(0, Math.floor((new Date(current.expires_at).getTime() - now) / 1000))
  // Echelle = duree TOTALE de la fenetre, lue depuis le backend par source.
  // Garantit que la barre est coherente meme si certains flux ont des
  // timeouts differents (60s par defaut MurOS, mais le schema firewall.py
  // accepte 10..600 si un admin veut customiser).
  const pct = Math.max(0, Math.min(100, (remaining / current.total) * 100))
  const danger = remaining <= Math.min(3, Math.ceil(current.total * 0.3))

  const kindLabel = labelFor(current)
  const description = descFor(current)
  const reconnectUrls = reconnectUrlsFor(current)

  const onConfirm = async () => {
    setWorking(true)
    try {
      if (current.kind === 'nft') {
        setStatus(await api.apply.confirm())
      } else if (current.kind === 'pending') {
        const u = await api.pending.confirm(current.change.id)
        setPending((cur) => cur.map((c) => c.id === current.change.id ? u : c))
      } else {
        await api.pendingApply.confirm(current.id)
        setPendingApply((cur) => cur.map((a) => a.id === current.id ? { ...a, status: 'confirmed' } : a))
      }
      dismissedRef.current.add(current.key)
    } finally { setWorking(false) }
  }

  const onRollback = async () => {
    setWorking(true)
    try {
      if (current.kind === 'nft') {
        setStatus(await api.apply.rollback())
      } else if (current.kind === 'pending') {
        const u = await api.pending.rollback(current.change.id)
        setPending((cur) => cur.map((c) => c.id === current.change.id ? u : c))
      } else {
        await api.pendingApply.rollback(current.id)
        setPendingApply((cur) => cur.map((a) => a.id === current.id ? { ...a, status: 'rolled_back' } : a))
      }
      dismissedRef.current.add(current.key)
    } finally { setWorking(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-lg shadow-2xl w-full max-w-lg p-6">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-semibold text-gray-900">Confirm change</h3>
          <span className="text-[10px] uppercase tracking-wider bg-amber-100 text-amber-900 px-1.5 py-0.5 rounded font-semibold border border-amber-200">{kindLabel}</span>
        </div>

        <p className="text-sm text-gray-700 mb-3">{description}</p>

        {reconnectUrls.length > 0 && (
          <div className="text-xs text-gray-800 bg-amber-50 border border-amber-200 px-3 py-2 rounded mb-3">
            <div className="font-medium mb-1">Reconnect to the interface:</div>
            <ul className="space-y-1">
              {reconnectUrls.map((u) => (
                <li key={u}>
                  <a
                    href={u}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono text-blue-700 hover:text-blue-900 underline break-all"
                  >
                    {u}
                  </a>
                </li>
              ))}
            </ul>
            <div className="mt-1 text-gray-700">
              If the admin IP or port changed, open one of these URLs in another tab to confirm from the new address before the countdown ends.
            </div>
          </div>
        )}

        <div className="mb-4">
          <div className="flex items-center justify-between text-xs text-gray-600 mb-1">
            <span>Automatic rollback in</span>
            <span className={`font-mono font-semibold ${danger ? 'text-red-700' : 'text-amber-700'}`}>{remaining}s</span>
          </div>
          <div className="w-full h-2 bg-gray-100 rounded overflow-hidden">
            <div
              className={`h-full transition-all duration-500 ${danger ? 'bg-red-500' : 'bg-amber-500'}`}
              style={{ width: pct + '%' }}
            />
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onRollback} disabled={working}>Cancel (rollback)</button>
          <button className="btn-primary" onClick={onConfirm} disabled={working}>
            {working ? 'In progress...' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  )
}

function labelFor(s: Source): string {
  if (s.kind === 'nft') return 'nftables'
  if (s.kind === 'pending') {
    const m: Record<string, string> = { interface: 'interface', route: 'route', vlan: 'vlan' }
    return m[s.change.kind] || s.change.kind
  }
  return s.applyType
}

function descFor(s: Source): string {
  if (s.kind === 'nft') {
    return 'nftables ruleset applied' + (s.dryRun ? ' (dry-run)' : '')
      + ". Check that access still works before confirming."
  }
  if (s.kind === 'pending') {
    return s.change.description
      + ". If you do not confirm, the previous state will be restored automatically."
  }
  const summaries: Record<string, string> = {
    http: "New HTTP config applied in nginx. Check that the UI is still reachable.",
    ssh: "New SSH config applied. Check that an SSH session still opens.",
    tls: "New TLS certificate in place. Check that the UI still answers over HTTPS.",
    interface: "New interface config applied. Check connectivity.",
    route: "New route applied. Check connectivity.",
  }
  const summary = s.summary ? ` (${s.summary})` : ''
  return (summaries[s.applyType] || `Change ${s.applyType} applied${summary}.`)
    + " Without confirmation, the old config will be restored automatically."
}

// Reconstruit la (les) URL(s) HTTPS sur lesquelles l'admin doit basculer
// si le changement applique a affecte l'IP ou le port d'administration.
// Strategie :
//  - Apply HTTP : on parse le summary qui a le format "IP:PORT HTTPS"
//    (cf. nginx_config). On bascule en https://IP:PORT, et si l'IP est
//    0.0.0.0 (any interfaces) on retombe sur l'hostname courant.
//  - Pending d'interface (safe_apply) : on lit detail.new_ips (IPs sans
//    CIDR) et on garde le port HTTPS courant (lu sur l'URL active).
// Si rien n'est detectable on renvoie une liste vide (pas de banniere).
function reconnectUrlsFor(s: Source): string[] {
  const currentPort = window.location.port
    || (window.location.protocol === 'https:' ? '443' : '80')

  if (s.kind === 'apply' && s.applyType === 'http' && s.summary) {
    // Format attendu : "<ip>:<port> HTTPS"
    const m = s.summary.match(/^([^:\s]+):(\d+)/)
    if (m) {
      const ip = m[1]
      const port = m[2]
      const host = ip === '0.0.0.0' || ip === '::' ? window.location.hostname : ip
      return [buildUrl(host, port)]
    }
  }

  if (s.kind === 'pending' && s.change.kind === 'interface') {
    const ips = (s.change.detail?.new_ips as string[] | undefined) || []
    if (ips.length) {
      return ips.map((ip) => buildUrl(ip, currentPort))
    }
  }

  if (s.kind === 'apply' && s.applyType === 'interface' && s.summary) {
    // Le summary peut contenir une liste d'IPs, on tente une extraction.
    const ips = Array.from(s.summary.matchAll(/\b(\d{1,3}(?:\.\d{1,3}){3})\b/g)).map((m) => m[1])
    if (ips.length) return ips.map((ip) => buildUrl(ip, currentPort))
  }
  return []
}

function buildUrl(host: string, port: string): string {
  const scheme = 'https'
  // IPv6 -> [host]
  const h = host.includes(':') ? `[${host}]` : host
  const portPart = (scheme === 'https' && port === '443') ? '' : `:${port}`
  return `${scheme}://${h}${portPart}/`
}
