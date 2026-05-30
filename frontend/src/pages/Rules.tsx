import { useEffect, useMemo, useRef, useState } from 'react'
import TableSkeleton from '../components/TableSkeleton'
import { api, FirewallRule, Zone } from '../lib/api'
import Modal from '../components/Modal'
import PageHeader from '../components/PageHeader'
import ApplyFirewallButton from '../components/ApplyFirewallButton'
import EmptyState from '../components/EmptyState'
import RuleForm from '../components/RuleForm'
import HelpTooltip from '../components/HelpTooltip'
import RulesetModal from '../components/RulesetModal'
import Toggle from '../components/Toggle'
import { ErrorBlock } from '../components/Alerts'
import { ZoneBadge } from '../lib/zoneColor'
import { Shield, LockKeyhole as LockIcon, AlertTriangle } from 'lucide-react'
import { useConfirm } from '../components/ConfirmModal'

type Chain = 'forward' | 'input' | 'output'
const CHAINS: Chain[] = ['forward', 'input', 'output']

// Position >= 900 = catch-all rule (final drop all). Handled specially:
// no drag, no delete, "default" badge, distinct styling. This is the
// seed convention from compiler.py.
const isCatchAll = (r: FirewallRule) => r.position >= 900

// "any/any/accept" detector. A forward accept rule without any source
// zone, destination zone, address, address group, protocol, port or
// service group lets every flow pass and turns the firewall into a
// router. It is almost never what the operator wants, and historically
// gets added by accident while trying to "unblock" a specific service.
// We flag it with an amber warning so it stands out in the table.
function isOverlyPermissive(r: FirewallRule): boolean {
  if (!r.enabled || isCatchAll(r)) return false
  if (r.action !== 'accept') return false
  const hasService =
    !!r.src_port || !!r.dst_port || !!r.service_group_id ||
    (!!r.protocol && r.protocol !== 'any')
  // forward: any zone/address on either side, or a service, narrows it.
  if (r.chain === 'forward') {
    const hasMatcher =
      !!r.src_zone_id || !!r.dst_zone_id ||
      !!r.src_address || !!r.dst_address ||
      !!r.src_address_group_id || !!r.dst_address_group_id ||
      hasService
    return !hasMatcher
  }
  // input: the destination is the firewall, so only the source side and
  // the service narrow it. No source + no service = every port exposed.
  if (r.chain === 'input') {
    const hasMatcher =
      !!r.src_zone_id || !!r.src_address || !!r.src_address_group_id ||
      hasService
    return !hasMatcher
  }
  return false
}

// Badge shown in the zone column for the endpoint that is the firewall
// itself: destination on the input chain, source on the output chain.
// Keeps the table consistent with RuleForm's "This firewall" endpoint.
function FirewallBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 text-xs text-gray-600 bg-gray-100 border border-gray-200 rounded px-1.5 py-0.5"
      title="The firewall itself"
    >
      <Shield size={11} className="text-gray-500" />
      This firewall
    </span>
  )
}

function ActionCell({ action }: { action: FirewallRule['action'] }) {
  if (action === 'accept') {
    return (
      <div className="flex items-center gap-1.5">
        <span className="inline-block w-4 h-4 rounded-full bg-emerald-500 text-white text-[10px] leading-4 text-center font-bold" aria-hidden>✓</span>
        <span className="text-emerald-700 font-medium text-xs">accept</span>
      </div>
    )
  }
  if (action === 'drop') {
    return (
      <div className="flex items-center gap-1.5">
        <span className="inline-block w-4 h-4 rounded-full bg-red-500 text-white text-[10px] leading-4 text-center font-bold" aria-hidden>✗</span>
        <span className="text-red-700 font-medium text-xs">drop</span>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-1.5">
      <span className="inline-block w-4 h-4 rounded-full bg-amber-500 text-white text-[10px] leading-4 text-center font-bold" aria-hidden>⊘</span>
      <span className="text-amber-700 font-medium text-xs">reject</span>
    </div>
  )
}

function formatService(rule: FirewallRule): string {
  if (!rule.protocol) return 'any'
  if (rule.protocol === 'icmp') return 'icmp'
  const port = rule.dst_port || rule.src_port
  // Keep a uniform "<proto>/<port>" shape for all rows so the column
  // reads consistently. When no port is specified we render "/any" to
  // match "/443", "/80", ... visually.
  if (!port) return `${rule.protocol}/any`
  return `${rule.protocol}/${port}`
}

// Compact human-readable bytes (1024-based). Kept local so the Rules
// page does not need to depend on lib/format for one tiny helper.
function fmtBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1 }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

// Render a counter cell. Missing entries (rule never matched, nft
// unreachable, ruleset not yet applied) collapse to a discreet dash
// rather than 0/0 which would be ambiguous with "matched zero times".
function CounterCell({ c }: { c: { packets: number; bytes: number } | undefined }) {
  // Even disabled rules show "0 / 0 B" instead of a bare dash, so every
  // row in the table renders the same shape and the column stays tidy.
  const packets = c?.packets ?? 0
  const bytes = c?.bytes ?? 0
  const empty = !c || (!packets && !bytes)
  return (
    <span
      className={empty ? 'text-gray-400' : 'text-gray-700'}
      title={`${packets.toLocaleString('en')} packet(s), ${bytes.toLocaleString('en')} byte(s)`}
    >
      {fmtPackets(packets)}
      <span className="text-gray-300"> / </span>
      {fmtBytes(bytes)}
    </span>
  )
}

function fmtPackets(n: number): string {
  if (!n) return '0'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1_000).toFixed(n < 10_000 ? 1 : 0)}K`
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 1 : 0)}M`
  return `${(n / 1_000_000_000).toFixed(1)}G`
}

export default function Rules() {
  const [rules, setRules] = useState<FirewallRule[]>([])
  const [zones, setZones] = useState<Zone[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [chain, setChain] = useState<Chain>('forward')
  const [editing, setEditing] = useState<FirewallRule | null>(null)
  const [creating, setCreating] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()
  // Live nft counters per rule, polled every 3s. Stored as Record so
  // lookup by stringified id is O(1) in the render loop.
  const [stats, setStats] = useState<Record<string, { packets: number; bytes: number }>>({})

  const askDelete = async (r: FirewallRule) => {
    const ok = await confirm({
      title: 'Delete the rule',
      message: (
        <p>The rule <span className="font-mono text-gray-900">#{r.id}</span>
          {r.comment ? <> ({r.comment})</> : null} will be deleted.</p>
      ),
      destructive: true,
      requireText: 'delete',
    })
    if (!ok) return
    try { await api.rules.remove(r.id); reload() }
    catch (e) { setError((e as Error).message) }
  }
  const [showRuleset, setShowRuleset] = useState(false)
  const [filter, setFilter] = useState('')

  const zonesById = useMemo(() => {
    const m = new Map<number, Zone>()
    zones.forEach((z) => m.set(z.id, z))
    return m
  }, [zones])

  const reload = async () => {
    setLoading(true)
    try {
      const [r, z] = await Promise.all([api.rules.list(), api.zones.list()])
      setRules(r)
      setZones(z)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [])

  // Live counter polling. 3s feels responsive without hammering nft.
  // Failures are silent: keep the previous values rather than blank
  // the column (avoids visual flicker on transient errors).
  useEffect(() => {
    let alive = true
    const fetchStats = async () => {
      try {
        const s = await api.rules.stats()
        if (!alive) return
        setStats(s.rules)
      } catch {
        // ignore, retry next tick
      }
    }
    fetchStats()
    const id = setInterval(fetchStats, 3000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // Compteurs par chaine pour les onglets : on compte les non-catch-all
  // (sinon "default drop" gonfle artificiellement le badge).
  const countByChain = useMemo(() => {
    const c: Record<Chain, number> = { forward: 0, input: 0, output: 0 }
    for (const r of rules) {
      if (!isCatchAll(r)) c[r.chain]++
    }
    return c
  }, [rules])

  // List of the current chain (text filter applied). Catch-all is
  // always present and pinned at the bottom.
  const chainRules = useMemo(() => {
    let out = rules.filter((r) => r.chain === chain)
    if (filter.trim()) {
      const needle = filter.toLowerCase()
      out = out.filter((r) => {
        const sZone = zonesById.get(r.src_zone_id ?? -1)?.name || ''
        const dZone = zonesById.get(r.dst_zone_id ?? -1)?.name || ''
        const blob = [
          r.comment, sZone, dZone, r.src_address, r.dst_address,
          r.protocol, r.src_port, r.dst_port, r.action, r.chain,
        ].filter(Boolean).join(' ').toLowerCase()
        // Keep the catch-all in the pool so the default-policy row stays
        // consistent; it is hidden from the rendered list while a filter
        // is active (see catchAllRules below).
        return blob.includes(needle) || isCatchAll(r)
      })
    }
    // Sort by position so the catch-all stays at the bottom.
    return [...out].sort((a, b) => a.position - b.position || a.id - b.id)
  }, [rules, chain, filter, zonesById])

  const editableRules = chainRules.filter((r) => !isCatchAll(r))
  // The chain's hook policy in nftables is what really happens when
  // no explicit rule matches. We surface it as a non-editable row at
  // the bottom of every tab so the operator never wonders "where is
  // the implicit drop?". If a real catch-all rule already exists in
  // the DB (forward seed), we use that one; otherwise we synthesize
  // a placeholder that mirrors the compiler's chain policy.
  const dbCatchAll = chainRules.filter(isCatchAll)
  const defaultPolicyByChain: Record<string, 'drop' | 'accept'> = {
    input: 'drop',
    forward: 'drop',
    output: 'accept',
  }
  const syntheticCatchAll: FirewallRule | null = dbCatchAll.length > 0
    ? null
    : ({
        id: -1 * (chain === 'input' ? 1 : chain === 'forward' ? 2 : 3),
        position: 999,
        chain,
        action: defaultPolicyByChain[chain] || 'drop',
        src_zone_id: null,
        dst_zone_id: null,
        src_address: null,
        dst_address: null,
        protocol: null,
        dst_port: null,
        src_port: null,
        service_id: null,
        service_group_id: null,
        log: false,
        rate_limit: null,
        enabled: true,
        comment: defaultPolicyByChain[chain] === 'accept'
          ? 'Default policy (accept).'
          : 'Default policy (drop).',
      } as unknown as FirewallRule)
  // The default-policy row is always-on context, not a search hit. While
  // a text filter is active, hide it so the list narrows down to the
  // matching rules only (and the empty state can show "No rule matches").
  const catchAllRules = filter.trim()
    ? []
    : (syntheticCatchAll ? [syntheticCatchAll] : dbCatchAll)

  const toggleEnabled = async (r: FirewallRule) => {
    await api.rules.update(r.id, { enabled: !r.enabled })
    reload()
  }

  // Drag-and-drop state. To avoid visual jumps we track source and
  // target indices separately. On drop we apply the new permutation
  // locally, then call the backend reorder endpoint which renumbers
  // in multiples of 10. If the call fails, we reload (visual revert).
  const [draggingId, setDraggingId] = useState<number | null>(null)
  const [dragOverId, setDragOverId] = useState<number | null>(null)
  const dragSourceIndex = useRef<number | null>(null)

  const reorderLocal = (sourceId: number, targetId: number, after: boolean): FirewallRule[] => {
    const ids = editableRules.map((r) => r.id)
    const src = ids.indexOf(sourceId)
    if (src < 0) return editableRules
    ids.splice(src, 1)
    let dst = ids.indexOf(targetId)
    if (dst < 0) dst = ids.length
    if (after) dst += 1
    ids.splice(dst, 0, sourceId)
    return ids.map((id) => editableRules.find((r) => r.id === id)!).filter(Boolean)
  }

  const onDragStart = (e: React.DragEvent, r: FirewallRule, idx: number) => {
    if (isCatchAll(r)) { e.preventDefault(); return }
    setDraggingId(r.id)
    dragSourceIndex.current = idx
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', String(r.id))
  }

  const onDragOver = (e: React.DragEvent, r: FirewallRule) => {
    if (isCatchAll(r) || draggingId === null) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (dragOverId !== r.id) setDragOverId(r.id)
  }

  const onDragLeave = () => { setDragOverId(null) }

  const onDrop = async (e: React.DragEvent, target: FirewallRule) => {
    e.preventDefault()
    const sourceIdStr = e.dataTransfer.getData('text/plain')
    const sourceId = parseInt(sourceIdStr, 10)
    setDraggingId(null)
    setDragOverId(null)
    if (!sourceId || sourceId === target.id || isCatchAll(target)) return
    const newOrder = reorderLocal(sourceId, target.id, false)
    // Optimistic update: replace the rules of the current chain locally
    const otherChains = rules.filter((r) => r.chain !== chain)
    // Preserve the real DB catch-all rows (never the synthetic placeholder
    // nor an empty list when a filter is active) so the optimistic state
    // stays faithful until the reorder reload lands.
    setRules([...otherChains, ...newOrder, ...dbCatchAll])
    try {
      await api.rules.reorder(chain, newOrder.map((r) => r.id))
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
        icon={<Shield size={16} />}
       
        title="Filter rules"
        description="Filter rules. Evaluated top to bottom, first match wins."
        actions={<ApplyFirewallButton onClick={() => setShowRuleset(true)} onView={() => setShowRuleset(true)} />}
      />

      {showRuleset && <RulesetModal onClose={() => setShowRuleset(false)} />}

      <div className="px-6 py-4">
        {error && <ErrorBlock message={error} />}

        {/* Onglets de chaine style FortiGate. Forward par defaut (cas LAN -> WAN, le plus frequent). */}
        <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
          <div className="inline-flex rounded border border-gray-300 overflow-hidden">
            {CHAINS.map((c, i) => (
              <button
                key={c}
                className={
                  `px-3 py-1.5 text-sm inline-flex items-center gap-2 ${i > 0 ? 'border-l border-gray-300' : ''} ` +
                  (chain === c ? 'bg-steel-100 text-gray-900 font-medium' : 'bg-white text-gray-800 hover:bg-gray-50')
                }
                onClick={() => setChain(c)}
              >
                <span className="capitalize">{c}</span>
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
                  {countByChain[c]}
                </span>
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2 flex-1 max-w-xl">
            <input
              className="input flex-1 py-1.5 text-sm"
              placeholder="Filter (comment, zone, IP, port...)"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            {filter && <button className="btn-ghost py-1 text-xs" onClick={() => setFilter('')}>x</button>}
          </div>

          <button className="btn-primary" onClick={() => setCreating(true)}>Add rule</button>
        </div>

        {editableRules.length > 1 && (
          <div className="text-xs text-gray-600 mb-2 flex items-center gap-2">
            <span>Drag a row by its handle <span className="font-mono text-gray-500">⋮⋮</span> to reorder.</span>
            <HelpTooltip text="Rules are evaluated top to bottom inside each chain. On drop, MurOS renumbers all positions as 10, 20, 30... in the database. The catch-all 'default' row at the bottom cannot be moved or deleted (it is generated from the chain default policy)." />
          </div>
        )}

        <div className="border border-gray-200 rounded-md overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-800 text-xs uppercase tracking-wider">
              <tr>
                <th className="w-8"></th>
                <th className="text-center px-2 py-2 w-10">#</th>
                <th className="text-center px-2 py-2 w-14" title="Enable / disable this rule"></th>
                <th className="text-left px-2 py-2 w-24">
                  Action
                  <HelpTooltip text="accept = let through. drop = silently discard (client waits a timeout, recommended on WAN). reject = sends an ICMP unreachable (faster client-side but exposes the firewall presence)." />
                </th>
                <th className="text-left px-2 py-2 w-32">Src zone</th>
                <th className="text-left px-2 py-2">Source</th>
                <th className="text-left px-2 py-2 w-32">Dst zone</th>
                <th className="text-left px-2 py-2">Destination</th>
                <th className="text-left px-2 py-2 w-28">Service</th>
                <th className="text-center px-2 py-2 w-10" title="Log this match to the kernel journal">Log</th>
                <th className="text-left px-2 py-2 w-24">Limit</th>
                <th
                  className="text-right px-2 py-2 w-28"
                  title="Live nft counters: packets and bytes matched since the last Apply. Reset on every ruleset reload."
                >
                  Counter
                </th>
                <th className="text-left px-2 py-2">Comment</th>
                <th className="text-right px-2 py-2 w-24"></th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <TableSkeleton rows={5} cols={13} />
              )}
              {!loading && editableRules.length === 0 && catchAllRules.length === 0 && (
                <tr><td colSpan={13}>
                  <EmptyState
                    icon={<Shield size={20} />}
                    text={filter ? 'No rule matches the filter' : `No rule in the ${chain} chain`}
                    hint={filter ? undefined : `Default policy applies: traffic in the ${chain} chain is dropped unless an explicit accept rule is added.`}
                    action={!filter && (
                      <button className="btn-primary" onClick={() => setCreating(true)}>Add a rule</button>
                    )}
                  />
                </td></tr>
              )}
              {editableRules.map((r, idx) => {
                const srcZone = r.src_zone_id ? zonesById.get(r.src_zone_id) : undefined
                const dstZone = r.dst_zone_id ? zonesById.get(r.dst_zone_id) : undefined
                const dragging = draggingId === r.id
                const dropTarget = dragOverId === r.id && draggingId !== r.id
                return (
                  <tr
                    key={r.id}
                    id={`rule-${r.id}`}
                    draggable
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
                    <td className="px-2 py-1.5 text-center cursor-grab active:cursor-grabbing text-gray-400 select-none" title="Drag to reorder">⋮⋮</td>
                    <td className={`px-2 py-1.5 font-mono text-xs text-gray-600 text-center ${!r.enabled ? 'opacity-60' : ''}`}>{idx + 1}</td>
                    <td className={`px-2 py-1.5 text-center ${!r.enabled ? 'opacity-60' : ''}`}>
                      <Toggle size="sm" checked={r.enabled} onChange={() => toggleEnabled(r)} />
                    </td>
                    <td className={`px-2 py-1.5 ${!r.enabled ? 'opacity-60' : ''}`}>
                      <div className="flex items-center gap-1.5">
                        <ActionCell action={r.action} />
                        {isOverlyPermissive(r) && (
                          <span
                            className="inline-flex items-center text-amber-600"
                            title={r.chain === 'input'
                              ? 'This rule accepts every connection to the firewall itself (no source, protocol or port set). It exposes every service the firewall runs to all sources. Restrict it to a source zone, subnet or service.'
                              : 'This rule accepts every forwarded flow (no zone, address, protocol or port set). It effectively disables filtering on the forward chain. Restrict it to a zone, subnet or service.'}
                          >
                            <AlertTriangle size={14} />
                          </span>
                        )}
                      </div>
                    </td>
                    <td className={`px-2 py-1.5 ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.chain === 'output'
                        ? <FirewallBadge />
                        : srcZone ? <ZoneBadge name={srcZone.name} /> : <span className="text-xs text-gray-500 italic">any</span>}
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs ${!r.src_address ? 'text-gray-500 italic' : 'text-gray-800'} ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.src_address || 'any'}
                    </td>
                    <td className={`px-2 py-1.5 ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.chain === 'input'
                        ? <FirewallBadge />
                        : dstZone ? <ZoneBadge name={dstZone.name} /> : <span className="text-xs text-gray-500 italic">any</span>}
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs ${!r.dst_address ? 'text-gray-500 italic' : 'text-gray-800'} ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.dst_address || 'any'}
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-xs ${formatService(r) === 'any' ? 'text-gray-500 italic' : 'text-gray-700'} ${!r.enabled ? 'opacity-60' : ''}`}>
                      {formatService(r)}
                    </td>
                    <td className={`px-2 py-1.5 text-center ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.log
                        ? <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500" title="Log to the kernel journal" />
                        : <span className="text-[11px] text-gray-400">off</span>}
                    </td>
                    <td className={`px-2 py-1.5 font-mono text-[11px] ${!r.enabled ? 'opacity-60' : ''}`}>
                      {r.rate_limit
                        ? <span className="text-steel-700">{r.rate_limit}</span>
                        : <span className="text-gray-400 font-sans">off</span>}
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono text-[11px] tabular-nums ${!r.enabled ? 'opacity-60' : ''}`}>
                      <CounterCell c={stats[String(r.id)]} />
                    </td>
                    <td className={`px-2 py-1.5 text-xs text-gray-700 ${!r.enabled ? 'opacity-60' : ''}`}>{r.comment || ''}</td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">
                      <button className="btn-ghost py-0.5 px-2 text-xs" onClick={() => setEditing(r)}>Edit</button>
                      <button className="btn-ghost py-0.5 px-2 text-xs text-red-700 hover:text-red-800" onClick={() => askDelete(r)}>Delete</button>
                    </td>
                  </tr>
                )
              })}
              {catchAllRules.map((r) => {
                return (
                  <tr
                    key={r.id}
                    id={`rule-${r.id}`}
                    className="border-t-2 border-gray-300 bg-red-50/60 italic"
                  >
                    <td className="px-2 py-1.5 text-center text-gray-300 select-none" title="Catch-all cannot be reordered">-</td>
                    <td className="px-2 py-1.5 text-center">
                      <span className="inline-block px-1.5 py-0.5 text-[10px] font-semibold rounded bg-gray-200 text-gray-700 not-italic uppercase tracking-wider">
                        default
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <span
                        className="inline-flex items-center justify-center text-gray-400"
                        title="The chain default policy is immutable from the UI. To log or reshape what falls through, add an explicit rule just above with the right match and action."
                      >
                        <LockIcon size={14} />
                      </span>
                    </td>
                    <td className="px-2 py-1.5"><ActionCell action={r.action} /></td>
                    <td className="px-2 py-1.5 text-xs text-gray-500">any</td>
                    <td className="px-2 py-1.5 font-mono text-xs text-gray-500">any</td>
                    <td className="px-2 py-1.5 text-xs text-gray-500">any</td>
                    <td className="px-2 py-1.5 font-mono text-xs text-gray-500">any</td>
                    <td className="px-2 py-1.5 font-mono text-xs text-gray-500">any</td>
                    <td className="px-2 py-1.5 text-center">
                      {r.log
                        ? <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500" title="Log to the kernel journal" />
                        : <span className="text-[11px] text-gray-400 not-italic">off</span>}
                    </td>
                    <td className="px-2 py-1.5 font-mono text-[11px]">
                      {r.rate_limit
                        ? <span className="text-steel-700">{r.rate_limit}</span>
                        : <span className="text-gray-400 font-sans not-italic">off</span>}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-[11px] tabular-nums not-italic">
                      <CounterCell c={stats[String(r.id)]} />
                    </td>
                    <td className="px-2 py-1.5 text-xs">
                      <span className="text-gray-500 whitespace-nowrap">{r.comment || 'Default policy (drop).'}</span>
                    </td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap">
                      {/* No Edit button on catch-all: consistent with the lock icon. */}
                      <span className="text-xs text-gray-300 italic select-none">locked</span>
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
        title="New rule"
        size="lg"
      >
        <RuleForm
          zones={zones}
          defaultChain={chain}
          onCancel={() => setCreating(false)}
          onSubmit={async (data) => {
            // Auto position = last non-catch-all position + 10. If the
            // chain is empty, start at 10. The backend will renumber if
            // the admin drags and drops afterwards.
            const sameChain = rules.filter((r) => r.chain === chain && !isCatchAll(r))
            const maxPos = sameChain.reduce((m, r) => Math.max(m, r.position), 0)
            await api.rules.create({ ...data, chain, position: maxPos + 10 })
            setCreating(false)
            reload()
          }}
        />
      </Modal>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={`Edit rule #${editing?.id}`}
        size="lg"
      >
        {editing && (
          <RuleForm
            rule={editing}
            zones={zones}
            onCancel={() => setEditing(null)}
            onSubmit={async (data) => {
              await api.rules.update(editing.id, data)
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
