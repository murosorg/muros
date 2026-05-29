import { useEffect, useRef, useState } from 'react'
import { api, type DiagResult } from '../lib/api'
import PageHeader from '../components/PageHeader'
import { ErrorBlock } from '../components/Alerts'
import { Stethoscope } from 'lucide-react'

type Tool = 'ping' | 'traceroute' | 'dns' | 'port' | 'public_ip' | 'capture' | 'conntrack' | 'routes' | 'addresses' | 'nft'

type ToolMeta = {
  id: Tool
  label: string
  sub: string
}

type ToolGroup = {
  title: string
  items: ToolMeta[]
}

// Organisation des outils en categories : nav sidebar a gauche, plus
// scannable que des tabs horizontales pour 8 items. Names en FR clair,
// le sous-titre indique la commande Unix sous-jacente pour les admins
// qui prefereraient un shell.
const TOOL_GROUPS: ToolGroup[] = [
  {
    title: 'Connectivity',
    items: [
      { id: 'ping',       label: 'Reachability test', sub: 'ping' },
      { id: 'traceroute', label: 'Network path',      sub: 'traceroute' },
    ],
  },
  {
    title: 'Naming and ports',
    items: [
      { id: 'dns',  label: 'DNS resolution', sub: 'dig' },
      { id: 'port', label: 'Port test',      sub: 'nc / nmap' },
    ],
  },
  {
    title: 'WAN / Egress',
    items: [
      { id: 'public_ip', label: 'Public IP', sub: 'curl ifconfig.me, ipify, ...' },
    ],
  },
  {
    title: 'Traffic',
    items: [
      { id: 'capture',   label: 'Packet capture',     sub: 'tcpdump' },
      { id: 'conntrack', label: 'Active connections', sub: 'conntrack -L' },
    ],
  },
  {
    title: 'Kernel state',
    items: [
      { id: 'routes',    label: 'Routing table',  sub: 'ip route show' },
      { id: 'addresses', label: 'Interface addresses', sub: 'ip addr show' },
      { id: 'nft',       label: 'Ruleset nftables',  sub: 'nft list ruleset' },
    ],
  },
]

// Lookup pour le panneau de droite (label + sub).
const TOOL_BY_ID: Record<Tool, ToolMeta> = Object.fromEntries(
  TOOL_GROUPS.flatMap((g) => g.items).map((t) => [t.id, t])
) as Record<Tool, ToolMeta>

const DEFAULT_TARGET: Record<Tool, string> = {
  ping: '8.8.8.8',
  traceroute: '8.8.8.8',
  dns: 'github.com',
  port: '1.1.1.1',
  public_ip: '',
  capture: '',
  conntrack: '',
  routes: '',
  addresses: '',
  nft: '',
}

export default function Diagnostic() {
  const [tool, setTool] = useState<Tool>('ping')
  const [target, setTarget] = useState(DEFAULT_TARGET.ping)
  const [count, setCount] = useState(4)
  const [dnsType, setDnsType] = useState('A')
  const [dnsResolver, setDnsResolver] = useState('')
  const [iface, setIface] = useState('')
  const [captureCount, setCaptureCount] = useState(50)
  const [captureFilter, setCaptureFilter] = useState('')
  const [conntrackFilter, setConntrackFilter] = useState('')
  const [portTestPort, setPortTestPort] = useState(443)
  const [portTestProto, setPortTestProto] = useState<'tcp' | 'udp'>('tcp')
  const [publicIpFamily, setPublicIpFamily] = useState<'auto' | 'v4' | 'v6'>('auto')
  const [interfaces, setInterfaces] = useState<string[]>([])
  const [result, setResult] = useState<DiagResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const outputRef = useRef<HTMLPreElement>(null)
  const targetWasEditedRef = useRef(false)

  useEffect(() => {
    api.diag.interfaces().then((ifs) => {
      setInterfaces(ifs)
      if (ifs.length && !iface) setIface(ifs.find((i) => i !== 'lo') || ifs[0])
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Change la target par defaut quand on change d'outil (sauf si user a
  // deja edite la target manuellement).
  const selectTool = (t: Tool) => {
    setTool(t); setResult(null); setErr(null)
    if (!targetWasEditedRef.current && t !== 'capture') {
      setTarget(DEFAULT_TARGET[t])
    }
  }

  const onTargetChange = (v: string) => {
    targetWasEditedRef.current = true
    setTarget(v)
  }

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [result])

  const run = async () => {
    setBusy(true); setErr(null); setResult(null)
    try {
      let r: DiagResult
      if (tool === 'ping') r = await api.diag.ping(target, count)
      else if (tool === 'traceroute') r = await api.diag.traceroute(target, 20)
      else if (tool === 'dns') r = await api.diag.dns(target, dnsType, dnsResolver || undefined)
      else if (tool === 'port') r = await api.diag.portTest(target, portTestPort, portTestProto, 5)
      else if (tool === 'public_ip') r = await api.diag.publicIp(publicIpFamily)
      else if (tool === 'routes') r = await api.diag.routes()
      else if (tool === 'addresses') r = await api.diag.addresses()
      else if (tool === 'nft') r = await api.diag.nft()
      else if (tool === 'conntrack') r = await api.diag.conntrack(conntrackFilter || undefined)
      else r = await api.diag.capture(iface, captureCount, captureFilter || undefined)
      setResult(r)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const onKey: React.KeyboardEventHandler<HTMLInputElement> = (e) => { if (e.key === 'Enter') run() }

  return (
    <div>
      <PageHeader
        icon={<Stethoscope size={16} />}
       
        title="Diagnostics"
        description="Troubleshooting tools."
      />

      <div className="px-6 py-4 grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-6 max-w-6xl">
        {/* Nav sidebar : outils groupes par categorie. */}
        <nav className="space-y-4">
          {TOOL_GROUPS.map((group) => (
            <div key={group.title}>
              <div className="text-[10px] uppercase tracking-widest text-gray-500 font-semibold mb-1.5 px-2">
                {group.title}
              </div>
              <div className="space-y-0.5">
                {group.items.map((t) => {
                  const active = tool === t.id
                  return (
                    <button
                      key={t.id}
                      onClick={() => selectTool(t.id)}
                      title={t.sub}
                      className={`w-full text-left pl-2.5 pr-2 py-1 text-sm transition-colors border-l-[3px] ${
                        active
                          ? 'bg-amber-50 border-amber-400 text-gray-900 font-medium'
                          : 'border-transparent text-gray-800 hover:bg-gray-100'
                      }`}
                    >
                      {t.label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Panneau droit : form + result. */}
        <div className="space-y-4 min-w-0">
        <div className="card">
          <div className="mb-3">
            <div className="text-base font-semibold text-gray-900">{TOOL_BY_ID[tool].label}</div>
            <div className="text-xs text-gray-600 font-mono mt-0.5">{TOOL_BY_ID[tool].sub}</div>
          </div>
          <div className="flex flex-wrap gap-2 items-end mb-3">
            {tool === 'ping' && (<>
              <div className="flex-1 min-w-[200px]">
                <div className="text-xs font-medium text-gray-600 mb-1">Target</div>
                <input className="input" value={target} placeholder="8.8.8.8 or example.com"
                  onChange={(e) => onTargetChange(e.target.value)} onKeyDown={onKey} />
              </div>
              <div className="w-24">
                <div className="text-xs font-medium text-gray-600 mb-1">Packets</div>
                <input type="number" className="input" min={1} max={20} value={count}
                  onChange={(e) => setCount(parseInt(e.target.value) || 4)} />
              </div>
            </>)}
            {tool === 'traceroute' && (
              <div className="flex-1 min-w-[200px]">
                <div className="text-xs font-medium text-gray-600 mb-1">Target</div>
                <input className="input" value={target} placeholder="8.8.8.8 or example.com"
                  onChange={(e) => onTargetChange(e.target.value)} onKeyDown={onKey} />
              </div>
            )}
            {tool === 'dns' && (<>
              <div className="flex-1 min-w-[200px]">
                <div className="text-xs font-medium text-gray-600 mb-1">Name to resolve</div>
                <input className="input" value={target} placeholder="example.com"
                  onChange={(e) => onTargetChange(e.target.value)} onKeyDown={onKey} />
              </div>
              <div className="w-28">
                <div className="text-xs font-medium text-gray-600 mb-1">Type</div>
                <select className="input" value={dnsType} onChange={(e) => setDnsType(e.target.value)}>
                  {['A','AAAA','CNAME','MX','NS','TXT','SOA','PTR','SRV','CAA','ANY'].map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div className="w-44">
                <div className="text-xs font-medium text-gray-600 mb-1">Resolver (opt)</div>
                <input className="input" value={dnsResolver} placeholder="1.1.1.1"
                  onChange={(e) => setDnsResolver(e.target.value)} />
              </div>
            </>)}
            {tool === 'port' && (<>
              <div className="flex-1 min-w-[200px]">
                <div className="text-xs font-medium text-gray-600 mb-1">Target</div>
                <input className="input" value={target} placeholder="smtp.example.com"
                  onChange={(e) => onTargetChange(e.target.value)} onKeyDown={onKey} />
              </div>
              <div className="w-24">
                <div className="text-xs font-medium text-gray-600 mb-1">Port</div>
                <input type="number" className="input" min={1} max={65535} value={portTestPort}
                  onChange={(e) => setPortTestPort(parseInt(e.target.value) || 443)} />
              </div>
              <div className="w-24">
                <div className="text-xs font-medium text-gray-600 mb-1">Protocol</div>
                <select className="input" value={portTestProto}
                  onChange={(e) => setPortTestProto(e.target.value as 'tcp' | 'udp')}>
                  <option value="tcp">TCP</option>
                  <option value="udp">UDP</option>
                </select>
              </div>
            </>)}
            {tool === 'public_ip' && (
              <div className="w-40">
                <div className="text-xs font-medium text-gray-600 mb-1">IP family</div>
                <select className="input" value={publicIpFamily}
                  onChange={(e) => setPublicIpFamily(e.target.value as 'auto' | 'v4' | 'v6')}>
                  <option value="auto">auto (default)</option>
                  <option value="v4">IPv4 only (curl -4)</option>
                  <option value="v6">IPv6 only (curl -6)</option>
                </select>
              </div>
            )}
            {tool === 'capture' && (<>
              <div className="w-40">
                <div className="text-xs font-medium text-gray-600 mb-1">Interface</div>
                <select className="input" value={iface} onChange={(e) => setIface(e.target.value)}>
                  {interfaces.map((i) => <option key={i} value={i}>{i}</option>)}
                </select>
              </div>
              <div className="w-24">
                <div className="text-xs font-medium text-gray-600 mb-1">Max packets</div>
                <input type="number" className="input" min={1} max={500} value={captureCount}
                  onChange={(e) => setCaptureCount(parseInt(e.target.value) || 50)} />
              </div>
              <div className="flex-1 min-w-[200px]">
                <div className="text-xs font-medium text-gray-600 mb-1">BPF filter (optional)</div>
                <input className="input font-mono text-sm" value={captureFilter}
                  placeholder="port 443, icmp, host 1.1.1.1"
                  onChange={(e) => setCaptureFilter(e.target.value)} />
              </div>
            </>)}
            {tool === 'conntrack' && (
              <div className="flex-1 min-w-[200px]">
                <div className="text-xs font-medium text-gray-600 mb-1">Filter (optional)</div>
                <input className="input font-mono text-sm" value={conntrackFilter}
                  placeholder="tcp, udp, icmp or a source IP (e.g. 192.168.1.10)"
                  onChange={(e) => setConntrackFilter(e.target.value)} onKeyDown={onKey} />
              </div>
            )}
            <button className="btn-primary" onClick={run}
              disabled={busy || (
                tool === 'capture' ? !iface :
                ['routes','addresses','nft','conntrack','public_ip'].includes(tool) ? false :
                !target
              )}>
              {busy ? 'In progress...' : 'Run'}
            </button>
          </div>

          <div className="text-xs text-gray-600">
            {tool === 'ping' && 'Sends 4 ICMP echo packets by default. Target: IP or name.'}
            {tool === 'traceroute' && 'Lists routers traversed (max 20 hops).'}
            {tool === 'dns' && 'Resolution via dig. Name required (not an IP). Optional resolver to compare multiple DNS.'}
            {tool === 'port' && 'TCP uses a real handshake (reliable). UDP relies on ICMP unreachable replies (less reliable, false negatives possible).'}
            {tool === 'capture' && 'tcpdump 500 packets max over 20s. BPF syntax: port 443, host 1.1.1.1, icmp, not arp.'}
            {tool === 'conntrack' && 'Lists active connections tracked by netfilter (capped at 200). Useful to inspect NAT, debug stuck states.'}
            {tool === 'routes' && 'Real-time view of the kernel routing table.'}
            {tool === 'addresses' && 'IPs assigned to each interface, real time.'}
            {tool === 'nft' && 'nftables ruleset loaded in the kernel. This is what actually runs, not the MurOS DB config.'}
            {tool === 'public_ip' && 'Asks several well-known providers (ifconfig.me, api.ipify.org, icanhazip.com, ipinfo.io) for the WAN egress IP. Divergent answers may indicate a captive portal, transparent proxy, or dual-WAN.'}
          </div>
        </div>

        {err && (
          <ErrorBlock message={err} />
        )}

        {result && (
          <div className="card !p-0 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-xs text-gray-500 uppercase tracking-wide">Result</span>
                <span className="font-mono text-xs text-gray-700 truncate" title={result.command}>{result.command}</span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  className="text-xs px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-100 text-gray-700"
                  onClick={() => {
                    const text = (result.stdout || '') + (result.stderr ? '\n' + result.stderr : '')
                    navigator.clipboard.writeText(text).catch(() => {})
                  }}
                  title="Copy stdout+stderr to clipboard"
                >Copy</button>
                <button
                  className="text-xs px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-100 text-gray-700"
                  onClick={() => {
                    const ts = new Date().toISOString().replace(/[:T.]/g, '-').slice(0, 19)
                    const header = `# command: ${result.command}\n# duration_ms: ${result.duration_ms}\n# exit: ${result.returncode}${result.timed_out ? ' (timeout)' : ''}\n\n`
                    const body = header + (result.stdout || '') + (result.stderr ? '\n--- stderr ---\n' + result.stderr : '')
                    const blob = new Blob([body], { type: 'text/plain' })
                    const url = URL.createObjectURL(blob)
                    const a = document.createElement('a')
                    a.href = url
                    a.download = `diag-${tool}-${ts}.txt`
                    a.click()
                    URL.revokeObjectURL(url)
                  }}
                  title="Download as .txt"
                >Download</button>
                <span className="text-xs text-gray-500 font-mono whitespace-nowrap ml-1">{result.duration_ms} ms</span>
                <span className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded ${
                  result.timed_out ? 'bg-amber-100 text-amber-800 border border-amber-200'
                    : result.returncode === 0 ? 'bg-emerald-100 text-emerald-800 border border-emerald-200'
                    : 'bg-red-100 text-red-800 border border-red-200'
                }`}>
                  {result.timed_out ? 'timeout' : result.returncode === 0 ? 'success' : `exit ${result.returncode}`}
                </span>
              </div>
            </div>
            <pre ref={outputRef} className="font-mono text-[12.5px] text-gray-800 p-4 overflow-x-auto whitespace-pre-wrap min-h-[200px] max-h-[500px] overflow-y-auto leading-relaxed bg-white">
              {result.stdout || ''}
              {result.stderr && (
                <span className="text-red-700">{result.stderr}</span>
              )}
              {!result.stdout && !result.stderr && (
                <span className="text-gray-500">(no output)</span>
              )}
            </pre>
          </div>
        )}
        {!result && !busy && !err && (
          <div className="border border-gray-200 bg-gray-50 rounded-md overflow-hidden">
            <div className="px-3 py-12 text-center text-sm text-gray-600">
              Enter the target if needed, then click Run.
            </div>
          </div>
        )}
        </div>
      </div>
    </div>
  )
}
