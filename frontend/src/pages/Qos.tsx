// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useState } from 'react'
import {
  api,
  type Interface,
  type QosShaper,
  type QosClass,
  type QosRule,
  type QosClassInput,
  type QosRuleInput,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import Modal from '../components/Modal'
import ApplyServiceButton from '../components/ApplyServiceButton'
import { useConfirm } from '../components/ConfirmModal'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { Gauge, Plus, Pencil, Trash2 } from 'lucide-react'

const PRIORITY_LABELS: Record<number, string> = {
  0: '0 - Highest (VoIP)', 1: '1', 2: '2', 3: '3 - Normal',
  4: '4', 5: '5', 6: '6', 7: '7 - Lowest (bulk)',
}

export default function Qos() {
  const [shapers, setShapers] = useState<QosShaper[]>([])
  const [ifaces, setIfaces] = useState<Interface[]>([])
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [formDirty] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  const [shaperModal, setShaperModal] = useState<QosShaper | 'new' | null>(null)
  const [classModal, setClassModal] = useState<{ shaper: QosShaper; cls: QosClass | 'new' } | null>(null)
  const [ruleModal, setRuleModal] = useState<{ cls: QosClass; rule: QosRule | 'new' } | null>(null)

  const reload = async () => {
    try {
      const [s, i] = await Promise.all([api.qos.listShapers(), api.interfaces.list()])
      setShapers(s); setIfaces(i)
    } catch (e) { setError((e as Error).message) }
  }
  useEffect(() => { void reload() }, [])

  const ifaceName = (id: number) => ifaces.find((i) => i.id === id)?.name ?? `#${id}`

  const removeShaper = async (sh: QosShaper) => {
    if (!await confirm({
      title: 'Delete shaper?',
      message: `Remove shaping on ${sh.interface_name ?? ifaceName(sh.interface_id)}? The qdisc tree is torn down on Apply.`,
      confirmLabel: 'Delete', destructive: true,
    })) return
    try { await api.qos.removeShaper(sh.id); setMessage('Shaper deleted. Click Apply to update the kernel.'); await reload() }
    catch (e) { setError((e as Error).message) }
  }

  const removeClass = async (cls: QosClass) => {
    if (!await confirm({ title: 'Delete class?', message: `Remove class "${cls.name}" and its rules?`, confirmLabel: 'Delete', destructive: true })) return
    try { await api.qos.removeClass(cls.id); setMessage('Class deleted. Click Apply.'); await reload() }
    catch (e) { setError((e as Error).message) }
  }

  const removeRule = async (rule: QosRule) => {
    try { await api.qos.removeRule(rule.id); setMessage('Rule deleted. Click Apply.'); await reload() }
    catch (e) { setError((e as Error).message) }
  }

  return (
    <div>
      <PageHeader
        icon={<Gauge size={16} />}
        title="QoS / Traffic shaping"
        description="Egress bandwidth prioritisation (HTB + fq_codel)."
        actions={
          <ApplyServiceButton
            service="qos"
            pendingTooltip="Rebuild the tc qdisc tree on the shaped interfaces."
            onApplied={() => { void reload(); setMessage('Traffic shaping applied.') }}
            onError={setError}
            formDirty={formDirty}
          />
        }
      />

      <div className="px-6 py-4 space-y-4">
        {error && <ErrorBlock message={error} />}
        {message && <SuccessBlock message={message} onDismiss={() => setMessage(null)} />}

        <div className="text-xs text-gray-600 bg-slate-50 border border-slate-200 rounded p-3">
          Shaping applies to <strong>outbound</strong> traffic, where the queue forms. Set the
          bandwidth to about <strong>95% of the real uplink rate</strong> so the queue stays inside
          MurOS and not in the ISP modem. Traffic is sorted into priority classes; unmatched traffic
          falls into the default class.
        </div>

        <div className="flex justify-end">
          <button className="btn-primary" onClick={() => setShaperModal('new')}>
            <Plus size={14} className="inline -mt-0.5 mr-1" />Add shaper
          </button>
        </div>

        {shapers.length === 0 && (
          <div className="text-sm text-gray-500 text-center py-8 border border-dashed border-slate-200 rounded">
            No shaper yet. Add one on the interface facing your uplink (typically the WAN).
          </div>
        )}

        {shapers.map((sh) => (
          <div key={sh.id} className="card">
            <div className="flex items-start justify-between gap-3 mb-3">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold">{sh.interface_name ?? ifaceName(sh.interface_id)}</h2>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${sh.enabled ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-200 text-gray-600'}`}>
                    {sh.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
                <div className="text-sm text-gray-600 mt-0.5">
                  {sh.bandwidth_kbit.toLocaleString()} kbit/s egress
                  {sh.comment ? ` - ${sh.comment}` : ''}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button className="btn-secondary" onClick={() => setShaperModal(sh)} title="Edit shaper"><Pencil size={14} /></button>
                <button className="btn-danger" onClick={() => removeShaper(sh)} title="Delete shaper"><Trash2 size={14} /></button>
              </div>
            </div>

            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b border-slate-200">
                  <th className="py-1.5">Class</th>
                  <th>Priority</th>
                  <th>Rate</th>
                  <th>Ceil</th>
                  <th>Rules</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {[...sh.classes].sort((a, b) => a.priority - b.priority).map((cls) => (
                  <tr key={cls.id} className="border-b border-slate-100 align-top">
                    <td className="py-2 font-medium">
                      {cls.name}
                      {cls.is_default && <span className="ml-1 text-xs text-gray-500">(default)</span>}
                    </td>
                    <td>{cls.priority}</td>
                    <td>{cls.rate_kbit.toLocaleString()} kbit/s</td>
                    <td>{cls.ceil_kbit ? `${cls.ceil_kbit.toLocaleString()} kbit/s` : 'link max'}</td>
                    <td>
                      <div className="space-y-1">
                        {cls.rules.length === 0 && <span className="text-xs text-gray-400">no rule</span>}
                        {cls.rules.map((r) => (
                          <div key={r.id} className="flex items-center gap-2 text-xs">
                            <code className="font-mono bg-slate-100 px-1 rounded">{ruleLabel(r)}</code>
                            <button className="text-gray-400 hover:text-gray-700" onClick={() => setRuleModal({ cls, rule: r })} title="Edit rule"><Pencil size={11} /></button>
                            <button className="text-gray-400 hover:text-red-600" onClick={() => removeRule(r)} title="Delete rule"><Trash2 size={11} /></button>
                          </div>
                        ))}
                        <button className="text-xs text-blue-600 hover:underline" onClick={() => setRuleModal({ cls, rule: 'new' })}>+ rule</button>
                      </div>
                    </td>
                    <td className="text-right">
                      <button className="text-gray-400 hover:text-gray-700 mr-2" onClick={() => setClassModal({ shaper: sh, cls })} title="Edit class"><Pencil size={13} /></button>
                      <button className="text-gray-400 hover:text-red-600" onClick={() => removeClass(cls)} title="Delete class"><Trash2 size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="btn-secondary mt-3" onClick={() => setClassModal({ shaper: sh, cls: 'new' })}>
              <Plus size={14} className="inline -mt-0.5 mr-1" />Add class
            </button>
          </div>
        ))}
      </div>

      {shaperModal && (
        <ShaperForm
          shaper={shaperModal === 'new' ? null : shaperModal}
          ifaces={ifaces}
          usedIfaceIds={shapers.map((s) => s.interface_id)}
          onClose={() => setShaperModal(null)}
          onSaved={() => { setShaperModal(null); setMessage('Shaper saved. Click Apply.'); void reload() }}
          onError={setError}
        />
      )}
      {classModal && (
        <ClassForm
          shaperId={classModal.shaper.id}
          cls={classModal.cls === 'new' ? null : classModal.cls}
          onClose={() => setClassModal(null)}
          onSaved={() => { setClassModal(null); setMessage('Class saved. Click Apply.'); void reload() }}
          onError={setError}
        />
      )}
      {ruleModal && (
        <RuleForm
          classId={ruleModal.cls.id}
          rule={ruleModal.rule === 'new' ? null : ruleModal.rule}
          onClose={() => setRuleModal(null)}
          onSaved={() => { setRuleModal(null); setMessage('Rule saved. Click Apply.'); void reload() }}
          onError={setError}
        />
      )}
      <ConfirmHost />
    </div>
  )
}

function ruleLabel(r: QosRule): string {
  const parts: string[] = []
  if (r.dscp !== null) parts.push(`dscp ${r.dscp}`)
  if (r.protocol) parts.push(r.protocol)
  if (r.dst_port !== null) parts.push(`port ${r.dst_port}`)
  if (r.src_address) parts.push(`src ${r.src_address}`)
  if (r.dst_address) parts.push(`dst ${r.dst_address}`)
  const label = parts.length ? parts.join(' ') : 'any'
  return r.enabled ? label : `${label} (off)`
}

function ShaperForm({ shaper, ifaces, usedIfaceIds, onClose, onSaved, onError }: {
  shaper: QosShaper | null
  ifaces: Interface[]
  usedIfaceIds: number[]
  onClose: () => void
  onSaved: () => void
  onError: (m: string) => void
}) {
  const [interfaceId, setInterfaceId] = useState<number>(shaper?.interface_id ?? ifaces[0]?.id ?? 0)
  const [bandwidth, setBandwidth] = useState<number>(shaper?.bandwidth_kbit ?? 95000)
  const [enabled, setEnabled] = useState<boolean>(shaper?.enabled ?? true)
  const [comment, setComment] = useState<string>(shaper?.comment ?? '')
  const [busy, setBusy] = useState(false)

  const available = ifaces.filter((i) => shaper?.interface_id === i.id || !usedIfaceIds.includes(i.id))

  const submit = async () => {
    setBusy(true)
    try {
      const body = { interface_id: interfaceId, bandwidth_kbit: bandwidth, enabled, comment: comment || null }
      if (shaper) await api.qos.updateShaper(shaper.id, body)
      else await api.qos.createShaper(body)
      onSaved()
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal open onClose={onClose} title={shaper ? 'Edit shaper' : 'Add shaper'}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={submit} disabled={busy || !interfaceId}>{busy ? 'Saving...' : 'Save'}</button></>}>
      <div className="space-y-3">
        <Field label="Interface" hint="The interface whose outbound traffic is shaped (usually the WAN).">
          <select className="input" value={interfaceId} onChange={(e) => setInterfaceId(Number(e.target.value))} disabled={!!shaper}>
            {available.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
          </select>
        </Field>
        <Field label="Egress bandwidth (kbit/s)" hint="Set to ~95% of the real uplink rate.">
          <input type="number" className="input" value={bandwidth} onChange={(e) => setBandwidth(Number(e.target.value) || 0)} />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        <Field label="Comment (optional)">
          <input className="input" value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
      </div>
    </Modal>
  )
}

function ClassForm({ shaperId, cls, onClose, onSaved, onError }: {
  shaperId: number
  cls: QosClass | null
  onClose: () => void
  onSaved: () => void
  onError: (m: string) => void
}) {
  const [name, setName] = useState(cls?.name ?? '')
  const [priority, setPriority] = useState<number>(cls?.priority ?? 3)
  const [rate, setRate] = useState<number>(cls?.rate_kbit ?? 1000)
  const [ceil, setCeil] = useState<string>(cls?.ceil_kbit != null ? String(cls.ceil_kbit) : '')
  const [isDefault, setIsDefault] = useState<boolean>(cls?.is_default ?? false)
  const [comment, setComment] = useState(cls?.comment ?? '')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    try {
      const body: QosClassInput = {
        name, priority, rate_kbit: rate,
        ceil_kbit: ceil.trim() === '' ? null : Number(ceil),
        is_default: isDefault, comment: comment || null,
      }
      if (cls) await api.qos.updateClass(cls.id, body)
      else await api.qos.createClass(shaperId, body)
      onSaved()
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal open onClose={onClose} title={cls ? 'Edit class' : 'Add class'}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={submit} disabled={busy || !name.trim()}>{busy ? 'Saving...' : 'Save'}</button></>}>
      <div className="space-y-3">
        <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Voice" /></Field>
        <Field label="Priority" hint="0 is served first under contention; use it for VoIP / interactive.">
          <select className="input" value={priority} onChange={(e) => setPriority(Number(e.target.value))}>
            {Object.entries(PRIORITY_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        <Field label="Guaranteed rate (kbit/s)" hint="Minimum bandwidth this class always gets.">
          <input type="number" className="input" value={rate} onChange={(e) => setRate(Number(e.target.value) || 0)} />
        </Field>
        <Field label="Ceil (kbit/s, optional)" hint="Max it may borrow up to. Empty = link maximum.">
          <input type="number" className="input" value={ceil} onChange={(e) => setCeil(e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
          Default class (catch-all for unmatched traffic)
        </label>
        <Field label="Comment (optional)"><input className="input" value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
      </div>
    </Modal>
  )
}

function RuleForm({ classId, rule, onClose, onSaved, onError }: {
  classId: number
  rule: QosRule | null
  onClose: () => void
  onSaved: () => void
  onError: (m: string) => void
}) {
  const [protocol, setProtocol] = useState<string>(rule?.protocol ?? '')
  const [dstPort, setDstPort] = useState<string>(rule?.dst_port != null ? String(rule.dst_port) : '')
  const [src, setSrc] = useState(rule?.src_address ?? '')
  const [dst, setDst] = useState(rule?.dst_address ?? '')
  const [dscp, setDscp] = useState<string>(rule?.dscp != null ? String(rule.dscp) : '')
  const [enabled, setEnabled] = useState<boolean>(rule?.enabled ?? true)
  const [comment, setComment] = useState(rule?.comment ?? '')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    try {
      const body: QosRuleInput = {
        protocol: protocol === '' ? null : (protocol as 'tcp' | 'udp'),
        dst_port: dstPort.trim() === '' ? null : Number(dstPort),
        src_address: src.trim() || null,
        dst_address: dst.trim() || null,
        dscp: dscp.trim() === '' ? null : Number(dscp),
        enabled, comment: comment || null,
      }
      if (rule) await api.qos.updateRule(rule.id, body)
      else await api.qos.createRule(classId, body)
      onSaved()
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal open onClose={onClose} title={rule ? 'Edit rule' : 'Add rule'}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={submit} disabled={busy}>{busy ? 'Saving...' : 'Save'}</button></>}>
      <div className="space-y-3">
        <div className="text-xs text-gray-600">Leave a field empty to match any. An empty rule matches all remaining traffic.</div>
        <Field label="Protocol">
          <select className="input" value={protocol} onChange={(e) => setProtocol(e.target.value)}>
            <option value="">any</option><option value="tcp">tcp</option><option value="udp">udp</option>
          </select>
        </Field>
        <Field label="Destination port" hint="Single port (e.g. 5060 for SIP).">
          <input type="number" className="input" value={dstPort} onChange={(e) => setDstPort(e.target.value)} />
        </Field>
        <Field label="DSCP" hint="0-63. 46 = EF (voice), 0 = best effort.">
          <input type="number" className="input" value={dscp} onChange={(e) => setDscp(e.target.value)} />
        </Field>
        <Field label="Source address / CIDR"><input className="input" value={src} onChange={(e) => setSrc(e.target.value)} placeholder="192.168.1.0/24" /></Field>
        <Field label="Destination address / CIDR"><input className="input" value={dst} onChange={(e) => setDst(e.target.value)} /></Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        <Field label="Comment (optional)"><input className="input" value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
      </div>
    </Modal>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-sm font-medium mb-1">{label}</div>
      {children}
      {hint && <div className="text-xs text-gray-600 mt-1">{hint}</div>}
    </label>
  )
}
