import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, MetricsHistory, MetricsSummary, SystemService } from '../lib/api'
import PageHeader from '../components/PageHeader'
import LoadingState from '../components/LoadingState'
import { fmt } from '../lib/format'
import Sparkline from '../components/Sparkline'
import TimeChart from '../components/TimeChart'
import { ErrorBlock } from '../components/Alerts'
import { Gauge } from 'lucide-react'

const HISTORY_LENGTH = 60  // 60 echantillons * 3s = 3 min
const REFRESH_INTERVAL = 3000

// Wrappers around the centralized helper lib/format.ts. We keep local
// names to minimize the diff at call sites, but the logic comes from
// a single place (fmt.bytes uses binary units 1024, aligned with the
// rest of the app).
const formatBytes = (n: number): string => fmt.bytes(n)
const formatRate = (n: number): string => `${fmt.bytes(n)}/s`
const formatUptime = (seconds: number): string => fmt.duration(seconds)

function usageColor(pct: number): string {
  // Colors reserved for states that require action: amber when we
  // approach the cap, red when we hit it. Below that, neutral: no
  // "feel-good" green that would dilute the alert signal.
  if (pct < 60) return 'text-gray-900'
  if (pct < 85) return 'text-amber-700'
  return 'text-red-700'
}

type History = {
  cpu: number[]
  mem: number[]
  conntrack: number[]
  ifRx: Map<string, number[]>
  ifTx: Map<string, number[]>
}

export default function Monitoring() {
  const [data, setData] = useState<MetricsSummary | null>(null)
  const [history, setHistory] = useState<MetricsHistory | null>(null)
  const [historyHours, setHistoryHours] = useState(24)
  const [error, setError] = useState<string | null>(null)
  const [services, setServices] = useState<SystemService[]>([])

  const reloadServices = () => {
    api.systemActions.listServices().then(setServices).catch(() => setServices([]))
  }
  useEffect(() => {
    reloadServices()
    const id = setInterval(reloadServices, 10_000)
    return () => clearInterval(id)
  }, [])
  const historyRef = useRef<History>({
    cpu: [], mem: [], conntrack: [], ifRx: new Map(), ifTx: new Map(),
  })
  const prevIfRef = useRef<Map<string, { rx: number; tx: number; ts: number }>>(new Map())
  const [ifRates, setIfRates] = useState<Map<string, { rx: number; tx: number }>>(new Map())
  const [lastApply, setLastApply] = useState<{ firewall: string | null; network: string | null }>({ firewall: null, network: null })
  const [pendingCount, setPendingCount] = useState<number>(0)

  const tick = async () => {
    try {
      const s = await api.metrics.summary()
      setData(s)
      setError(null)

      const h = historyRef.current
      h.cpu = [...h.cpu, s.cpu_usage_percent].slice(-HISTORY_LENGTH)
      h.mem = [...h.mem, s.memory.used_percent].slice(-HISTORY_LENGTH)
      h.conntrack = [...h.conntrack, s.conntrack.used_percent].slice(-HISTORY_LENGTH)

      // Per-interface throughput (delta / time)
      const now = Date.now()
      const rates = new Map<string, { rx: number; tx: number }>()
      for (const iface of s.interfaces) {
        const prev = prevIfRef.current.get(iface.name)
        if (prev) {
          const dt = (now - prev.ts) / 1000
          if (dt > 0) {
            const rx = Math.max(0, (iface.rx_bytes - prev.rx) / dt)
            const tx = Math.max(0, (iface.tx_bytes - prev.tx) / dt)
            rates.set(iface.name, { rx, tx })
            h.ifRx.set(iface.name, [...(h.ifRx.get(iface.name) || []), rx].slice(-HISTORY_LENGTH))
            h.ifTx.set(iface.name, [...(h.ifTx.get(iface.name) || []), tx].slice(-HISTORY_LENGTH))
          }
        }
        prevIfRef.current.set(iface.name, { rx: iface.rx_bytes, tx: iface.tx_bytes, ts: now })
      }
      // Cleanup: interface disappeared from the snapshot (VLAN
      // removed, NIC unplugged) -> drop its in-memory history so we
      // do not display a graph for an iface that no longer exists.
      const liveNames = new Set(s.interfaces.map((i) => i.name))
      for (const k of Array.from(h.ifRx.keys())) {
        if (!liveNames.has(k)) h.ifRx.delete(k)
      }
      for (const k of Array.from(h.ifTx.keys())) {
        if (!liveNames.has(k)) h.ifTx.delete(k)
      }
      for (const k of Array.from(prevIfRef.current.keys())) {
        if (!liveNames.has(k)) prevIfRef.current.delete(k)
      }
      setIfRates(rates)
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => {
    tick()
    const id = setInterval(tick, REFRESH_INTERVAL)
    return () => clearInterval(id)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadHistory = async (hours: number) => {
    try {
      setHistory(await api.metrics.history(hours))
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => {
    loadHistory(historyHours)
    const id = setInterval(() => loadHistory(historyHours), 60_000)
    return () => clearInterval(id)
  }, [historyHours])

  // Operations widgets : derniere apply firewall + reseau + pending count.
  // Backend refresh once per minute (applies are rare, no need to go
  // faster). audit_log is filtered API-side by method + contains
  // (path partial match) and we keep the last HTTP 2xx entry.
  useEffect(() => {
    const refreshOps = async () => {
      try {
        const [fw, net, pending] = await Promise.all([
          api.logs.audit({ limit: 30, method: 'POST', contains: '/firewall/apply' }).catch(() => []),
          api.logs.audit({ limit: 30, method: 'POST', contains: '/network/apply' }).catch(() => []),
          api.network.pending().catch(() => ({ count: 0 })),
        ])
        const lastFw = fw.find((e) => e.status_code != null && e.status_code < 400)
        const lastNet = net.find((e) => e.status_code != null && e.status_code < 400)
        setLastApply({
          firewall: lastFw?.timestamp || null,
          network: lastNet?.timestamp || null,
        })
        setPendingCount(pending.count || 0)
      } catch { /* silent */ }
    }
    refreshOps()
    const id = window.setInterval(refreshOps, 60_000)
    return () => window.clearInterval(id)
  }, [])

  // Real-time aggregates for the Operations widget at the top.
  //
  // We want to count every network interface MurOS knows about. The
  // kernel sets operstate=UNKNOWN on virtual netdevs (wg*, tun/tap,
  // bonds with no carrier concept, ...) even when they carry traffic
  // just fine, so we treat 'unknown' as up. Pure plumbing interfaces
  // (loopback, docker bridges, veth pairs) are still skipped: they
  // are never administered through MurOS.
  const isCountedLink = (name: string): boolean => {
    if (name === 'lo') return false
    const ignoredPrefixes = ['docker', 'br-', 'veth', 'virbr']
    return !ignoredPrefixes.some((p) => name.startsWith(p))
  }
  const isLinkUp = (operstate: string | undefined): boolean =>
    operstate === 'up' || operstate === 'unknown'
  const ifaceAggregate = useMemo(() => {
    if (!data) return { up: 0, total: 0, totalRx: 0, totalTx: 0 }
    let up = 0
    let total = 0
    for (const iface of data.interfaces) {
      if (!isCountedLink(iface.name)) continue
      total++
      if (isLinkUp(iface.operstate)) up++
    }
    let totalRx = 0
    let totalTx = 0
    for (const [name, r] of ifRates.entries()) {
      if (!isCountedLink(name)) continue
      totalRx += r.rx
      totalTx += r.tx
    }
    return { up, total, totalRx, totalTx }
  }, [data, ifRates])

  // Per-interface throughput from history (cumulative delta)
  const interfaceRates = useMemo(() => {
    if (!history) return {}
    const out: Record<string, { rx: { x: number; y: number }[]; tx: { x: number; y: number }[] }> = {}
    for (const [name, samples] of Object.entries(history.interfaces)) {
      const rx: { x: number; y: number }[] = []
      const tx: { x: number; y: number }[] = []
      for (let i = 1; i < samples.length; i++) {
        const a = samples[i - 1]
        const b = samples[i]
        const dt = (new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()) / 1000
        if (dt <= 0) continue
        rx.push({ x: new Date(b.timestamp).getTime(), y: Math.max(0, (b.rx_bytes - a.rx_bytes) / dt) })
        tx.push({ x: new Date(b.timestamp).getTime(), y: Math.max(0, (b.tx_bytes - a.tx_bytes) / dt) })
      }
      out[name] = { rx, tx }
    }
    return out
  }, [history])

  const h = historyRef.current

  return (
    <div>
      <PageHeader
        icon={<Gauge size={16} />}
       
        title="Dashboard"
        description="Live system and traffic counters."
      />

      <div className="px-6 py-4">
        {/* Operations summary : vue d'un coup d'oeil de l'etat firewall.
            Pas un sparkline, juste les chiffres bruts qui comptent. */}
        <section className="mb-6">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            <OperationsStat
              label="Interfaces up"
              value={`${ifaceAggregate.up} / ${ifaceAggregate.total}`}
              hint={ifaceAggregate.up === ifaceAggregate.total && ifaceAggregate.total > 0 ? 'All physical links operational' : `${ifaceAggregate.total - ifaceAggregate.up} link(s) down`}
              tone={ifaceAggregate.up === ifaceAggregate.total ? 'ok' : 'warn'}
            />
            <OperationsStat
              label="Total throughput"
              value={`${formatRate(ifaceAggregate.totalRx)} in`}
              valueLine2={`${formatRate(ifaceAggregate.totalTx)} out`}
              hint="Sum across all non-loopback interfaces"
              tone="info"
            />
            <OperationsStat
              label="Conntrack"
              value={data ? data.conntrack.current.toLocaleString('en') : '-'}
              hint={data ? `${data.conntrack.used_percent.toFixed(1)}% of ${data.conntrack.max.toLocaleString('en')}` : 'Active sessions tracked'}
              tone={data && data.conntrack.used_percent > 80 ? 'warn' : 'info'}
            />
            <OperationsStat
              label="Pending changes"
              value={String(pendingCount)}
              hint={pendingCount > 0 ? 'Network changes not yet applied' : 'Everything is in sync'}
              tone={pendingCount > 0 ? 'warn' : 'ok'}
              linkTo={pendingCount > 0 ? '/network' : undefined}
            />
            <OperationsStat
              label="Last apply"
              value={lastApply.firewall || lastApply.network
                ? formatRelative(latestApply(lastApply))
                : 'never'}
              hint={lastApplyHint(lastApply)}
              tone="info"
            />
          </div>
        </section>

        {/* Services in two columns, listed from the oldest / most native
            Unix daemon to the most recent (order of release, as set in
            the backend catalog). No core/optional split anymore: every
            service ships with MurOS, the table reflects current state
            and links to the management page. */}
        <section className="mb-6">
          {services.length === 0 ? (
            <LoadingState variant="inline" text="Loading services state..." />
          ) : (
            <div>
              <div className="flex items-baseline justify-between mb-2">
                <h2 className="text-sm font-semibold text-gray-900">Services</h2>
                <span className="text-[11px] text-gray-600">
                  {services.length} service{services.length > 1 ? 's' : ''}
                </span>
              </div>
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <ServiceTable services={services.slice(0, Math.floor(services.length / 2))} />
                {/* Second column hides the duplicate "Service / State"
                    header: at xl+ they sit side by side and a single
                    header reads across both. */}
                <ServiceTable services={services.slice(Math.floor(services.length / 2))} hideHeaderOnDesktop />
              </div>
            </div>
          )}
        </section>

        {error && (
          <ErrorBlock message={error} />
        )}

        {!data && !error && <LoadingState text="Loading metrics..." />}

        {data && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              <MetricCard
                label="CPU"
                value={`${data.cpu_usage_percent.toFixed(1)}%`}
                hint={`${data.cpu_cores} cores`}
                colorClass={usageColor(data.cpu_usage_percent)}
                history={h.cpu}
                max={100}
                sparkFormat={(n) => `${n.toFixed(1)}%`}
              />
              <MetricCard
                label="Memory"
                value={`${data.memory.used_percent.toFixed(1)}%`}
                hint={`${formatBytes(data.memory.used_bytes)} / ${formatBytes(data.memory.total_bytes)}`}
                colorClass={usageColor(data.memory.used_percent)}
                history={h.mem}
                max={100}
                sparkFormat={(n) => `${n.toFixed(1)}%`}
              />
              <MetricCard
                label="Conntrack"
                value={`${data.conntrack.used_percent.toFixed(1)}%`}
                hint={`${data.conntrack.current.toLocaleString('fr')} / ${data.conntrack.max.toLocaleString('fr')} sessions`}
                colorClass={usageColor(data.conntrack.used_percent)}
                history={h.conntrack}
                max={100}
                sparkFormat={(n) => `${n.toFixed(1)}%`}
              />
              <div className="border border-gray-200 bg-white rounded p-3">
                <div className="text-[10px] uppercase tracking-wider text-gray-700 mb-1">Load / Uptime</div>
                <div className="text-lg font-mono font-semibold text-gray-900 tabular-nums">
                  {data.load.map((l) => l.toFixed(2)).join(' / ')}
                </div>
                <div className="text-xs text-gray-700 mt-1 font-mono">uptime {formatUptime(data.uptime_seconds)}</div>
              </div>
            </div>

            <section className="mb-6">
              <h2 className="text-sm font-semibold mb-2 text-gray-900">Storage</h2>
              <div className="border border-gray-200 rounded-md overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                    <tr>
                      <th className="text-left px-3 py-2">Mount point</th>
                      <th className="text-right px-3 py-2 w-28">Used</th>
                      <th className="text-right px-3 py-2 w-28">Total</th>
                      <th className="text-right px-3 py-2 w-20">%</th>
                      <th className="text-left px-3 py-2">Usage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.disks.map((d) => (
                      <tr key={d.mount} className="border-t border-gray-200">
                        <td className="px-3 py-2 font-mono">{d.mount}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs text-gray-800">{formatBytes(d.used_bytes)}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs text-gray-800">{formatBytes(d.total_bytes)}</td>
                        <td className={`px-3 py-2 text-right font-mono text-xs font-semibold ${usageColor(d.used_percent)}`}>
                          {d.used_percent.toFixed(1)}%
                        </td>
                        <td className="px-3 py-2">
                          <div className="h-2 bg-gray-100 rounded overflow-hidden">
                            <div
                              className="h-full bg-gray-800"
                              style={{ width: `${d.used_percent}%` }}
                            />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="mb-6">
              <h2 className="text-sm font-semibold mb-2 text-gray-900">Per-interface traffic</h2>
              <div className="border border-gray-200 rounded-md overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                    <tr>
                      <th className="text-left px-3 py-2 w-32">Interface</th>
                      <th className="text-right px-3 py-2 w-28">In</th>
                      <th className="text-right px-3 py-2 w-28">Out</th>
                      <th className="text-right px-3 py-2 w-24">Pkts in</th>
                      <th className="text-right px-3 py-2 w-24">Pkts out</th>
                      <th className="text-right px-3 py-2 w-24">Errors</th>
                      <th className="text-right px-3 py-2 w-24">Drops</th>
                      <th className="text-left px-3 py-2 w-44">Rate (in / out)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.interfaces.map((iface) => {
                      const rate = ifRates.get(iface.name)
                      const rx = h.ifRx.get(iface.name) || []
                      const tx = h.ifTx.get(iface.name) || []
                      const maxRate = Math.max(...rx, ...tx, 1)
                      return (
                        <tr key={iface.name} className="border-t border-gray-200">
                          <td className="px-3 py-2 font-mono">{iface.name}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-800">{formatBytes(iface.rx_bytes)}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-800">{formatBytes(iface.tx_bytes)}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-800">{iface.rx_packets.toLocaleString('fr')}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-800">{iface.tx_packets.toLocaleString('fr')}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-800">{iface.rx_errors + iface.tx_errors}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-800">{iface.rx_dropped + iface.tx_dropped}</td>
                          <td className="px-3 py-2">
                            <div className="flex items-center gap-2">
                              <Sparkline values={rx} max={maxRate} color="#0891b2" width={70} height={24} />
                              <Sparkline values={tx} max={maxRate} color="#ea580c" width={70} height={24} />
                            </div>
                            {rate && (
                              <div className="font-mono text-[10px] text-gray-700 mt-0.5">
                                <span className="text-cyan-800" title="Inbound">&darr; {formatRate(rate.rx)}</span>
                                {' '}
                                <span className="text-orange-800" title="Outbound">&uarr; {formatRate(rate.tx)}</span>
                              </div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="mb-6">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-sm font-semibold text-gray-900">History</h2>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-700">Period:</span>
                  <select
                    className="select w-auto py-1 text-xs"
                    value={historyHours}
                    onChange={(e) => setHistoryHours(Number(e.target.value))}
                  >
                    <option value={1}>1 hour</option>
                    <option value={6}>6 hours</option>
                    <option value={12}>12 hours</option>
                    <option value={24}>24 hours</option>
                  </select>
                </div>
              </div>

              {history && history.samples.length === 0 && (
                <p className="text-xs text-gray-700 mb-2">
                  No data yet. Metrics are collected every 60 seconds,
                  l'historique apparaitra ici progressivement.
                </p>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <TimeChart
                  label="CPU and memory (%)"
                  yMax={100}
                  yFormat={(v) => `${v.toFixed(0)}%`}
                  series={[
                    {
                      name: 'CPU',
                      color: '#dc2626',
                      points: (history?.samples || []).map((s) => ({
                        x: new Date(s.timestamp).getTime(),
                        y: s.cpu_usage_percent,
                      })),
                    },
                    {
                      name: 'Memory',
                      color: '#2563eb',
                      points: (history?.samples || []).map((s) => ({
                        x: new Date(s.timestamp).getTime(),
                        y: s.memory_used_percent,
                      })),
                    },
                  ]}
                />
                <TimeChart
                  label="Conntrack sessions"
                  yFormat={(v) => v.toFixed(0)}
                  series={[
                    {
                      name: 'sessions',
                      color: '#7c3aed',
                      points: (history?.samples || []).map((s) => ({
                        x: new Date(s.timestamp).getTime(),
                        y: s.conntrack_current,
                      })),
                    },
                  ]}
                />
                <TimeChart
                  label="System load"
                  yFormat={(v) => v.toFixed(2)}
                  series={[
                    {
                      name: '1 min',
                      color: '#ea580c',
                      points: (history?.samples || []).map((s) => ({
                        x: new Date(s.timestamp).getTime(),
                        y: s.load_1,
                      })),
                    },
                    {
                      name: '5 min',
                      color: '#0891b2',
                      points: (history?.samples || []).map((s) => ({
                        x: new Date(s.timestamp).getTime(),
                        y: s.load_5,
                      })),
                    },
                  ]}
                />
                {(() => {
                  // One graph per interface, alphabetical order. lo is
                  // excluded: on a firewall we care about traffic that
                  // transits, the loopback says nothing about the role
                  // of the machine. We also filter on the interfaces
                  // currently present in the live snapshot: a removed
                  // iface (VLAN dropped, NIC unplugged) keeps its
                  // history in DB but we do not want to keep showing
                  // its graph if it no longer exists, that is misleading.
                  const liveNames = new Set((data?.interfaces || []).map((i) => i.name))
                  const names = Object.keys(interfaceRates)
                    .filter((n) => n !== 'lo' && liveNames.has(n))
                    .sort((a, b) => a.localeCompare(b))
                  return names.map((name) => {
                    const rates = interfaceRates[name]
                    return (
                      <TimeChart
                        key={name}
                        label={`Traffic ${name}`}
                        yFormat={formatBytes}
                        series={[
                          { name: 'In',  color: '#0891b2', points: rates.rx },
                          { name: 'Out', color: '#ea580c', points: rates.tx },
                        ]}
                      />
                    )
                  })
                })()}
              </div>
            </section>

          </>
        )}
      </div>
    </div>
  )
}

function MetricCard({
  label, value, hint, colorClass, history, max, sparkFormat,
}: {
  label: string
  value: string
  hint?: string
  colorClass?: string
  history?: number[]
  max?: number
  // Optional formatter for the min/max caption rendered under the
  // sparkline. Defaults to plain rounded integer; pass a unit-aware
  // formatter (e.g. `(n) => n.toFixed(1) + '%'`) when relevant.
  sparkFormat?: (n: number) => string
}) {
  return (
    <div className="border border-gray-200 bg-white rounded p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-wider text-gray-700 mb-1">{label}</div>
          <div className={`text-2xl font-mono font-semibold tabular-nums ${colorClass || 'text-gray-900'}`}>{value}</div>
          {hint && <div className="text-[11px] text-gray-700 mt-1 font-mono truncate">{hint}</div>}
        </div>
        {history && history.length > 1 && (
          <Sparkline
            values={history}
            max={max}
            width={70}
            height={36}
            showRange
            format={sparkFormat}
          />
        )}
      </div>
    </div>
  )
}

// 3 normalized states: active (green), inactive (gray), in error (red).
// Transient systemd states are projected as follows:
//   activating/reloading -> active
//   deactivating         -> inactive
//   failed/unknown       -> in error
function serviceLabel(status: string): { dot: string; badge: string; label: string } {
  switch (status) {
    case 'active':
    case 'activating':
    case 'reloading':
      return { dot: 'bg-emerald-500', badge: 'bg-emerald-100 text-emerald-800', label: 'active' }
    case 'inactive':
    case 'deactivating':
      return { dot: 'bg-slate-500', badge: 'bg-slate-200 text-slate-700', label: 'inactive' }
    case 'disabled_by_admin':
      // Operator explicitly turned the daemon off from the UI. Not an
      // error condition, render it as a neutral muted state.
      return { dot: 'bg-slate-400', badge: 'bg-slate-100 text-slate-700', label: 'disabled by admin' }
    case 'failed':
    case 'unknown':
    default:
      return { dot: 'bg-red-500', badge: 'bg-red-100 text-red-800', label: 'in error' }
  }
}

// Renders one half of the service list as a self-contained table. The
// section header (title + total count) is rendered once above the
// two-column grid, not per column.
function ServiceTable({ services, hideHeaderOnDesktop }: { services: SystemService[]; hideHeaderOnDesktop?: boolean }) {
  const navigate = useNavigate()
  if (services.length === 0) return null
  return (
    <div className="border border-gray-200 rounded-md overflow-hidden">
      <table className="w-full text-sm">
        <thead className={`bg-gray-50 text-gray-600 text-xs ${hideHeaderOnDesktop ? 'xl:hidden' : ''}`}>
          <tr>
            <th className="text-left font-medium px-3 py-2 w-6"></th>
            <th className="text-left font-medium px-3 py-2">Service</th>
            <th className="text-left font-medium px-3 py-2 w-24">State</th>
          </tr>
        </thead>
        <tbody>
          {services.map((s) => {
            const st = serviceLabel(s.status)
            return (
              <tr
                key={s.unit}
                className="border-t border-gray-200 hover:bg-gray-50 cursor-pointer"
                onClick={() => navigate(s.page)}
                title={`Go to management page: ${s.page}`}
              >
                <td className="px-3 py-1.5">
                  <span
                    className={`inline-block w-2 h-2 rounded-full ${st.dot}`}
                    aria-hidden
                  />
                </td>
                <td
                  className="px-3 py-1.5 text-gray-900"
                  title={`systemd unit: ${s.unit}`}
                >
                  {s.display_name}
                </td>
                <td className="px-3 py-1.5">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium text-center min-w-[96px] inline-block whitespace-nowrap ${st.badge}`}>
                    {st.label}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// --- Operations summary helpers ---

function OperationsStat({ label, value, valueLine2, hint, tone, linkTo }: {
  label: string
  value: string
  valueLine2?: string
  hint?: string
  tone?: 'ok' | 'warn' | 'info'
  linkTo?: string
}) {
  const navigate = useNavigate()
  const toneCls = tone === 'ok'   ? 'border-emerald-200 bg-emerald-50/30'
                : tone === 'warn' ? 'border-amber-200 bg-amber-50/30'
                : 'border-gray-200 bg-white'
  const clickable = !!linkTo
  return (
    <div
      className={`border rounded p-3 ${toneCls} ${clickable ? 'cursor-pointer hover:shadow-sm transition-shadow' : ''}`}
      onClick={clickable ? () => navigate(linkTo!) : undefined}
      title={clickable ? `Click to go to ${linkTo}` : undefined}
    >
      <div className="text-[10px] uppercase tracking-wider text-gray-700 mb-1">{label}</div>
      <div className="text-lg font-semibold text-gray-900 tabular-nums leading-tight">{value}</div>
      {valueLine2 && (
        <div className="text-sm text-gray-700 tabular-nums leading-tight">{valueLine2}</div>
      )}
      {hint && <div className="text-[11px] text-gray-600 mt-1">{hint}</div>}
    </div>
  )
}

function latestApply(la: { firewall: string | null; network: string | null }): string | null {
  if (!la.firewall && !la.network) return null
  if (!la.firewall) return la.network
  if (!la.network) return la.firewall
  return la.firewall > la.network ? la.firewall : la.network
}

function lastApplyHint(la: { firewall: string | null; network: string | null }): string {
  // We avoid repeating the headline number (which is the most recent of the
  // two). Only surface the second source when it brings new information:
  // - one source missing: mention it as never applied
  // - both present but different: show both timestamps
  // - both present and identical: a single line confirming sync
  if (!la.firewall && !la.network) return 'No successful apply recorded yet'
  if (la.firewall && !la.network) return 'Network: never applied'
  if (la.network && !la.firewall) return 'Firewall: never applied'
  if (la.firewall === la.network) return 'Firewall and network in sync'
  return `Firewall: ${formatRelative(la.firewall)} . Network: ${formatRelative(la.network)}`
}

function formatRelative(ts: string | null): string {
  if (!ts) return 'never'
  try {
    // Backend returns ISO without timezone (UTC implicit). Append 'Z'
    // if missing so the browser does not interpret it as local time.
    const d = new Date(ts.endsWith('Z') || /[+-]\d\d:\d\d$/.test(ts) ? ts : ts + 'Z')
    const diff = Math.max(0, (Date.now() - d.getTime()) / 1000)
    if (diff < 60) return 'just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`
    return d.toISOString().slice(0, 10)
  } catch {
    return ts
  }
}
