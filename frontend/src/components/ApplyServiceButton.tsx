// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useState } from 'react'
import { api, type ServicePending } from '../lib/api'

/**
 * Yellow Apply button used in the PageHeader of every managed service
 * page (DHCP, DNS server, SNMP, WireGuard, IPsec, HA, SSH, ...).
 *
 * Contract :
 * - Save in a form / modal writes DB + on-disk config (no systemd action),
 *   the backend then flags the service dirty.
 * - This button polls /api/<service>/pending every 3s. When dirty it
 *   shows an orange dot, mirroring ApplyFirewallButton's affordance.
 * - Clicking the button hits /api/<service>/apply which restarts /
 *   reloads the daemon and clears the dirty flag.
 */
type ServiceName = 'dhcp' | 'dns' | 'snmp' | 'wireguard' | 'ipsec'

type Endpoints = {
  pending: () => Promise<ServicePending>
  apply: () => Promise<unknown>
}

const ENDPOINTS: Record<ServiceName, Endpoints> = {
  dhcp:      { pending: () => api.dhcp.pending(),       apply: () => api.dhcp.apply() },
  dns:       { pending: () => api.dnsServer.pending(),  apply: () => api.dnsServer.apply() },
  snmp:      { pending: () => api.snmp.pending(),       apply: () => api.snmp.apply() },
  wireguard: { pending: () => api.wireguard.pending(),  apply: () => api.wireguard.apply() },
  ipsec:     { pending: () => api.ipsec.pending(),      apply: () => api.ipsec.apply() },
}

type Props = {
  service: ServiceName
  // Service-specific label shown in the tooltip when pending
  // (e.g. 'Reload Kea', 'Restart unbound').
  pendingTooltip?: string
  // Notified after a successful apply so the page can reload its
  // status / config snapshots.
  onApplied?: () => void
  // Notified on apply error so the page can surface it in its error
  // banner (we deliberately avoid pop-up toasts here).
  onError?: (msg: string) => void
  // Force-disable the button (e.g. when the daemon is not installed).
  disabled?: boolean
  // When true, the page-level form has unsaved local edits. The orange
  // dot is suppressed in that case so a single pending signal is shown
  // at a time : the Save button carries the dot first, then Apply
  // takes over once the form is persisted.
  formDirty?: boolean
}

export default function ApplyServiceButton({
  service, pendingTooltip, onApplied, onError, disabled, formDirty,
}: Props) {
  const [pending, setPending] = useState<ServicePending | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = async () => {
    try { setPending(await ENDPOINTS[service].pending()) } catch { /* silent */ }
  }

  useEffect(() => {
    void reload()
    const id = setInterval(reload, 3000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [service])

  const backendDirty = !!pending?.dirty
  // Hide the dot while the page has unsaved local edits, even if the
  // backend already has staged changes from a previous Save. This way
  // the operator sees a single pending signal at a time (Save first,
  // then Apply).
  const showDot = backendDirty && !formDirty
  const tooltip = formDirty
    ? 'Save the current form first, then click Apply to reload the service.'
    : backendDirty
      ? (pendingTooltip ?? 'Unsaved changes are staged - click Apply to reload the service.')
      : 'No pending changes (service is in sync with the saved configuration).'

  const onClick = async () => {
    setBusy(true)
    try {
      await ENDPOINTS[service].apply()
      await reload()
      onApplied?.()
    } catch (e) {
      onError?.((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <button
      type="button"
      className="btn-apply relative"
      onClick={onClick}
      disabled={busy || disabled || !backendDirty}
      title={tooltip}
    >
      {showDot && !busy && (
        <span
          className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 ring-2 ring-white"
          aria-hidden="true"
        />
      )}
      {busy ? 'Applying...' : 'Apply'}
    </button>
  )
}
