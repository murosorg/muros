import { useEffect, useState } from 'react'
import { ArrowRight, ChevronDown, ChevronRight } from 'lucide-react'
import { api, FirewallRule, Zone, ServiceGroup, AddressGroup } from '../lib/api'
import Toggle from './Toggle'

type Props = {
  rule?: FirewallRule
  zones: Zone[]
  defaultChain?: FirewallRule['chain']
  onSubmit: (data: Partial<FirewallRule>) => Promise<void>
  onCancel: () => void
}

const empty: Partial<FirewallRule> = {
  position: 0,
  chain: 'forward',
  action: 'accept',
  src_zone_id: null,
  dst_zone_id: null,
  src_address: null,
  dst_address: null,
  protocol: null,
  src_port: null,
  dst_port: null,
  log: false,
  enabled: true,
  comment: null,
  rate_limit: null,
  service_group_id: null,
  src_address_group_id: null,
  dst_address_group_id: null,
}

const CHAIN_LABEL: Record<FirewallRule['chain'], string> = {
  input: 'input (to the firewall)',
  forward: 'forward (traverses the firewall)',
  output: 'output (from the firewall)',
}

// Quick-fill presets for the most common rule shapes. Picking one sets
// protocol + destination port and clears any service group selection.
type QuickService = { label: string; protocol: 'tcp' | 'udp' | 'icmp'; port: string | null }
const QUICK_SERVICES: QuickService[] = [
  { label: 'SSH', protocol: 'tcp', port: '22' },
  { label: 'HTTP', protocol: 'tcp', port: '80' },
  { label: 'HTTPS', protocol: 'tcp', port: '443' },
  { label: 'DNS', protocol: 'udp', port: '53' },
  { label: 'Ping', protocol: 'icmp', port: null },
]

export default function RuleForm({ rule, zones, defaultChain, onSubmit, onCancel }: Props) {
  const [data, setData] = useState<Partial<FirewallRule>>(rule || { ...empty, chain: defaultChain || empty.chain })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [serviceGroups, setServiceGroups] = useState<ServiceGroup[]>([])
  const [addressGroups, setAddressGroups] = useState<AddressGroup[]>([])
  const [showAdvanced, setShowAdvanced] = useState(false)

  useEffect(() => {
    setData(rule || empty)
  }, [rule])

  useEffect(() => {
    void api.serviceGroups.list().then(setServiceGroups).catch(() => undefined)
    void api.addressGroups.list().then(setAddressGroups).catch(() => undefined)
  }, [])

  const useServiceGroup = data.service_group_id != null

  const set = <K extends keyof FirewallRule>(k: K, v: FirewallRule[K] | null) =>
    setData((d) => ({ ...d, [k]: v }))

  // Picking a quick service overrides any group binding and writes the
  // protocol + destination port directly. `Ping` clears the port (icmp).
  const applyQuickService = (s: QuickService) => {
    setData((d) => ({
      ...d,
      service_group_id: null,
      protocol: s.protocol,
      dst_port: s.port,
      src_port: null,
    }))
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const payload = { ...data }
      for (const k of ['src_address', 'dst_address', 'src_port', 'dst_port', 'comment', 'rate_limit'] as const) {
        if (payload[k] === '') payload[k] = null
      }
      if (payload.protocol === 'any' || !payload.protocol) payload.protocol = null
      await onSubmit(payload)
    } catch (e) {
      setError(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const chainLocked = !!defaultChain && !rule

  // Warn the operator when the rule being edited is a forward accept
  // with no matcher at all: it accepts every flow and effectively
  // disables filtering. Common foot-gun when "unblocking" a service.
  const overlyPermissive =
    data.chain === 'forward' &&
    data.action === 'accept' &&
    (data.enabled ?? true) &&
    !data.src_zone_id && !data.dst_zone_id &&
    !data.src_address && !data.dst_address &&
    !data.src_address_group_id && !data.dst_address_group_id &&
    !data.src_port && !data.dst_port &&
    !data.service_group_id &&
    (!data.protocol || data.protocol === 'any')

  return (
    <div className="space-y-5">
      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 px-3 py-2 rounded">
          {error}
        </div>
      )}

      {/* 1. Identity: a short label for the rule (free text). */}
      <div>
        <label className="label">Description</label>
        <input
          className="input"
          placeholder="e.g. Allow LAN to Internet"
          value={data.comment ?? ''}
          onChange={(e) => set('comment', e.target.value)}
          autoFocus
        />
      </div>

      {/* 2. Action: 3 visual buttons. Picked color hints at semantics. */}
      <div>
        <label className="label">Action</label>
        <div className="grid grid-cols-3 gap-2">
          <ActionButton
            picked={data.action === 'accept'}
            color="emerald"
            label="Accept"
            onClick={() => set('action', 'accept')}
          />
          <ActionButton
            picked={data.action === 'drop'}
            color="red"
            label="Drop"
            onClick={() => set('action', 'drop')}
          />
          <ActionButton
            picked={data.action === 'reject'}
            color="amber"
            label="Reject"
            onClick={() => set('action', 'reject')}
          />
        </div>
      </div>

      {/* 3. Where: source zone -> destination zone (visual flow). */}
      <div>
        <label className="label">Traffic flow</label>
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
          <select
            className="select"
            value={data.src_zone_id ?? ''}
            onChange={(e) => set('src_zone_id', e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">From any zone</option>
            {zones.map((z) => (
              <option key={z.id} value={z.id}>From {z.name}</option>
            ))}
          </select>
          <ArrowRight size={16} className="text-gray-400" />
          <select
            className="select"
            value={data.dst_zone_id ?? ''}
            onChange={(e) => set('dst_zone_id', e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">To any zone</option>
            {zones.map((z) => (
              <option key={z.id} value={z.id}>To {z.name}</option>
            ))}
          </select>
        </div>
      </div>

      {/* 4. Service: quick chips + custom port/protocol or saved group. */}
      <div>
        <div className="flex items-center justify-between">
          <label className="label mb-0">Service</label>
          <div className="flex gap-1 flex-wrap">
            {QUICK_SERVICES.map((s) => {
              const picked = !useServiceGroup && data.protocol === s.protocol
                && (data.dst_port ?? null) === s.port
              return (
                <button
                  key={s.label}
                  type="button"
                  onClick={() => applyQuickService(s)}
                  className={`text-[11px] px-2 py-0.5 rounded border ${
                    picked
                      ? 'border-steel-400 bg-steel-50 text-steel-900'
                      : 'border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  {s.label}
                </button>
              )
            })}
          </div>
        </div>

        <select
          className="select mt-2"
          value={data.service_group_id ?? ''}
          onChange={(e) => set('service_group_id', e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">Custom (protocol and ports below)</option>
          {serviceGroups.map((g) => (
            <option key={g.id} value={g.id}>
              Group: {g.name} ({g.ports.map(p => `${p.protocol}/${p.port}`).join(', ') || 'empty'})
            </option>
          ))}
        </select>

        <div className="grid grid-cols-[120px_1fr] gap-2 mt-2">
          <select
            className="select"
            value={data.protocol ?? 'any'}
            onChange={(e) => set('protocol', e.target.value as FirewallRule['protocol'])}
            disabled={useServiceGroup}
          >
            <option value="any">any proto</option>
            <option value="tcp">tcp</option>
            <option value="udp">udp</option>
            <option value="icmp">icmp</option>
          </select>
          <input
            className="input font-mono"
            placeholder={useServiceGroup ? 'Defined by the group' : 'Destination port (e.g. 22 or 80,443)'}
            value={useServiceGroup ? '' : (data.dst_port ?? '')}
            onChange={(e) => set('dst_port', e.target.value)}
            disabled={useServiceGroup || data.protocol === 'icmp'}
          />
        </div>
      </div>

      {/* 5. Addresses: optional narrowing. Default empty = any. */}
      <div className="grid grid-cols-2 gap-3">
        <AddressField
          label="Source address"
          groupId={data.src_address_group_id ?? null}
          onGroupChange={(id) => set('src_address_group_id', id)}
          value={data.src_address ?? ''}
          onValueChange={(v) => set('src_address', v)}
          groups={addressGroups}
          placeholder="any (e.g. 192.168.1.0/24)"
        />
        <AddressField
          label="Destination address"
          groupId={data.dst_address_group_id ?? null}
          onGroupChange={(id) => set('dst_address_group_id', id)}
          value={data.dst_address ?? ''}
          onValueChange={(v) => set('dst_address', v)}
          groups={addressGroups}
          placeholder="any (e.g. 10.0.0.0/8)"
        />
      </div>

      {/* 6. Enabled toggle and chain badge when locked by the page. */}
      <div className="flex items-center gap-4 pt-1">
        <label className="flex items-center gap-2 text-sm">
          <Toggle checked={data.enabled ?? true} onChange={(v) => set('enabled', v)} />
          Enabled
        </label>
        {chainLocked && (
          <span className="text-xs text-gray-600">
            Chain: <span className="font-mono">{CHAIN_LABEL[data.chain || 'forward']}</span>
          </span>
        )}
      </div>

      {/* 7. Advanced: position, src port, log, chain override, rate limit. */}
      <div className="border border-gray-200 rounded">
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="w-full flex items-center gap-1.5 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          Advanced options
        </button>
        {showAdvanced && (
          <div className="px-3 pb-3 pt-1 space-y-3 border-t border-gray-200">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Position</label>
                <input
                  type="number"
                  className="input"
                  value={data.position ?? 0}
                  onChange={(e) => set('position', Number(e.target.value))}
                  placeholder="0 = append"
                />
              </div>
              <div>
                <label className="label">Source port</label>
                <input
                  className="input font-mono"
                  placeholder="any (e.g. 1024-65535)"
                  value={useServiceGroup ? '' : (data.src_port ?? '')}
                  onChange={(e) => set('src_port', e.target.value)}
                  disabled={useServiceGroup || data.protocol === 'icmp'}
                />
              </div>
            </div>

            {!chainLocked && (
              <div>
                <label className="label">Chain</label>
                <select
                  className="select"
                  value={data.chain || 'forward'}
                  onChange={(e) => set('chain', e.target.value as FirewallRule['chain'])}
                >
                  <option value="input">{CHAIN_LABEL.input}</option>
                  <option value="forward">{CHAIN_LABEL.forward}</option>
                  <option value="output">{CHAIN_LABEL.output}</option>
                </select>
              </div>
            )}

            <label className="flex items-center gap-2 text-sm">
              <Toggle checked={data.log ?? false} onChange={(v) => set('log', v)} />
              Log matching packets to muros-fw
            </label>

            <RateLimitField
              value={data.rate_limit ?? null}
              onChange={(v) => set('rate_limit', v)}
            />
          </div>
        )}
      </div>

      {overlyPermissive && (
        <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2">
          This rule accepts every forwarded flow (no zone, address,
          protocol or port set). It effectively disables filtering on
          the forward chain. Restrict it to a zone, subnet or service
          before saving.
        </div>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
        <button className="btn-primary" onClick={handleSubmit} disabled={submitting}>
          {submitting ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}

function ActionButton({
  picked, color, label, onClick,
}: {
  picked: boolean
  color: 'emerald' | 'red' | 'amber'
  label: string
  onClick: () => void
}) {
  const palette = {
    emerald: 'border-emerald-400 bg-emerald-50 text-emerald-900',
    red:     'border-red-400 bg-red-50 text-red-900',
    amber:   'border-amber-400 bg-amber-50 text-amber-900',
  }[color]
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-sm font-medium px-3 py-2 rounded border transition-colors ${
        picked ? palette : 'border-gray-300 hover:bg-gray-50 text-gray-700'
      }`}
    >
      {label}
    </button>
  )
}

function AddressField({
  label, groupId, onGroupChange, value, onValueChange, groups, placeholder,
}: {
  label: string
  groupId: number | null
  onGroupChange: (id: number | null) => void
  value: string
  onValueChange: (v: string) => void
  groups: AddressGroup[]
  placeholder: string
}) {
  const useGroup = groupId != null
  return (
    <div>
      <label className="label">{label}</label>
      <select
        className="select mb-2"
        value={groupId ?? ''}
        onChange={(e) => onGroupChange(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">Custom</option>
        {groups.map((g) => (
          <option key={g.id} value={g.id}>Group: {g.name}</option>
        ))}
      </select>
      <input
        className="input font-mono"
        placeholder={useGroup ? 'Defined by the group' : placeholder}
        value={useGroup ? '' : value}
        onChange={(e) => onValueChange(e.target.value)}
        disabled={useGroup}
      />
    </div>
  )
}

type Unit = 'second' | 'minute' | 'hour' | 'day'

const UNIT_LABEL: Record<Unit, string> = {
  second: 'second',
  minute: 'minute',
  hour: 'hour',
  day: 'day',
}

const PRESETS: { label: string; rate: number; unit: Unit; burst?: number }[] = [
  { label: 'SSH anti-bruteforce', rate: 5, unit: 'minute', burst: 10 },
  { label: 'Anti-flood ICMP', rate: 10, unit: 'second' },
  { label: 'DNS cap', rate: 100, unit: 'second', burst: 200 },
  { label: 'Moderate API', rate: 60, unit: 'minute' },
]

function parseRateLimit(s: string | null): { rate: string; unit: Unit; burst: string } {
  if (!s) return { rate: '', unit: 'minute', burst: '' }
  // Format : N/unit[ burst M]
  const m = s.match(/^(\d+)\s*\/\s*(second|minute|hour|day)(?:\s+burst\s+(\d+))?$/)
  if (!m) return { rate: '', unit: 'minute', burst: '' }
  return { rate: m[1], unit: m[2] as Unit, burst: m[3] || '' }
}

function buildRateLimit(rate: string, unit: Unit, burst: string): string | null {
  const r = rate.trim()
  if (!r || !/^\d+$/.test(r)) return null
  const b = burst.trim()
  if (b && /^\d+$/.test(b)) return `${r}/${unit} burst ${b}`
  return `${r}/${unit}`
}

function RateLimitField({ value, onChange }: { value: string | null; onChange: (v: string | null) => void }) {
  const enabled = value !== null && value !== ''
  const initial = parseRateLimit(value)
  // On garde les champs en buffer local quand on coupe la limite, comme ca on
  // peut la rallumer sans tout retaper.
  const [rate, setRate] = useState(initial.rate || '5')
  const [unit, setUnit] = useState<Unit>(initial.unit)
  const [burst, setBurst] = useState(initial.burst)

  useEffect(() => {
    const p = parseRateLimit(value)
    if (p.rate) setRate(p.rate)
    setUnit(p.unit)
    setBurst(p.burst)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const commit = (r: string, u: Unit, b: string) => {
    onChange(buildRateLimit(r, u, b))
  }

  const toggle = (on: boolean) => {
    if (on) {
      // On (re)active : on construit avec les valeurs en buffer
      onChange(buildRateLimit(rate || '5', unit, burst))
    } else {
      onChange(null)
    }
  }

  const applyPreset = (p: typeof PRESETS[number]) => {
    const r = String(p.rate)
    const b = p.burst ? String(p.burst) : ''
    setRate(r); setUnit(p.unit); setBurst(b)
    commit(r, p.unit, b)
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <label className="label mb-0">Rate limit</label>
        <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
          <span className={enabled ? 'text-gray-900 font-medium' : 'text-gray-700'}>
            {enabled ? 'Enabled' : 'Disabled'}
          </span>
          <button
            type="button"
            onClick={() => toggle(!enabled)}
            className={`inline-flex h-5 w-9 rounded-full transition-colors ${enabled ? 'bg-steel-400' : 'bg-gray-300'}`}
            aria-pressed={enabled}
          >
            <span
              className={`inline-block h-4 w-4 my-0.5 rounded-full bg-white transition-transform ${enabled ? 'translate-x-4' : 'translate-x-0.5'}`}
            />
          </button>
        </label>
      </div>

      {enabled && (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              type="number"
              min={1}
              className="input w-24 font-mono"
              placeholder="5"
              value={rate}
              onChange={(e) => { setRate(e.target.value); commit(e.target.value, unit, burst) }}
            />
            <span className="text-sm text-gray-700">packets per</span>
            <select
              className="select w-32"
              value={unit}
              onChange={(e) => { const u = e.target.value as Unit; setUnit(u); commit(rate, u, burst) }}
            >
              {(['second', 'minute', 'hour', 'day'] as Unit[]).map((u) => (
                <option key={u} value={u}>{UNIT_LABEL[u]}</option>
              ))}
            </select>
            <span className="text-sm text-gray-700">burst</span>
            <input
              type="number"
              min={0}
              className="input w-24 font-mono"
              placeholder="any"
              value={burst}
              onChange={(e) => { setBurst(e.target.value); commit(rate, unit, e.target.value) }}
              title="Optional. Allows an initial burst above the average rate (e.g. 100/sec burst 200 = up to 200 packets then 100/sec)"
            />
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            <span className="text-[11px] text-gray-700 mr-1 self-center">Presets:</span>
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                className="text-[11px] px-2 py-0.5 border border-gray-300 rounded hover:bg-gray-50"
                onClick={() => applyPreset(p)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
