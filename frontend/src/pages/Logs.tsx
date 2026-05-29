import React, { useEffect, useMemo, useState, useCallback } from 'react'
import TableSkeleton from '../components/TableSkeleton'
import { Link } from 'react-router-dom'
import { api, FirewallLogEntry, LogsStatus, type AuditLogEntry, type SystemLogEntry } from '../lib/api'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { ErrorBlock } from '../components/Alerts'
import { fmt } from '../lib/format'
import { ScrollText, Inbox, RefreshCw } from 'lucide-react'

type Scope = 'muros' | 'kernel'
type View = 'firewall' | 'audit' | 'system'

// Underline tab strip shared by the three log views. Same style as the
// IPsec page so the section navigation feels consistent across MurOS.
const LOG_VIEWS: { key: View; label: string }[] = [
  { key: 'firewall', label: 'Firewall drops' },
  { key: 'audit',    label: 'Web actions' },
  { key: 'system',   label: 'System journal' },
]

function LogsTabs({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  return (
    <div className="flex border-b border-gray-200">
      {LOG_VIEWS.map((t) => {
        const active = view === t.key
        return (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            className={`px-3 py-2 text-sm -mb-px border-b-2 transition-colors ${
              active
                ? 'border-steel-400 text-gray-900 font-medium'
                : 'border-transparent text-gray-600 hover:text-gray-900'
            }`}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

// Small "Auto-refresh (3s) | Reload" cluster placed in PageHeader.status
// slot. Works for any view; the parent supplies its own load().
function RefreshControls({
  autoRefresh, onAutoRefreshChange, onReload, busy,
}: {
  autoRefresh: boolean
  onAutoRefreshChange: (v: boolean) => void
  onReload: () => void
  busy: boolean
}) {
  return (
    <div className="flex items-center gap-3">
      <label className="inline-flex items-center gap-1.5 text-xs text-gray-700 cursor-pointer select-none">
        <input
          type="checkbox"
          className="accent-steel-500"
          checked={autoRefresh}
          onChange={(e) => onAutoRefreshChange(e.target.checked)}
        />
        Auto-refresh
        <span className="text-gray-400">(3s)</span>
      </label>
      <button
        className="btn-secondary inline-flex items-center gap-1.5"
        onClick={onReload}
        disabled={busy}
        title="Reload now"
      >
        <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
        Reload
      </button>
    </div>
  )
}

export default function Logs() {
  const [view, setView] = useState<View>('firewall')

  return (
    <div>
      <PageHeader
        icon={<ScrollText size={16} />}
        title="Logs"
        description={
          view === 'firewall' ? 'Packets logged by nftables.' :
          view === 'audit'    ? 'Write actions performed through the UI.' :
                                'Backend service logs.'
        }
        titleHelp={view === 'firewall'
          ? 'Action and rule are extracted from the nft prefix [muros <ACTION> r=<ID> <CHAIN>]. The Packet column summarises protocol, source and destination. Filter uses journalctl -g regex syntax.'
          : undefined}
      />

      <div className="px-6 pt-3 pb-4 space-y-4">
        <LogsTabs view={view} onChange={setView} />
        {view === 'firewall' && <FirewallView />}
        {view === 'audit'    && <AuditView />}
        {view === 'system'   && <SystemView />}
      </div>
    </div>
  )
}

function FirewallView() {
  const [entries, setEntries] = useState<FirewallLogEntry[]>([])
  const [status, setStatus] = useState<LogsStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [scope, setScope] = useState<Scope>('muros')
  // Auto-refresh: re-run load() every 3s while the checkbox is on.
  // Default off so we do not hammer journalctl. The reload button
  // stays alongside for one-shot refresh between ticks.
  const [autoRefresh, setAutoRefresh] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [res, st] = await Promise.all([
        api.logs.firewall(500, search || undefined, scope),
        api.logs.status(),
      ])
      setEntries(res)
      setStatus(st)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [search, scope])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [autoRefresh, load])

  const formatTime = (iso: string) => {
    if (!iso) return ''
    try { return fmt.datetime(iso) }
    catch { return iso }
  }

  const messageClass = (m: string) => {
    if (/DROP|drop/.test(m)) return 'text-red-700'
    if (/REJECT|reject/.test(m)) return 'text-amber-700'
    if (/ACCEPT|accept/.test(m)) return 'text-emerald-700'
    return 'text-gray-900'
  }

  // Readable summary of a kernel nftables line. We extract
  // IN/OUT/SRC/DST/PROTO/SPT/DPT and keep the rest for the "details"
  // tooltip.
  const summarizePacket = (raw: string): { summary: string; details: string } => {
    const pick = (re: RegExp) => {
      const m = raw.match(re)
      return m ? m[1] : null
    }
    const inIf = pick(/\bIN=(\S*)/)
    const outIf = pick(/\bOUT=(\S*)/)
    const src = pick(/\bSRC=(\S+)/)
    const dst = pick(/\bDST=(\S+)/)
    const proto = pick(/\bPROTO=(\S+)/)
    const spt = pick(/\bSPT=(\d+)/)
    const dpt = pick(/\bDPT=(\d+)/)
    if (!src || !dst) {
      return { summary: raw, details: raw }
    }
    const ifPart = (inIf || outIf)
      ? `${inIf ? 'in=' + (inIf || '-') : ''}${inIf && outIf ? ' ' : ''}${outIf ? 'out=' + outIf : ''}`
      : ''
    const protoPart = proto || 'ip'
    const srcPart = spt ? `${src}:${spt}` : src
    const dstPart = dpt ? `${dst}:${dpt}` : dst
    const summary = `${protoPart} ${srcPart} -> ${dstPart}${ifPart ? '  (' + ifPart + ')' : ''}`
    return { summary, details: raw }
  }

  const actionBadge = (a: string | null) => {
    if (!a) return null
    const cls = a === 'DROP'   ? 'bg-red-100 text-red-800 border-red-200'
             : a === 'REJECT' ? 'bg-amber-100 text-amber-800 border-amber-200'
             : a === 'ACCEPT' ? 'bg-emerald-100 text-emerald-800 border-emerald-200'
             :                  'bg-gray-100 text-gray-700 border-gray-200'
    return (
      <span className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border ${cls}`}>
        {a}
      </span>
    )
  }

  const empty = !loading && entries.length === 0
  const noRulesLogging = status && status.rules_with_log_enabled === 0
  const needRoot = status && status.journalctl_available && !status.is_root

  return (
    <div className="card space-y-3">
      {/* Toolbar: scope toggle on the left, search + quick chips in the
          middle, refresh controls + count on the right. Single row so
          everything related to the table stays adjacent. */}
      <div className="flex items-center gap-2 flex-wrap">
        <select
          className="select w-auto py-1.5 text-xs"
          value={scope}
          onChange={(e) => setScope(e.target.value as Scope)}
          title="Source: MurOS-tagged rules only, or the full kernel firewall log"
        >
          <option value="muros">Source: MurOS rules</option>
          <option value="kernel">Source: Full kernel</option>
        </select>
        <input
          className="input flex-1 min-w-[220px] py-1.5"
          placeholder="Filter (regex, e.g. DROP|REJECT)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') load() }}
        />
        {/* Quick filter chips: clicking sets the regex, an active chip is
            highlighted so the user knows the filter is in effect. */}
        <div className="flex gap-1">
          {([
            { label: 'drops',   value: 'DROP|REJECT' },
            { label: 'accepts', value: 'ACCEPT' },
          ] as const).map((c) => {
            const active = search === c.value
            return (
              <button
                key={c.label}
                onClick={() => setSearch(active ? '' : c.value)}
                className={`text-xs px-2 py-1 rounded-full border transition-colors ${
                  active
                    ? 'bg-steel-100 border-steel-400 text-gray-900 font-medium'
                    : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
                }`}
              >
                {c.label}
              </button>
            )
          })}
          {search && search !== 'DROP|REJECT' && search !== 'ACCEPT' && (
            <button className="btn-ghost py-1 text-xs" onClick={() => setSearch('')} title="Clear filter">
              clear
            </button>
          )}
        </div>
        <span className="ml-auto inline-flex items-center gap-3">
          <span className="text-[11px] text-gray-500 whitespace-nowrap">
            {entries.length} {entries.length === 1 ? 'entry' : 'entries'}
          </span>
          <RefreshControls
            autoRefresh={autoRefresh}
            onAutoRefreshChange={setAutoRefresh}
            onReload={load}
            busy={loading}
          />
        </span>
      </div>

      {/* Contextual banners, prioritised so we only show one at a time. */}
      {status && !status.journalctl_available && (
        <ErrorBlock message="journalctl not found on this system. Logs cannot be read." />
      )}
      {status && status.journalctl_available && needRoot && (
        <div className="border border-amber-300 bg-amber-50 rounded px-3 py-1.5 text-xs text-amber-900">
          API not running as root, <code>journalctl -k</code> may return empty.
          Check that <code>muros-backend</code> service runs as root.
        </div>
      )}
      {empty && noRulesLogging && scope === 'muros' && (
        <div className="border border-amber-300 bg-amber-50 rounded px-3 py-1.5 text-xs text-amber-900">
          No rule has the <em>log</em> checkbox enabled. Edit a rule in{' '}
          <Link to="/rules" className="underline font-medium">Rules</Link>{' '}
          or switch to <em>Full kernel</em>.
        </div>
      )}
      {empty && !noRulesLogging && scope === 'muros' && (
        <div className="border border-gray-200 bg-gray-50 rounded px-3 py-1.5 text-xs text-gray-800">
          {status?.rules_with_log_enabled} rule(s) with log enabled but no packet matched yet.
        </div>
      )}

      {error && <ErrorBlock message={error} />}

      <LogTable
        columns={[
          { label: 'Timestamp', width: 'w-44' },
          { label: 'Action', width: 'w-20' },
          { label: 'Rule', width: 'w-20' },
          { label: 'Packet' },
        ]}
        emptyIcon={<ScrollText size={20} />}
        emptyText="No entry"
        emptyHint="Enable log on a rule in Rules, or switch to Full kernel."
        loading={loading}
        empty={empty}
        body={entries.map((e, idx) => {
          const { summary, details } = summarizePacket(e.message)
          return (
            <tr key={idx} className="border-t border-gray-200 hover:bg-gray-50 align-top">
              <td className="px-3 py-1.5 font-mono text-xs text-gray-700 whitespace-nowrap">{formatTime(e.timestamp)}</td>
              <td className="px-3 py-1.5 whitespace-nowrap">{actionBadge(e.action)}</td>
              <td className="px-3 py-1.5 whitespace-nowrap text-xs">
                {e.rule_id != null ? (
                  <Link to={`/rules#rule-${e.rule_id}`} className="font-mono text-blue-700 hover:text-blue-900 underline">
                    #{e.rule_id}
                  </Link>
                ) : (
                  <span className="text-gray-400 font-mono">-</span>
                )}
                {e.chain && <span className="ml-1 font-mono text-[10px] text-gray-500">{e.chain}</span>}
              </td>
              <td className={`px-3 py-1.5 font-mono text-xs break-all ${messageClass(e.message)}`} title={details}>
                {summary}
              </td>
            </tr>
          )
        })}
      />
    </div>
  )
}

// Shared lightweight table chrome used by the three views. Keeps row
// heights, header styling and empty state consistent.
function LogTable({
  columns, body, loading, empty, emptyIcon, emptyText, emptyHint,
}: {
  columns: { label: string; width?: string }[]
  body: React.ReactNode
  loading: boolean
  empty: boolean
  emptyIcon?: React.ReactNode
  emptyText: string
  emptyHint?: string
}) {
  return (
    <div className="border border-gray-200 rounded-md overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-600 text-xs">
          <tr>
            {columns.map((c, i) => (
              <th key={i} className={`text-left font-medium px-3 py-2 ${c.width ?? ''}`}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading && (
            <TableSkeleton rows={5} cols={columns.length} />
          )}
          {!loading && empty && (
            <tr>
              <td colSpan={columns.length}>
                <EmptyState icon={emptyIcon} text={emptyText} hint={emptyHint} />
              </td>
            </tr>
          )}
          {!loading && !empty && body}
        </tbody>
      </table>
    </div>
  )
}

function AuditView() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [methodFilter, setMethodFilter] = useState<string>('')
  const [userFilter, setUserFilter] = useState<string>('')
  const [sinceMinutes, setSinceMinutes] = useState<string>('')
  const [search, setSearch] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api.logs.audit({
        limit: 1000,
        method: methodFilter || undefined,
        username: userFilter || undefined,
        contains: search || undefined,
      })
      setEntries(r); setError(null)
    } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }, [methodFilter, userFilter, search])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [autoRefresh, load])

  // Filtre par fenetre temporelle cote client (le backend n'expose pas
  // since pour audit, on filtre apres la fetch). Pour <=24h ca suffit.
  const filteredEntries = useMemo(() => {
    if (!sinceMinutes) return entries
    const since = Date.now() - parseInt(sinceMinutes, 10) * 60 * 1000
    return entries.filter((e) => {
      try { return new Date(e.timestamp + 'Z').getTime() >= since }
      catch { return true }
    })
  }, [entries, sinceMinutes])

  const knownUsers = useMemo(() => {
    const s = new Set<string>()
    entries.forEach((e) => { if (e.username) s.add(e.username) })
    return Array.from(s).sort()
  }, [entries])

  const empty = !loading && filteredEntries.length === 0
  const anyFilter = !!(search || methodFilter || userFilter || sinceMinutes)

  return (
    <div className="card space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <select className="select w-auto py-1.5" value={methodFilter}
          onChange={(e) => setMethodFilter(e.target.value)}>
          <option value="">All methods</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="PATCH">PATCH</option>
          <option value="DELETE">DELETE</option>
        </select>
        <select className="select w-auto py-1.5" value={userFilter}
          onChange={(e) => setUserFilter(e.target.value)}>
          <option value="">All users</option>
          {knownUsers.map((u) => <option key={u} value={u}>{u}</option>)}
        </select>
        <select className="select w-auto py-1.5" value={sinceMinutes}
          onChange={(e) => setSinceMinutes(e.target.value)}>
          <option value="">All time</option>
          <option value="15">Last 15 min</option>
          <option value="60">Last hour</option>
          <option value="360">Last 6 hours</option>
          <option value="1440">Last 24 hours</option>
          <option value="10080">Last 7 days</option>
        </select>
        <input
          className="input flex-1 min-w-[220px] py-1.5"
          placeholder="Search path or label..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') load() }}
        />
        {anyFilter && (
          <button className="btn-ghost py-1 text-xs" onClick={() => {
            setSearch(''); setMethodFilter(''); setUserFilter(''); setSinceMinutes('')
          }}>clear</button>
        )}
        <span className="ml-auto inline-flex items-center gap-3">
          <span className="text-[11px] text-gray-500 whitespace-nowrap">
            {filteredEntries.length}{filteredEntries.length !== entries.length ? ` / ${entries.length}` : ''} action(s)
          </span>
          <RefreshControls
            autoRefresh={autoRefresh}
            onAutoRefreshChange={setAutoRefresh}
            onReload={load}
            busy={loading}
          />
        </span>
      </div>

      {error && <ErrorBlock message={error} />}

      <LogTable
        columns={[
          { label: 'Timestamp', width: 'w-40' },
          { label: 'User', width: 'w-24' },
          { label: 'Method', width: 'w-16' },
          { label: 'Action' },
          { label: 'Status', width: 'w-20' },
          { label: 'Client IP', width: 'w-28' },
        ]}
        loading={loading && entries.length === 0}
        empty={empty}
        emptyText="No action logged"
        emptyHint="UI write actions appear here (POST/PUT/PATCH/DELETE)."
        body={filteredEntries.map((e) => (
          <tr key={e.id} className="border-t border-gray-200 hover:bg-gray-50">
            <td className="px-3 py-1.5 font-mono text-xs text-gray-700 whitespace-nowrap align-top">
              {fmt.datetime(e.timestamp + 'Z')}
            </td>
            <td className="px-3 py-1.5 text-xs font-medium align-top">
              {e.username || <span className="text-gray-400">anonymous</span>}
            </td>
            <td className="px-3 py-1.5 align-top">
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded font-medium ${
                e.method === 'DELETE' ? 'bg-red-100 text-red-800' :
                e.method === 'POST' ? 'bg-emerald-100 text-emerald-800' :
                e.method === 'PUT' || e.method === 'PATCH' ? 'bg-blue-100 text-blue-800' :
                'bg-gray-100 text-gray-700'
              }`}>{e.method}</span>
            </td>
            <td className="px-3 py-1.5 text-xs align-top">
              <div>{e.action_summary || e.path}</div>
              {e.action_summary && e.action_summary !== e.path && (
                <div className="font-mono text-[10px] text-gray-500">{e.path}</div>
              )}
            </td>
            <td className="px-3 py-1.5 align-top">
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded font-medium ${
                e.status_code < 300 ? 'bg-emerald-100 text-emerald-800' :
                e.status_code < 400 ? 'bg-blue-100 text-blue-800' :
                e.status_code < 500 ? 'bg-amber-100 text-amber-800' :
                'bg-red-100 text-red-800'
              }`}>{e.status_code}</span>
            </td>
            <td className="px-3 py-1.5 font-mono text-xs text-gray-600 align-top">
              {e.client_ip || <span className="text-gray-400">-</span>}
            </td>
          </tr>
        ))}
      />
      <div className="text-[10px] text-gray-400">Last 5000 entries kept in DB.</div>
    </div>
  )
}

function SystemView() {
  const [units, setUnits] = useState<string[]>([])
  const [unit, setUnit] = useState<string>('muros-backend.service')
  const [entries, setEntries] = useState<SystemLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [priority, setPriority] = useState<string>('')
  const [sinceMinutes, setSinceMinutes] = useState<string>('60')
  const [autoRefresh, setAutoRefresh] = useState(false)

  useEffect(() => {
    api.logs.systemUnits().then(setUnits).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api.logs.system({
        unit,
        limit: 1000,
        since_minutes: sinceMinutes ? parseInt(sinceMinutes, 10) : undefined,
        search: search || undefined,
        priority: priority || undefined,
      })
      setEntries(r); setError(null)
    } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }, [unit, sinceMinutes, search, priority])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [autoRefresh, load])

  const prioBadge = (p: number) => {
    // 0=emerg 1=alert 2=crit 3=err 4=warning 5=notice 6=info 7=debug
    if (p <= 3) return { c: 'bg-red-100 text-red-800 border-red-200', l: 'ERR' }
    if (p === 4) return { c: 'bg-amber-100 text-amber-800 border-amber-200', l: 'WRN' }
    if (p === 5) return { c: 'bg-sky-100 text-sky-800 border-sky-200', l: 'NOT' }
    if (p === 6) return { c: 'bg-emerald-100 text-emerald-800 border-emerald-200', l: 'INF' }
    return { c: 'bg-gray-100 text-gray-700 border-gray-200', l: 'DBG' }
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <select className="select w-auto py-1.5" value={unit} onChange={(e) => setUnit(e.target.value)}>
          {units.length === 0 && <option value="muros-backend.service">muros-backend.service</option>}
          {units.map((u) => <option key={u} value={u}>{u}</option>)}
        </select>
        <select className="select w-auto py-1.5" value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="">All priorities</option>
          <option value="err">Errors only</option>
          <option value="warning">Warning+</option>
          <option value="info">Info+</option>
          <option value="debug">Debug+</option>
        </select>
        <select className="select w-auto py-1.5" value={sinceMinutes} onChange={(e) => setSinceMinutes(e.target.value)}>
          <option value="">All time</option>
          <option value="15">Last 15 min</option>
          <option value="60">Last hour</option>
          <option value="360">Last 6 hours</option>
          <option value="1440">Last 24 hours</option>
        </select>
        <input
          className="input flex-1 min-w-[220px] py-1.5"
          placeholder="Search regex (journalctl -g)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') load() }}
        />
        <span className="ml-auto inline-flex items-center gap-3">
          <span className="text-[11px] text-gray-500 whitespace-nowrap">
            {entries.length} {entries.length === 1 ? 'entry' : 'entries'}
          </span>
          <RefreshControls
            autoRefresh={autoRefresh}
            onAutoRefreshChange={setAutoRefresh}
            onReload={load}
            busy={loading}
          />
        </span>
      </div>

      {error && <ErrorBlock message={error} />}

      <LogTable
        columns={[
          { label: 'Timestamp', width: 'w-44' },
          { label: 'Prio', width: 'w-16' },
          { label: 'Message' },
        ]}
        loading={loading && entries.length === 0}
        empty={!loading && entries.length === 0}
        emptyIcon={<Inbox size={20} />}
        emptyText="No log"
        emptyHint="No entries match the current filters. Try widening the time window or removing the priority filter."
        body={entries.map((e, idx) => {
          const b = prioBadge(e.priority)
          return (
            <tr key={idx} className="border-t border-gray-200 hover:bg-gray-50 align-top">
              <td className="px-3 py-1.5 font-mono text-xs text-gray-700 whitespace-nowrap">
                {e.timestamp ? fmt.datetime(e.timestamp) : ''}
              </td>
              <td className="px-3 py-1.5 whitespace-nowrap">
                <span className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border ${b.c}`}>{b.l}</span>
              </td>
              <td className="px-3 py-1.5 font-mono text-xs break-all text-gray-900">
                {e.message}
              </td>
            </tr>
          )
        })}
      />
    </div>
  )
}
