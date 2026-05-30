import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, MetricsSummary, SystemService } from '../lib/api'
import PageHeader from '../components/PageHeader'
import LoadingState from '../components/LoadingState'
import { fmt } from '../lib/format'
import Sparkline from '../components/Sparkline'
import TimeChart from '../components/TimeChart'
import { ErrorBlock } from '../components/Alerts'
import { Gauge } from 'lucide-react'

// Live dashboard: we sample the summary endpoint twice per second so the
// charts feel alive, and keep an in-memory ring buffer covering the
// widest selectable window. The history charts are no longer fed by the
// backend 60s collector: everything you see here is the live stream of
// the last few minutes, computed client side.
const REFRESH_INTERVAL = 500          // 0.5s real-time refresh
const MAX_WINDOW_MINUTES = 15         // widest selectable history window
const MAX_SAMPLES = Math.ceil((MAX_WINDOW_MINUTES * 60 * 1000) / REFRESH_INTERVAL)

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

// A timestamped point. x is a ms epoch, y the value. Every in-memory
// series is stored this way so the time charts can plot against a real
// time axis and the per-interface sparklines just read the y values.
type Pt = { x: number; y: number }

type History = {
  cpu: Pt[]
  mem: Pt[]
  load1: Pt[]
  load5: Pt[]
  conntrackPct: Pt[]
  conntrackCur: Pt[]
  ifRx: Map<string, Pt[]>
  ifTx: Map<string, Pt[]>
}

export default function Monitoring() {
  const [data, setData] = useState<MetricsSummary | null>(null)
  const [windowMinutes, setWindowMinutes] = useState(5)
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
    cpu: [], mem: [], load1: [], load5: [], conntrackPct: [], conntrackCur: [],
    ifRx: new Map(), ifTx: new Map(),
  })
  const prevIfRef = useRef<Map<string, { rx: number; tx: number; ts: number }>>(new Map())
  const [ifRates, setIfRates] = useState<Map<string, { rx: number; tx: number }>>(new Map())

  const tick = async () => {
    try {
      const s = await api.metrics.summary()
      setData(s)
      setError(null)

      const now = Date.now()
      // Drop points older than the widest window and cap the length so
      // the buffers stay bounded even after hours on the page.
      const trim = (pts: Pt[]): Pt[] => {
        const cutoff = now - MAX_WINDOW_MINUTES * 60_000
        const out = pts.filter((p) => p.x >= cutoff)
        return out.length > MAX_SAMPLES ? out.slice(-MAX_SAMPLES) : out
      }

      const h = historyRef.current
      h.cpu = trim([...h.cpu, { x: now, y: s.cpu_usage_percent }])
      h.mem = trim([...h.mem, { x: now, y: s.memory.used_percent }])
      h.load1 = trim([...h.load1, { x: now, y: s.load[0] ?? 0 }])
      h.load5 = trim([...h.load5, { x: now, y: s.load[1] ?? 0 }])
      h.conntrackPct = trim([...h.conntrackPct, { x: now, y: s.conntrack.used_percent }])
      h.conntrackCur = trim([...h.conntrackCur, { x: now, y: s.conntrack.current }])

      // Per-interface throughput (delta / time)
      const rates = new Map<string, { rx: number; tx: number }>()
      for (const iface of s.interfaces) {
        const prev = prevIfRef.current.get(iface.name)
        if (prev) {
          const dt = (now - prev.ts) / 1000
          if (dt > 0) {
            const rx = Math.max(0, (iface.rx_bytes - prev.rx) / dt)
            const tx = Math.max(0, (iface.tx_bytes - prev.tx) / dt)
            rates.set(iface.name, { rx, tx })
            h.ifRx.set(iface.name, trim([...(h.ifRx.get(iface.name) || []), { x: now, y: rx }]))
            h.ifTx.set(iface.name, trim([...(h.ifTx.get(iface.name) || []), { x: now, y: tx }]))
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

  const h = historyRef.current
  // Clip an in-memory series to the selected display window. Used by both
  // the history charts and the per-interface sparklines so everything
  // shows the same time span.
  const windowStart = Date.now() - windowMinutes * 60_000
  const clip = (pts: Pt[]): Pt[] => pts.filter((p) => p.x >= windowStart)
  const clipY = (pts: Pt[]): number[] => clip(pts).map((p) => p.y)

  return (
    <div>
      <PageHeader
        icon={<Gauge size={16} />}
       
        title="Dashboard"
        description="Live system and traffic counters."
      />

      <div className="px-6 py-4">
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
                history={clipY(h.cpu)}
                max={100}
                sparkFormat={(n) => `${n.toFixed(1)}%`}
              />
              <MetricCard
                label="Memory"
                value={`${data.memory.used_percent.toFixed(1)}%`}
                hint={`${formatBytes(data.memory.used_bytes)} / ${formatBytes(data.memory.total_bytes)}`}
                colorClass={usageColor(data.memory.used_percent)}
                history={clipY(h.mem)}
                max={100}
                sparkFormat={(n) => `${n.toFixed(1)}%`}
              />
              <MetricCard
                label="Conntrack"
                value={`${data.conntrack.used_percent.toFixed(1)}%`}
                hint={`${data.conntrack.current.toLocaleString('fr')} / ${data.conntrack.max.toLocaleString('fr')} sessions`}
                colorClass={usageColor(data.conntrack.used_percent)}
                history={clipY(h.conntrackPct)}
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
                      const rx = clipY(h.ifRx.get(iface.name) || [])
                      const tx = clipY(h.ifTx.get(iface.name) || [])
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
                  <span className="text-xs text-gray-700">Window:</span>
                  <select
                    className="select w-auto py-1 text-xs"
                    value={windowMinutes}
                    onChange={(e) => setWindowMinutes(Number(e.target.value))}
                  >
                    <option value={1}>1 minute</option>
                    <option value={5}>5 minutes</option>
                    <option value={15}>15 minutes</option>
                  </select>
                </div>
              </div>

              {clip(h.cpu).length < 2 && (
                <p className="text-xs text-gray-700 mb-2">
                  Collecting live samples, the charts fill in over the next
                  few seconds.
                </p>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <TimeChart
                  label="CPU and memory (%)"
                  yMax={100}
                  xSpanMs={windowMinutes * 60_000}
                  yFormat={(v) => `${v.toFixed(0)}%`}
                  series={[
                    { name: 'CPU', color: '#dc2626', points: clip(h.cpu) },
                    { name: 'Memory', color: '#2563eb', points: clip(h.mem) },
                  ]}
                />
                <TimeChart
                  label="Conntrack sessions"
                  xSpanMs={windowMinutes * 60_000}
                  yFormat={(v) => v.toFixed(0)}
                  series={[
                    { name: 'sessions', color: '#7c3aed', points: clip(h.conntrackCur) },
                  ]}
                />
                <TimeChart
                  label="System load"
                  xSpanMs={windowMinutes * 60_000}
                  yFormat={(v) => v.toFixed(2)}
                  series={[
                    { name: '1 min', color: '#ea580c', points: clip(h.load1) },
                    { name: '5 min', color: '#0891b2', points: clip(h.load5) },
                  ]}
                />
                {(() => {
                  // One graph per interface, alphabetical order. lo is
                  // excluded: on a firewall we care about traffic that
                  // transits, the loopback says nothing about the role
                  // of the machine. We also filter on the interfaces
                  // currently present in the live snapshot: a removed
                  // iface (VLAN dropped, NIC unplugged) is dropped from
                  // the in-memory buffers, so it stops being charted.
                  const liveNames = new Set((data?.interfaces || []).map((i) => i.name))
                  const names = Array.from(h.ifRx.keys())
                    .filter((n) => n !== 'lo' && liveNames.has(n))
                    .sort((a, b) => a.localeCompare(b))
                  return names.map((name) => (
                    <TimeChart
                      key={name}
                      label={`Traffic ${name}`}
                      xSpanMs={windowMinutes * 60_000}
                      yFormat={formatBytes}
                      series={[
                        { name: 'In',  color: '#0891b2', points: clip(h.ifRx.get(name) || []) },
                        { name: 'Out', color: '#ea580c', points: clip(h.ifTx.get(name) || []) },
                      ]}
                    />
                  ))
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


