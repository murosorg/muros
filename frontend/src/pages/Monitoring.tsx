import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, MetricsSummary, SystemService, MurosUpdateStatus } from '../lib/api'
import PageHeader from '../components/PageHeader'
import LoadingState from '../components/LoadingState'
import { fmt } from '../lib/format'
import Sparkline from '../components/Sparkline'
import TimeChart from '../components/TimeChart'
import { ErrorBlock } from '../components/Alerts'
import { Gauge, Package, CheckCircle2, ArrowUpCircle } from 'lucide-react'
import ChangelogNotes from '../components/ChangelogNotes'

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

      // Per-interface throughput (delta / time). Feeds the live
      // "Traffic <iface>" charts in the History section.
      for (const iface of s.interfaces) {
        const prev = prevIfRef.current.get(iface.name)
        if (prev) {
          const dt = (now - prev.ts) / 1000
          if (dt > 0) {
            const rx = Math.max(0, (iface.rx_bytes - prev.rx) / dt)
            const tx = Math.max(0, (iface.tx_bytes - prev.tx) / dt)
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
        {error && (
          <ErrorBlock message={error} />
        )}

        {/* Service inventory top-left, with the MurOS version / changelog
            widget on top of storage filling the space next to it. This
            block answers "what is running, am I on the latest version,
            and where is the space going?" first. The raw resource KPIs
            (CPU, memory, conntrack, load) sit below it. */}
        {!data && !error && <LoadingState text="Loading metrics..." />}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
          <section className="lg:col-span-1">
            <h2 className="text-sm font-semibold text-gray-900 mb-2">Services</h2>
            {services.length === 0 ? (
              <LoadingState variant="inline" text="Loading services state..." />
            ) : (
              <ServiceList services={services} />
            )}
          </section>

          <div className="lg:col-span-2 space-y-4">
            <VersionCard />

            {data && (
              <section>
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
            )}
          </div>
        </div>

        {data && (
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 mb-6">
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
        )}

        {data && (
          <>
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

// MurOS version widget. Fills the space next to storage on the dashboard
// and answers "am I on the latest build?" without leaving the home page:
// it shows the installed version, whether an upgrade is available on the
// apt channel, and the changelog of the latest version when the backend
// exposes release notes. Management (install/repair) still lives on the
// System page; here we only surface the state and link there.
function VersionCard() {
  const navigate = useNavigate()
  const [muros, setMuros] = useState<MurosUpdateStatus | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () => {
      api.updates
        .murosStatus()
        .then((s) => { if (alive) { setMuros(s); setFailed(false) } })
        .catch(() => { if (alive && !muros) setFailed(true) })
    }
    load()
    // Refresh periodically so the up-to-date / update-available badge stays
    // current while the dashboard is left open. murosStatus reads cached
    // apt metadata (no apt-get update), so this stays cheap.
    const id = window.setInterval(load, 60_000)
    return () => { alive = false; window.clearInterval(id) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const upgrade = !!muros?.upgrade_available
  const latest = muros?.candidate || muros?.installed || null

  return (
    <section>
      <h2 className="text-sm font-semibold mb-2 text-gray-900">Version</h2>
      <div className="border border-gray-200 bg-white rounded-md p-3">
        {!muros && !failed && (
          <LoadingState variant="inline" text="Checking version..." />
        )}
        {failed && (
          <p className="text-sm text-gray-700">Version information is unavailable.</p>
        )}
        {muros && (
          <>
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-2 min-w-0">
                <Package size={16} className="text-gray-500 shrink-0" />
                <div className="min-w-0">
                  <div className="text-sm text-gray-900">
                    MurOS{' '}
                    <span className="font-mono font-semibold">
                      {muros.installed || 'not installed'}
                    </span>
                  </div>
                  {muros.last_check_at && (
                    <div className="text-[11px] text-gray-600">
                      Last checked {fmt.relative(muros.last_check_at)}
                    </div>
                  )}
                </div>
              </div>
              {upgrade ? (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-1 rounded bg-amber-100 text-amber-800 whitespace-nowrap">
                  <ArrowUpCircle size={13} />
                  Update available
                </span>
              ) : muros.installed ? (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-1 rounded bg-emerald-100 text-emerald-800 whitespace-nowrap">
                  <CheckCircle2 size={13} />
                  Up to date
                </span>
              ) : null}
            </div>

            {upgrade && muros.candidate && (
              <div className="text-xs text-gray-700 mt-2">
                Latest version on the apt channel:{' '}
                <span className="font-mono font-semibold text-gray-900">{muros.candidate}</span>
              </div>
            )}

            {latest && muros.release_notes ? (
              <details className="mt-3 text-sm" open={upgrade}>
                <summary className="cursor-pointer text-gray-800 hover:text-gray-900">
                  Changelog {latest}
                  {muros.release_published_at && (
                    <span className="text-gray-600"> ({fmt.date(muros.release_published_at)})</span>
                  )}
                </summary>
                <div className="mt-2 bg-gray-50 border border-gray-200 rounded p-3 max-h-56 overflow-auto">
                  <ChangelogNotes text={muros.release_notes} />
                </div>
              </details>
            ) : (
              <p className="text-[11px] text-gray-600 mt-3">
                No changelog available for the latest version.
              </p>
            )}

            <button
              type="button"
              onClick={() => navigate('/system/updates')}
              className="mt-3 text-xs text-gray-700 underline hover:text-gray-900"
            >
              Manage updates
            </button>
          </>
        )}
      </div>
    </section>
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

const isRunning = (status: string) =>
  status === 'active' || status === 'activating' || status === 'reloading'
const isError = (status: string) =>
  status === 'failed' || status === 'unknown'

// A single clickable service row: status dot + name on the left, state
// badge pinned right, linking to the service management page.
function ServiceRow({ s, first }: { s: SystemService; first: boolean }) {
  const navigate = useNavigate()
  const st = serviceLabel(s.status)
  return (
    <button
      type="button"
      onClick={() => navigate(s.page)}
      title={`systemd unit: ${s.unit} - go to ${s.page}`}
      className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-gray-50 ${first ? '' : 'border-t border-gray-200'}`}
    >
      <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${st.dot}`} aria-hidden />
      <span className="flex-1 min-w-0 truncate text-gray-900">{s.display_name}</span>
      <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium whitespace-nowrap ${st.badge}`}>
        {st.label}
      </span>
    </button>
  )
}

// Service inventory split in two groups so the dashboard stays a health
// view rather than a full catalog:
//   - Essential: the services that ship enabled (default_on). Always
//     listed, so an unexpected "inactive" or "in error" stands out.
//   - Optional: feature daemons (VPN, HA, watcher, SSH...). Only shown
//     once they are running or in error. A feature you have not enabled
//     is summarized as a single muted line instead of a permanent
//     "inactive" row that adds noise.
function ServiceList({ services }: { services: SystemService[] }) {
  if (services.length === 0) return null

  const essential = services.filter((s) => s.default_on)
  const optional = services.filter((s) => !s.default_on)
  const optionalShown = optional.filter((s) => isRunning(s.status) || isError(s.status))
  const optionalHidden = optional.length - optionalShown.length

  return (
    <div className="space-y-3">
      <div className="border border-gray-200 rounded-md overflow-hidden bg-white">
        {essential.map((s, i) => (
          <ServiceRow key={s.unit} s={s} first={i === 0} />
        ))}
      </div>

      {(optionalShown.length > 0 || optionalHidden > 0) && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-600 mb-1 px-0.5">
            Optional features
          </div>
          {optionalShown.length > 0 && (
            <div className="border border-gray-200 rounded-md overflow-hidden bg-white">
              {optionalShown.map((s, i) => (
                <ServiceRow key={s.unit} s={s} first={i === 0} />
              ))}
            </div>
          )}
          {optionalHidden > 0 && (
            <p className="text-[11px] text-gray-600 mt-1 px-0.5">
              {optionalHidden} optional service{optionalHidden > 1 ? 's' : ''} available, not enabled.
            </p>
          )}
        </div>
      )}
    </div>
  )
}


