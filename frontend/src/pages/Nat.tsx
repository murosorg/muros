import { useEffect, useMemo, useRef, useState } from 'react'
import TableSkeleton from '../components/TableSkeleton'
import { api, Interface, NatRule, Zone } from '../lib/api'
import Modal from '../components/Modal'
import PageHeader from '../components/PageHeader'
import ApplyFirewallButton from '../components/ApplyFirewallButton'
import EmptyState from '../components/EmptyState'
import RulesetModal from '../components/RulesetModal'
import NatForm from '../components/NatForm'
import HelpTooltip from '../components/HelpTooltip'
import Toggle from '../components/Toggle'
import { ErrorBlock } from '../components/Alerts'
import { ArrowLeftRight } from 'lucide-react'
import { useConfirm } from '../components/ConfirmModal'

// In nftables, NAT lives in two hook chains:
//   - postrouting (priority srcnat) : masquerade + snat (rewrite source)
//   - prerouting  (priority dstnat) : dnat              (rewrite destination)
// MurOS derives the hook from the rule type in compiler.py. We surface
// that mapping in the UI as tabs so the operator sees the same model
// nftables actually runs.
type Hook = 'postrouting' | 'prerouting'
const HOOKS: Hook[] = ['postrouting', 'prerouting']

const HOOK_LABEL: Record<Hook, string> = {
  postrouting: 'Source NAT',
  prerouting: 'Destination NAT',
}

const HOOK_HINT: Record<Hook, string> = {
  postrouting:
    'postrouting (priority srcnat) : rewrites the source address of outgoing packets. Contains masquerade and snat rules.',
  prerouting:
    'prerouting (priority dstnat) : rewrites the destination address of incoming packets. Contains dnat (port forward) rules.',
}

const hookOf = (t: NatRule['type']): Hook =>
  t === 'dnat' ? 'prerouting' : 'postrouting'

const typeBadge = (t: NatRule['type']) => {
  if (t === 'masquerade') return 'badge bg-sky-50 text-sky-800 border border-sky-200'
  if (t === 'snat') return 'badge bg-violet-50 text-violet-800 border border-violet-200'
  return 'badge bg-amber-50 text-amber-800 border border-amber-200'
}

function formatTarget(r: NatRule): string {
  if (r.type === 'masquerade') return 'Interface IP'
  if (!r.redirect_to_ip) return '-'
  return r.redirect_to_port ? `${r.redirect_to_ip}:${r.redirect_to_port}` : r.redirect_to_ip
}

function formatService(r: NatRule): string {
  if (!r.protocol) return 'any'
  if (!r.dst_port) return r.protocol
  return `${r.protocol}/${r.dst_port}`
}

export default function Nat() {
  const [rules, setRules] = useState<NatRule[]>([])
  const [interfaces, setInterfaces] = useState<Interface[]>([])
  const [zones, setZones] = useState<Zone[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [hook, setHook] = useState<Hook>('postrouting')
  const [filter, setFilter] = useState('')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<NatRule | null>(null)
  const [showRuleset, setShowRuleset] = useState(false)
  const [quickSetupBusy, setQuickSetupBusy] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  const askDelete = async (r: NatRule) => {
    const ok = await confirm({
      title: 'Delete the NAT rule',
      message: (
        <p>The NAT rule <span className="font-mono text-gray-900">#{r.id}</span>
          {r.comment ? <> ({r.comment})</> : null} will be deleted.</p>
      ),
      destructive: true,
      requireText: 'delete',
    })
    if (!ok) return
    try { await api.nat.remove(r.id); reload() }
    catch (e) { setError((e as Error).message) }
  }

  const interfacesById = useMemo(() => {
    const m = new Map<number, Interface>()
    interfaces.forEach((i) => m.set(i.id, i))
    return m
  }, [interfaces])

  // Auto-detect the WAN interface from the zone named "wan"
  // (case-insensitive). The quick-setup banner only appears when we
  // find a plausible candidate.
  const wanInterface = useMemo(() => {
    const wanZone = zones.find((z) => z.name.toLowerCase() === 'wan')
    if (!wanZone) return null
    return interfaces.find((i) => i.zone_id === wanZone.id) || null
  }, [interfaces, zones])

  const hasMasqueradeToWan = useMemo(() => {
    if (!wanInterface) return false
    return rules.some((r) =>
      r.type === 'masquerade' && r.interface_id === wanInterface.id && !r.src_address && r.enabled
    )
  }, [rules, wanInterface])

  const reload = async () => {
    setLoading(true)
    try {
      const [n, i, z] = await Promise.all([api.nat.list(), api.interfaces.list(), api.zones.list()])
      setRules(n)
      setInterfaces(i)
      setZones(z)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [])

  const applyQuickMasquerade = async () => {
    if (!wanInterface) return
    setQuickSetupBusy(true)
    try {
      await api.nat.create({
        type: 'masquerade',
        position: 10,
        interface_id: wanInterface.id,
        src_address: null,
        dst_address: null,
        protocol: null,
        dst_port: null,
        redirect_to_ip: null,
        redirect_to_port: null,
        enabled: true,
        comment: 'MASQUERADE all to WAN (quick setup)',
      })
      await reload()
    } catch (e) {
      setError(String(e))
    } finally {
      setQuickSetupBusy(false)
    }
  }

  // Per-hook counts for the tab badges.
  const countByHook = useMemo(() => {
    const c: Record<Hook, number> = { postrouting: 0, prerouting: 0 }
    for (const r of rules) c[hookOf(r.type)]++
    return c
  }, [rules])

  const hookRules = useMemo(() => {
    let out = rules.filter((r) => hookOf(r.type) === hook)
    if (filter.trim()) {
      const needle = filter.toLowerCase()
      out = out.filter((r) => {
        const iface = r.interface_id ? interfacesById.get(r.interface_id)?.name || '' : ''
        const blob = [
          r.comment, iface, r.src_address, r.dst_address, r.protocol,
          r.dst_port, r.redirect_to_ip, r.redirect_to_port, r.type,
        ].filter(Boolean).join(' ').toLowerCase()
        return blob.includes(needle)
      })
    }
    return [...out].sort((a, b) => a.position - b.position || a.id - b.id)
  }, [rules, hook, filter, interfacesById])

  // Drag-and-drop only when no text filter is active (a partial view
  // would corrupt the global position sequence on reorder).
  const canReorder = !filter.trim() && hookRules.length > 1

  const toggleEnabled = async (r: NatRule) => {
    await api.nat.update(r.id, { enabled: !r.enabled })
    reload()
  }

  const [draggingId, setDraggingId] = useState<number | null>(null)
  const [dragOverId, setDragOverId] = useState<number | null>(null)
  const dragSourceIndex = useRef<number | null>(null)

  // Reorder within the current hook only. We rebuild the full id list
  // from the global rules (other hook rules keep their relative order)
  // and send it to /api/nat/rules/reorder which renumbers positions in
  // x10.
  const reorderLocal = (sourceId: number, targetId: number): NatRule[] => {
    const currentIds = hookRules.map((r) => r.id)
    const src = currentIds.indexOf(sourceId)
    if (src < 0) return rules
    currentIds.splice(src, 1)
    let dst = currentIds.indexOf(targetId)
    if (dst < 0) dst = currentIds.length
    currentIds.splice(dst, 0, sourceId)
    const reorderedHook = currentIds.map((id) => rules.find((r) => r.id === id)!).filter(Boolean)
    const otherHook = rules.filter((r) => hookOf(r.type) !== hook)
    // Other-hook rules stay in their current relative order, current
    // hook rules come right after. The backend will renumber globally.
    return [...otherHook, ...reorderedHook]
  }

  const onDragStart = (e: React.DragEvent, r: NatRule, idx: number) => {
    if (!canReorder) { e.preventDefault(); return }
    setDraggingId(r.id)
    dragSourceIndex.current = idx
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', String(r.id))
  }

  const onDragOver = (e: React.DragEvent, r: NatRule) => {
    if (!canReorder || draggingId === null) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (dragOverId !== r.id) setDragOverId(r.id)
  }

  const onDragLeave = () => { setDragOverId(null) }

  const onDrop = async (e: React.DragEvent, target: NatRule) => {
    e.preventDefault()
    const sourceIdStr = e.dataTransfer.getData('text/plain')
    const sourceId = parseInt(sourceIdStr, 10)
    setDraggingId(null)
    setDragOverId(null)
    if (!sourceId || sourceId === target.id || !canReorder) return
    const newOrder = reorderLocal(sourceId, target.id)
    setRules(newOrder)
    try {
      await api.nat.reorder(newOrder.map((r) => r.id))
      reload()
    } catch (err) {
      setError((err as Error).message)
      reload()
    }
  }

  const onDragEnd = () => { setDraggingId(null); setDragOverId(null) }

  return (
    <div>
      <PageHeader
        icon={<ArrowLeftRight size={16} />}
        title="Network Address Translation (NAT)"
        description="Source and destination NAT rules."
        actions={<ApplyFirewallButton onClick={() => setShowRuleset(true)} onView={() => setShowRuleset(true)} />}
      />

      {showRuleset && <RulesetModal onClose={() => setShowRuleset(false)} />}

      <div className="px-6 py-4">
        {error && <ErrorBlock message={error} />}

        {wanInterface && !hasMasqueradeToWan && rules.length === 0 && (
          <div className="bg-sky-50 border border-sky-200 rounded p-4 mb-4">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <div className="font-medium text-sky-900 text-sm">Quick setup</div>
                <div className="text-xs text-sky-800 mt-1">
                  No NAT rule yet. The most common setup is MASQUERADE all LAN traffic going out the WAN
                  interface <code className="font-mono">{wanInterface.name}</code>. Click below to create it.
                </div>
              </div>
              <button
                className="btn-primary whitespace-nowrap"
                onClick={applyQuickMasquerade}
                disabled={quickSetupBusy}
              >
                {quickSetupBusy ? 'Creating...' : `MASQUERADE all to ${wanInterface.name}`}
              </button>
            </div>
          </div>
        )}

        {/* Hook tabs : same pattern as Rules chain tabs. */}
        <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
          <div className="inline-flex rounded border border-gray-300 overflow-hidden">
            {HOOKS.map((h, i) => (
              <button
                key={h}
                title={HOOK_HINT[h]}
                className={
                  `px-3 py-1.5 text-sm inline-flex items-center gap-2 ${i > 0 ? 'border-l border-gray-300' : ''} ` +
                  (hook === h ? 'bg-steel-100 text-gray-900 font-medium' : 'bg-white text-gray-800 hover:bg-gray-50')
                }
                onClick={() => setHook(h)}
              >
                <span>{HOOK_LABEL[h]}</span>
                <span className="text-[10px] font-mono text-gray-500 lowercase">{h}</span>
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
                  {countByHook[h]}
                </span>
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2 flex-1 max-w-xl">
            <input
              className="input flex-1 py-1.5 text-sm"
              placeholder="Filter (comment, interface, IP, port...)"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            {filter && <button className="btn-ghost py-1 text-xs" onClick={() => setFilter('')}>x</button>}
          </div>

          <button className="btn-primary" onClick={() => setCreating(true)}>Add NAT rule</button>
        </div>

        {canReorder && (
          <div className="text-xs text-gray-600 mb-2 flex items-center gap-2">
            <span>Drag a row by its handle <span className="font-mono text-gray-500">⋮⋮</span> to reorder.</span>
            <HelpTooltip text="Rules are evaluated top to bottom within each hook chain. On drop, MurOS renumbers all positions as 10, 20, 30... in the database." />
          </div>
        )}

        <div className="border border-gray-200 rounded-md overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-800 text-xs uppercase tracking-wider">
              <tr>
                <th className="w-8"></th>
                <th className="text-center px-2 py-2 w-10">#</th>
                <th className="text-center px-2 py-2 w-14" title="Enable / disable this rule"></th>
                <th className="text-left px-2 py-2 w-28">Type</th>
                <th className="text-left px-2 py-2 w-28">Interface</th>
                <th className="text-left px-2 py-2">Source</th>
                <th className="text-left px-2 py-2">Destination</th>
                <th className="text-left px-2 py-2 w-28">Service</th>
                <th className="text-left px-2 py-2">To</th>
                <th className="text-left px-2 py-2">Comment</th>
                <th className="text-right px-2 py-2 w-24"></th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <TableSkeleton rows={5} cols={11} />
              )}
              {!loading && hookRules.length === 0 && (
                <tr><td colSpan={11}>
                  <EmptyState
                    icon={<ArrowLeftRight size={20} />}
                    text={filter
                      ? 'No NAT rule matches the filter'
                      : `No rule in the ${HOOK_LABEL[hook].toLowerCase()} chain`}
                    hint={filter
                      ? undefined
                      : hook === 'postrouting'
                        ? "Traffic leaves with its original source address. For a simple 'masquerade WAN out' case, add a first rule."
                        : 'No port forward is active. Add a DNAT rule to publish an internal service.'}
                    action={!filter && (
                      <button className="btn-primary" onClick={() => setCreating(true)}>Add NAT rule</button>
                    )}
                  />
                </td></tr>
              )}
              {hookRules.map((r, idx) => {
                const iface = r.interface_id ? interfacesById.get(r.interface_id) : undefined
                const dragging = draggingId === r.id
                const dropTarget = dragOverId === r.id && draggingId !== r.id
                return (
                  <tr
                    key={r.id}
                    draggable={canReorder}
                    onDragStart={(e) => onDragStart(e, r, idx)}
                    onDragOver={(e) => onDragOver(e, r)}
                    onDragLeave={onDragLeave}
                    onDrop={(e) => onDrop(e, r)}
                    onDragEnd={onDragEnd}
                    className={
                      `border-t border-gray-200 transition-colors ` +
                      (dragging ? 'opacity-30 ' : '') +
                      (dropTarget ? 'bg-blue-50 border-t-2 border-t-blue-400 ' : 'hover:bg-gray-50 ') +
                      (!r.enabled ? 'bg-gray-50/40 ' : '')
                    }
                  >
                    <td
                      className={`px-2 py-1.5 text-center text-gray-400 select-none ${canReorder ? 'cursor-grab active:cursor-grabbing' : 'cursor-not-allowed text-gray-200'}`}
                      title={canReorder ? 'Drag to reorder' : filter ? 'Clear the filter to reorder' : 'Need at least 2 rules to reorder'}
                    >
                      ⋮⋮
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs text-gray-600 text-center ${!r.enabled ? 'opacity-60' : ''}`}>
                      {idx + 1}
                    </td>
                    <td className={`px-2 py-1.5 text-center ${!r.enabled ? 'opacity-60' : ''}`}>
                      <Toggle size="sm" checked={r.enabled} onChange={() => toggleEnabled(r)} />
                    </td>
                    <td className={`px-2 py-1.5 ${!r.enabled ? 'opacity-60' : ''}`}>
                      <span className={typeBadge(r.type)}>{r.type}</span>
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs ${!iface ? 'text-gray-500 italic' : 'text-gray-800'} ${!r.enabled ? 'opacity-60' : ''}`}>
                      {iface ? iface.name : 'any'}
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs ${!r.src_address ? 'text-gray-500 italic' : 'text-gray-800'} ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.src_address || 'any'}
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs ${!r.dst_address ? 'text-gray-500 italic' : 'text-gray-800'} ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.dst_address || 'any'}
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs ${formatService(r) === 'any' ? 'text-gray-500 italic' : 'text-gray-700'} ${!r.enabled ? 'opacity-60' : ''}`}>
                      {formatService(r)}
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs text-gray-800 ${!r.enabled ? 'opacity-60' : ''}`}>
                      {formatTarget(r)}
                    </td>
                    <td className={`px-2 py-1.5 text-xs text-gray-700 ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.comment || ''}
                    </td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">
                      <button className="btn-ghost py-0.5 px-2 text-xs" onClick={() => setEditing(r)}>Edit</button>
                      <button className="btn-ghost py-0.5 px-2 text-xs text-red-700 hover:text-red-800" onClick={() => askDelete(r)}>Delete</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="New NAT rule"
        size="lg"
      >
        <NatForm
          interfaces={interfaces}
          defaultType={hook === 'prerouting' ? 'dnat' : 'masquerade'}
          onCancel={() => setCreating(false)}
          onSubmit={async (data) => {
            await api.nat.create(data)
            setCreating(false)
            reload()
          }}
        />
      </Modal>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={`Edit NAT rule #${editing?.id}`}
        size="lg"
      >
        {editing && (
          <NatForm
            rule={editing}
            interfaces={interfaces}
            onCancel={() => setEditing(null)}
            onSubmit={async (data) => {
              await api.nat.update(editing.id, data)
              setEditing(null)
              reload()
            }}
          />
        )}
      </Modal>

      <ConfirmHost />
    </div>
  )
}
