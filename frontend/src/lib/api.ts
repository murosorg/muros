// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
// Client HTTP minimal

export type Zone = {
  id: number
  name: string
  description: string | null
  created_at: string
}

export type Interface = {
  id: number
  name: string
  description: string | null
  zone_id: number | null
  type: 'physical' | 'vlan'
  parent_interface: string | null
  vlan_id: number | null
  ip_mode: 'none' | 'static' | 'dhcp'
  ip_address: string | null
  gateway: string | null
  dns_servers: string | null
  mtu: number | null
  enabled: boolean
  dirty?: boolean
  pending_delete?: boolean
}

export type FirewallRule = {
  id: number
  position: number
  chain: 'input' | 'forward' | 'output'
  action: 'accept' | 'drop' | 'reject'
  src_zone_id: number | null
  dst_zone_id: number | null
  src_address: string | null
  dst_address: string | null
  protocol: 'tcp' | 'udp' | 'icmp' | 'any' | null
  src_port: string | null
  dst_port: string | null
  log: boolean
  enabled: boolean
  comment: string | null
  rate_limit: string | null
  service_group_id: number | null
  src_address_group_id: number | null
  dst_address_group_id: number | null
  created_at: string
}

export type ServiceGroupPort = {
  id?: number
  protocol: 'tcp' | 'udp'
  port: string
}

export type ServiceGroup = {
  id: number
  name: string
  description: string | null
  ports: ServiceGroupPort[]
  created_at: string
}

export type AddressGroupEntry = {
  id?: number
  value: string
}

export type AddressGroup = {
  id: number
  name: string
  description: string | null
  entries: AddressGroupEntry[]
  created_at: string
}

export type NatRule = {
  id: number
  position: number
  type: 'masquerade' | 'snat' | 'dnat'
  interface_id: number | null
  src_address: string | null
  dst_address: string | null
  protocol: 'tcp' | 'udp' | 'icmp' | 'any' | null
  dst_port: string | null
  redirect_to_ip: string | null
  redirect_to_port: string | null
  enabled: boolean
  comment: string | null
  created_at: string
}

export type StaticRoute = {
  id: number
  destination: string
  gateway: string | null
  interface_id: number | null
  metric: number
  enabled: boolean
  comment: string | null
  created_at: string
}

export type WanStatus = 'up' | 'down' | 'unknown'

export type WanGateway = {
  id: number
  name: string
  interface_id: number
  gateway: string
  priority: number
  monitoring_target: string
  interval_s: number
  failures_threshold: number
  enabled: boolean
  comment: string | null
  status: WanStatus
  consecutive_failures: number
  consecutive_successes: number
  last_probe_at: string | null
  last_change_at: string | null
}

export type WanActive = {
  active_id: number | null
  active_name: string | null
  reason: 'healthy' | 'all_down' | 'disabled' | 'no_gateway'
}

export type MetricsSummary = {
  timestamp: string
  cpu_usage_percent: number
  cpu_cores: number
  memory: { total_bytes: number; available_bytes: number; used_bytes: number; used_percent: number }
  swap: { total_bytes: number; used_bytes: number; used_percent: number }
  load: number[]
  uptime_seconds: number
  disks: Array<{ mount: string; total_bytes: number; used_bytes: number; used_percent: number }>
  interfaces: Array<{
    name: string
    operstate?: string
    rx_bytes: number; tx_bytes: number
    rx_packets: number; tx_packets: number
    rx_errors: number; tx_errors: number
    rx_dropped: number; tx_dropped: number
  }>
  conntrack: { current: number; max: number; used_percent: number }
}

export type MetricSamplePoint = {
  timestamp: string
  cpu_usage_percent: number
  memory_used_percent: number
  memory_used_bytes: number
  conntrack_current: number
  conntrack_used_percent: number
  load_1: number
  load_5: number
  load_15: number
}

export type InterfaceSamplePoint = {
  timestamp: string
  interface_name: string
  rx_bytes: number
  tx_bytes: number
  rx_packets: number
  tx_packets: number
}

export type MetricsHistory = {
  samples: MetricSamplePoint[]
  interfaces: Record<string, InterfaceSamplePoint[]>
  retention_hours: number
}

export type SystemLogEntry = {
  timestamp: string
  unit: string
  priority: number
  message: string
}

export type FirewallLogEntry = {
  timestamp: string
  message: string
  hostname: string | null
  syslog_identifier: string | null
  // Champs derives du prefixe nft "[muros <ACTION> r=<ID> <CHAIN>]"
  action: string | null
  rule_id: number | null
  chain: string | null
}

export type LogsStatus = {
  rules_with_log: number
  rules_with_log_enabled: number
  journalctl_available: boolean
  is_root: boolean
}


export type Health = {
  status: string
  version: string
  apply_enabled?: boolean
  uptime_seconds?: number
}
export type SystemInfo = {
  hostname: string
  kernel: string
  arch: string
  apply_enabled?: boolean
  is_root?: boolean
}

export type SystemInterface = {
  name: string
  state: string
  mtu: number
  mac: string | null
  addresses: string[]
  is_virtual: boolean
  gateway: string | null
}

export type User = {
  id: number
  username: string
  is_admin: boolean
  must_change_password: boolean
  last_login: string | null
}

export type AdminUser = {
  id: number
  username: string
  is_admin: boolean
  ui_access: boolean
  must_change_password: boolean
  last_login: string | null
  exists_on_system: boolean
}

export type UsersList = {
  users: AdminUser[]
  grantable_accounts: string[]
}

export type FirewallPending = {
  rules: number
  nat: number
  zones: number
  total: number
}

export type FirewallCounter = {
  packets: number
  bytes: number
}

export type FirewallStats = {
  // Keys are stringified DB rule ids (JSON object keys are strings).
  rules: Record<string, FirewallCounter>
  nat: Record<string, FirewallCounter>
}

export type ApplyStatus = {
  state: 'idle' | 'pending' | 'committed' | 'rolled_back' | 'failed'
  started_at: string | null
  expires_at: string | null
  timeout_seconds: number
  dry_run: boolean
  message: string | null
}

const TOKEN_KEY = 'muros-token'

export const auth = {
  get token() { return localStorage.getItem(TOKEN_KEY) },
  set token(t: string | null) {
    if (t) localStorage.setItem(TOKEN_KEY, t)
    else localStorage.removeItem(TOKEN_KEY)
  },
  isLoggedIn() { return !!localStorage.getItem(TOKEN_KEY) },
  logout() { localStorage.removeItem(TOKEN_KEY) },
}

let onUnauthorized: ((expired: boolean) => void) | null = null
export function setUnauthorizedHandler(h: (expired: boolean) => void) { onUnauthorized = h }

// `hadValidSession` passe a true des qu'un appel authentifie a reussi
// pendant CE chargement de page. Un 401 ulterieur signifie alors une vraie
// expiration de session (a afficher), tandis qu'un 401 sur le premier
// appel (token perime laisse dans localStorage par un dev) ne doit PAS
// afficher la banniere "Session expired" qui serait trompeuse.
let hadValidSession = false

// Backend reachability bus. The request wrapper fires `muros:backend-down`
// when nginx returns a 5xx upstream error (typical during a MurOS update
// while the backend is restarting) or when fetch() rejects with a
// network error. It fires `muros:backend-up` on the next successful
// round-trip. The `BackendUnreachableOverlay` component listens to both
// and renders a quiet "Reconnecting..." card instead of letting the UI
// flash a red "502 Bad Gateway" toast. The flag is module-private so
// repeated failures only emit one event until recovery.
let backendDownFlag = false
function notifyBackendDown() {
  if (backendDownFlag) return
  backendDownFlag = true
  window.dispatchEvent(new Event('muros:backend-down'))
}
function notifyBackendUp() {
  if (!backendDownFlag) return
  backendDownFlag = false
  window.dispatchEvent(new Event('muros:backend-up'))
}

async function request<T>(method: string, path: string, body?: unknown, opts?: { timeoutMs?: number }): Promise<T> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  const token = auth.token
  if (token) headers['Authorization'] = `Bearer ${token}`

  // AbortController : si le backend bloque (DNS HS, apt qui rame...) on
  // libere le navigateur apres timeoutMs ms plutot que laisser un spinner
  // colle. Defaut : pas de timeout cote client.
  const ctrl = opts?.timeoutMs ? new AbortController() : null
  const timer = ctrl && opts?.timeoutMs
    ? window.setTimeout(() => ctrl.abort(), opts.timeoutMs)
    : null

  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl?.signal,
    })
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error(`No response in ${Math.round((opts?.timeoutMs || 0) / 1000)}s. Try again or check the server connection.`)
    }
    // Network-level failure (DNS, TCP RST, TLS error...). The backend
    // is most likely down or restarting: surface a "reconnecting"
    // overlay rather than a red toast.
    notifyBackendDown()
    throw err
  } finally {
    if (timer !== null) window.clearTimeout(timer)
  }

  // Upstream-level failure served by nginx while the backend is being
  // restarted (typical during a MurOS update). Trigger the overlay so
  // the caller's catch block does not flash a red "502" toast on top
  // of it. The thrown error is intentionally generic: callers using
  // `.catch(() => {})` for polling stay silent, callers showing the
  // error inline get a neutral wording instead of "502 Bad Gateway".
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    notifyBackendDown()
    throw new Error('Backend temporarily unavailable')
  }

  // First successful round-trip after a transient outage: clear the
  // overlay so the UI returns to normal without a page reload.
  notifyBackendUp()

  if (res.status === 401) {
    const wasAuthed = hadValidSession
    auth.logout()
    hadValidSession = false
    onUnauthorized?.(wasAuthed)
    throw new Error('Authentification requise')
  }
  if (!res.ok) {
    let detail = ''
    try { detail = (await res.json()).detail } catch { /* */ }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  // L'appel a reussi avec un token : on a une vraie session valide cote
  // serveur. Marquer pour distinguer "session expiree" d'un "token perime
  // jamais valide ce chargement".
  if (token) hadValidSession = true
  if (res.status === 204) return undefined as T
  return res.json()
}

// Pending-apply state of a managed service (DHCP, DNS server, SNMP,
// WireGuard, IPsec, HA, SSH, http, notifications). Polled by
// ApplyServiceButton every 3s; `dirty=true` lights up the orange dot
// on the yellow Apply button in the page header.
export type ServicePending = {
  name: string
  dirty: boolean
  last_applied_at: string | null
  last_marked_dirty_at: string | null
}

// Aggregated pending state across every managed service. Polled by
// the sidebar (single round-trip) to decorate nav entries linked to a
// dirty service with an orange dot.
export type ServicesPendingAggregate = {
  states: Record<string, ServicePending>
  dirty_count: number
  dirty_services: string[]
}

// Audit row in service_apply_log : one entry per Save and one per
// Apply, with the actor (if known) and a short summary.
export type ServiceApplyLogEntry = {
  id: number
  name: string
  action: 'save' | 'apply'
  actor_user_id: number | null
  actor_username: string | null
  summary: string | null
  at: string | null
}

export const api = {
  health: () => request<Health>('GET', '/api/health'),
  systemInfo: () => request<SystemInfo>('GET', '/api/system/info'),

  zones: {
    list: () => request<Zone[]>('GET', '/api/zones'),
    create: (data: Partial<Zone>) => request<Zone>('POST', '/api/zones', data),
    update: (id: number, data: Partial<Zone>) => request<Zone>('PUT', `/api/zones/${id}`, data),
    remove: (id: number) => request<void>('DELETE', `/api/zones/${id}`),
  },

  interfaces: {
    list: () => request<Interface[]>('GET', '/api/interfaces'),
    listSystem: () => request<SystemInterface[]>('GET', '/api/interfaces/system'),
    create: (data: Partial<Interface>) => request<Interface>('POST', '/api/interfaces', data),
    update: (id: number, data: Partial<Interface>) => request<Interface>('PUT', `/api/interfaces/${id}`, data),
    remove: (id: number) => request<void>('DELETE', `/api/interfaces/${id}`),
    cancelDelete: (id: number) => request<Interface>('POST', `/api/interfaces/${id}/cancel-delete`),
    importCurrent: (id: number) => request<Interface>('POST', `/api/interfaces/${id}/import-current`),
  },

  rules: {
    list: () => request<FirewallRule[]>('GET', '/api/firewall/rules'),
    create: (data: Partial<FirewallRule>) => request<FirewallRule>('POST', '/api/firewall/rules', data),
    update: (id: number, data: Partial<FirewallRule>) => request<FirewallRule>('PUT', `/api/firewall/rules/${id}`, data),
    remove: (id: number) => request<void>('DELETE', `/api/firewall/rules/${id}`),
    move: (id: number, direction: 'up' | 'down') => request<FirewallRule>('POST', `/api/firewall/rules/${id}/move?direction=${direction}`),
    reorder: (chain: 'input' | 'forward' | 'output', ruleIds: number[]) =>
      request<FirewallRule[]>('POST', '/api/firewall/rules/reorder', { chain, rule_ids: ruleIds }),
    preview: () => request<{ ruleset: string }>('GET', '/api/firewall/preview'),
    check: () => request<{ ok: boolean; message: string; ruleset: string }>('POST', '/api/firewall/check'),
    stats: () => request<FirewallStats>('GET', '/api/firewall/stats'),
  },

  serviceGroups: {
    list: () => request<ServiceGroup[]>('GET', '/api/firewall/service-groups'),
    create: (data: { name: string; description?: string | null; ports: ServiceGroupPort[] }) =>
      request<ServiceGroup>('POST', '/api/firewall/service-groups', data),
    update: (id: number, data: { name?: string; description?: string | null; ports?: ServiceGroupPort[] }) =>
      request<ServiceGroup>('PUT', `/api/firewall/service-groups/${id}`, data),
    remove: (id: number) => request<void>('DELETE', `/api/firewall/service-groups/${id}`),
  },

  addressGroups: {
    list: () => request<AddressGroup[]>('GET', '/api/firewall/address-groups'),
    create: (data: { name: string; description?: string | null; entries: AddressGroupEntry[] }) =>
      request<AddressGroup>('POST', '/api/firewall/address-groups', data),
    update: (id: number, data: { name?: string; description?: string | null; entries?: AddressGroupEntry[] }) =>
      request<AddressGroup>('PUT', `/api/firewall/address-groups/${id}`, data),
    remove: (id: number) => request<void>('DELETE', `/api/firewall/address-groups/${id}`),
  },

  auth: {
    login: (username: string, password: string) =>
      request<{ access_token: string; token_type: string; must_change_password: boolean }>(
        'POST', '/api/auth/login', { username, password },
      ),
    me: () => request<User>('GET', '/api/auth/me'),
    changePassword: (current_password: string, new_password: string) =>
      request<User>('POST', '/api/auth/change-password', { current_password, new_password }),
  },

  users: {
    list: () => request<UsersList>('GET', '/api/users'),
    grant: (username: string, is_admin: boolean) =>
      request<AdminUser>('POST', '/api/users/grant', { username, is_admin }),
    update: (id: number, data: { ui_access?: boolean; is_admin?: boolean }) =>
      request<AdminUser>('PUT', `/api/users/${id}`, data),
    remove: (id: number) => request<void>('DELETE', `/api/users/${id}`),
  },

  metrics: {
    summary: () => request<MetricsSummary>('GET', '/api/metrics/summary'),
    history: (hours = 24) => request<MetricsHistory>('GET', `/api/metrics/history?hours=${hours}`),
  },

  logs: {
    firewall: (limit = 200, search?: string, scope: 'muros' | 'kernel' = 'muros') => {
      const params = new URLSearchParams({ limit: String(limit), scope })
      if (search) params.append('search', search)
      return request<FirewallLogEntry[]>('GET', `/api/logs/firewall?${params}`)
    },
    status: () => request<LogsStatus>('GET', '/api/logs/status'),
    audit: (opts?: { limit?: number; method?: string; username?: string; contains?: string }) => {
      const qs = new URLSearchParams()
      if (opts?.limit) qs.set('limit', String(opts.limit))
      if (opts?.method) qs.set('method', opts.method)
      if (opts?.username) qs.set('username', opts.username)
      if (opts?.contains) qs.set('contains', opts.contains)
      const s = qs.toString()
      return request<AuditLogEntry[]>('GET', `/api/logs/audit${s ? '?' + s : ''}`)
    },
    system: (opts?: { unit?: string; limit?: number; since_minutes?: number; search?: string; priority?: string }) => {
      const qs = new URLSearchParams()
      if (opts?.unit) qs.set('unit', opts.unit)
      if (opts?.limit) qs.set('limit', String(opts.limit))
      if (opts?.since_minutes) qs.set('since_minutes', String(opts.since_minutes))
      if (opts?.search) qs.set('search', opts.search)
      if (opts?.priority) qs.set('priority', opts.priority)
      const s = qs.toString()
      return request<SystemLogEntry[]>('GET', `/api/logs/system${s ? '?' + s : ''}`)
    },
    systemUnits: () => request<string[]>('GET', '/api/logs/system/units'),
  },

  apply: {
    status: () => request<ApplyStatus>('GET', '/api/firewall/apply/status'),
    // Defaut 60s aligne avec safe_apply / pending_apply / apply.py backend.
    // Convention MurOS unique pour tous les rollbacks (cf RollbackModal).
    run: (timeout = 60) => request<ApplyStatus>('POST', '/api/firewall/apply', { timeout_seconds: timeout }),
    confirm: () => request<ApplyStatus>('POST', '/api/firewall/apply/confirm'),
    rollback: () => request<ApplyStatus>('POST', '/api/firewall/apply/rollback'),
    pending: () => request<FirewallPending>('GET', '/api/firewall/pending'),
  },

  nat: {
    list: () => request<NatRule[]>('GET', '/api/nat/rules'),
    create: (data: Partial<NatRule>) => request<NatRule>('POST', '/api/nat/rules', data),
    update: (id: number, data: Partial<NatRule>) => request<NatRule>('PUT', `/api/nat/rules/${id}`, data),
    remove: (id: number) => request<void>('DELETE', `/api/nat/rules/${id}`),
    reorder: (ruleIds: number[]) =>
      request<NatRule[]>('POST', '/api/nat/rules/reorder', { rule_ids: ruleIds }),
  },

  routes: {
    list: () => request<StaticRoute[]>('GET', '/api/routes'),
    create: (data: Partial<StaticRoute>) => request<StaticRoute>('POST', '/api/routes', data),
    update: (id: number, data: Partial<StaticRoute>) => request<StaticRoute>('PUT', `/api/routes/${id}`, data),
    remove: (id: number) => request<void>('DELETE', `/api/routes/${id}`),
    reapply: () => request<void>('POST', '/api/routes/reapply'),
  },

  wan: {
    list: () => request<WanGateway[]>('GET', '/api/wan/gateways'),
    create: (data: Partial<WanGateway>) =>
      request<WanGateway>('POST', '/api/wan/gateways', data),
    update: (id: number, data: Partial<WanGateway>) =>
      request<WanGateway>('PUT', `/api/wan/gateways/${id}`, data),
    remove: (id: number) =>
      request<void>('DELETE', `/api/wan/gateways/${id}`),
    active: () => request<WanActive>('GET', '/api/wan/active'),
    probe: (id: number) =>
      request<WanGateway>('POST', `/api/wan/gateways/${id}/probe`),
    status: () =>
      request<{ service_state: string; version: string | null }>(
        'GET', '/api/wan/status',
      ),
  },

  network: {
    pending: () => request<{
      count: number
      interfaces: Array<{ id: number; name: string; type: string; ip_mode: string; ip_address: string | null }>
      routes: Array<{ id: number; destination: string; gateway: string | null; metric: number }>
    }>('GET', '/api/network/pending'),
    apply: () => request<{
      applied: boolean; message: string; errors?: string[]; pending_id: string | null
    }>('POST', '/api/network/apply'),
    environment: () => request<{
      apply_enabled: boolean
      competing_managers: string[]
    }>('GET', '/api/network/environment'),
    adopt: () => request<{
      interfaces_touched: number
      routes_touched: number
      skipped: boolean
    }>('POST', '/api/network/adopt'),
  },

  backups: {
    list: () => request<Backup[]>('GET', '/api/backups'),
    create: (label?: string) => request<Backup>('POST', '/api/backups', { label }),
    remove: (name: string) => request<void>('DELETE', `/api/backups/${encodeURIComponent(name)}`),
    restore: (name: string) => request<BackupRestoreResult>('POST', `/api/backups/${encodeURIComponent(name)}/restore`),
  },

  ntp: {
    status: () => request<NtpStatus>('GET', '/api/ntp/status'),
    servers: () => request<NtpServers>('GET', '/api/ntp/servers'),
    setServers: (servers: string[], serveLan: boolean) =>
      request<NtpServers>('PUT', '/api/ntp/servers', { servers, serve_lan: serveLan }),
  },

  dns: {
    get: () => request<DnsConfig>('GET', '/api/dns'),
    set: (data: { resolvers: string[]; search_domains: string[] }) =>
      request<DnsConfig>('PUT', '/api/dns', data),
  },

  updates: {
    status: () => request<UpdateStatus>('GET', '/api/updates'),
    check: () => request<UpdateStatus>('POST', '/api/updates/check'),
    install: () => request<UpdateInstallResult>('POST', '/api/updates/install'),
    // 30s : marge sur les 25s apt-get update cote backend. Si le serveur
    // est completement bloque (DNS HS, mirrors down), on libere le bouton
    // au lieu de laisser l'utilisateur sur "Verification..." indefini.
    checkAll: () => request<{ apt: UpdateStatus; muros: MurosUpdateStatus; last_check_at: string | null }>('POST', '/api/updates/check-all', undefined, { timeoutMs: 30000 }),
    murosStatus: () => request<MurosUpdateStatus>('GET', '/api/updates/muros'),
    installMuros: () => request<UpdateInstallResult>('POST', '/api/updates/muros/install'),
    murosProgress: () => request<MurosUpgradeProgress>('GET', '/api/updates/muros/progress'),
    repairMuros: () => request<{ started: boolean; message?: string; output_tail?: string }>('POST', '/api/updates/muros/repair'),
    rebootRequired: () => request<{ required: boolean; packages: string[] }>('GET', '/api/updates/reboot-required'),
    unattended: () => request<{ enabled: boolean; schedule: string | null; days: string[]; hour: number; minute: number; next_run: string | null; last_run: string | null; excluded_packages: string[] }>('GET', '/api/updates/unattended'),
    saveUnattended: (payload: { enabled: boolean; days: string[]; hour: number; minute: number }) =>
      request<{ enabled: boolean; schedule: string | null; days: string[]; hour: number; minute: number; next_run: string | null; last_run: string | null; excluded_packages: string[] }>('PUT', '/api/updates/unattended', payload),
  },

  hardening: {
    // Read-only : la drop-in est livree par le paquet et appliquee au postinst.
    status: () => request<HardeningStatus>('GET', '/api/hardening'),
  },

  pending: {
    list: () => request<PendingChange[]>('GET', '/api/pending'),
    confirm: (id: string) => request<PendingChange>('POST', `/api/pending/${id}/confirm`),
    rollback: (id: string) => request<PendingChange>('POST', `/api/pending/${id}/rollback`),
  },

  // Pending applies persiste en DB (http, ssh, tls, interface, route)
  pendingApply: {
    list: () => request<Array<{
      id: number
      apply_type: string
      status: string
      summary: string | null
      created_at: string
      expires_at: string
      timeout_seconds: number
      rollback_error: string | null
    }>>('GET', '/api/pending-apply'),
    confirm: (id: number) => request<{ id: number; status: string }>('POST', `/api/pending-apply/${id}/confirm`),
    rollback: (id: number) => request<{ id: number; status: string }>('POST', `/api/pending-apply/${id}/rollback`),
  },

  backupRemote: {
    get: () => request<BackupRemoteConfig>('GET', '/api/backups/remote'),
    set: (data: Partial<BackupRemoteConfig>) =>
      request<BackupRemoteConfig>('PUT', '/api/backups/remote', data),
    test: (override?: Partial<BackupRemoteConfig>) =>
      request<BackupRemoteTestResult>('POST', '/api/backups/remote/test', override),
    push: (name: string) =>
      request<BackupPushResult>('POST', `/api/backups/${encodeURIComponent(name)}/push`),
    getSshKey: () => request<SshKey>('GET', '/api/backups/remote/ssh-key'),
    generateSshKey: (force: boolean) =>
      request<SshKey>('POST', '/api/backups/remote/ssh-key', { force }),
  },

  ha: {
    getConfig: () => request<HaConfig>('GET', '/api/ha/config'),
    setConfig: (data: HaConfig) => request<HaConfig>('PUT', '/api/ha/config', data),
    listVips: () => request<HaVip[]>('GET', '/api/ha/vips'),
    createVip: (data: HaVipInput) => request<HaVip>('POST', '/api/ha/vips', data),
    updateVip: (id: number, data: HaVipInput) =>
      request<HaVip>('PUT', `/api/ha/vips/${id}`, data),
    deleteVip: (id: number) => request<void>('DELETE', `/api/ha/vips/${id}`),
    apply: () => request<HaApplyResult>('POST', '/api/ha/apply'),
    status: () => request<HaStatus>('GET', '/api/ha/status'),
    install: () => request<HaInstallResult>('POST', '/api/ha/install'),
  },

  wireguard: {
    status: () => request<WireGuardStatus>('GET', '/api/wireguard/status'),
    install: () => request<VpnInstallResult>('POST', '/api/wireguard/install'),
    getConfig: () => request<WireGuardConfig>('GET', '/api/wireguard/config'),
    updateConfig: (data: WireGuardConfigInput) =>
      request<WireGuardConfig>('PUT', '/api/wireguard/config', data),
    generateKeypair: () => request<WireGuardKeypair>('POST', '/api/wireguard/keypair'),
    generatePsk: () => request<{ preshared_key: string }>('POST', '/api/wireguard/psk'),
    listPeers: () => request<WireGuardPeer[]>('GET', '/api/wireguard/peers'),
    createPeer: (data: WireGuardPeerInput) =>
      request<WireGuardPeer>('POST', '/api/wireguard/peers', data),
    quickCreatePeer: (data: { name: string; description?: string | null }) =>
      request<WireGuardPeerExport>('POST', '/api/wireguard/peers/quick', data),
    updatePeer: (id: number, data: WireGuardPeerInput) =>
      request<WireGuardPeer>('PUT', `/api/wireguard/peers/${id}`, data),
    deletePeer: (id: number) => request<void>('DELETE', `/api/wireguard/peers/${id}`),
    exportPeer: (id: number, peerPrivateKey?: string) =>
      request<WireGuardPeerExport>('POST', `/api/wireguard/peers/${id}/export${peerPrivateKey ? `?peer_private_key=${encodeURIComponent(peerPrivateKey)}` : ''}`),
    apply: () => request<WireGuardApplyResult>('POST', '/api/wireguard/apply'),
    pending: () => request<ServicePending>('GET', '/api/wireguard/pending'),
  },

  ipsec: {
    status: () => request<IpsecStatus>('GET', '/api/ipsec/status'),
    getConfig: () => request<IpsecGlobalConfig>('GET', '/api/ipsec/config'),
    setConfig: (data: IpsecGlobalConfig) =>
      request<IpsecGlobalConfig>('PUT', '/api/ipsec/config', data),
    install: () => request<VpnInstallResult>('POST', '/api/ipsec/install'),
    listConnections: () => request<IpsecConnection[]>('GET', '/api/ipsec/connections'),
    createConnection: (data: IpsecConnectionInput) =>
      request<IpsecConnection>('POST', '/api/ipsec/connections', data),
    updateConnection: (id: number, data: IpsecConnectionInput) =>
      request<IpsecConnection>('PUT', `/api/ipsec/connections/${id}`, data),
    deleteConnection: (id: number) => request<void>('DELETE', `/api/ipsec/connections/${id}`),
    apply: () => request<IpsecApplyResult>('POST', '/api/ipsec/apply'),
    pending: () => request<ServicePending>('GET', '/api/ipsec/pending'),
    startService: () => request<{ service: string; message: string }>('POST', '/api/ipsec/service/start'),
    stopService: () => request<{ service: string; message: string }>('POST', '/api/ipsec/service/stop'),
    getCa: () => request<IpsecCa | null>('GET', '/api/ipsec/ca'),
    generateCa: (data: IpsecCaGenerate) => request<IpsecCa>('POST', '/api/ipsec/ca', data),
    listCerts: () => request<IpsecCert[]>('GET', '/api/ipsec/certs'),
    createCert: (data: IpsecCertGenerate) =>
      request<IpsecCert>('POST', '/api/ipsec/certs', data),
    importCert: (data: IpsecCertImport) =>
      request<IpsecCert>('POST', '/api/ipsec/certs/import', data),
    revokeCert: (id: number) =>
      request<IpsecCert>('POST', `/api/ipsec/certs/${id}/revoke`),
    deleteCert: (id: number) => request<void>('DELETE', `/api/ipsec/certs/${id}`),
  },

  haSync: {
    getRole: () => request<HaSyncRole>('GET', '/api/ha/role'),
    getConfig: () => request<HaSyncConfig>('GET', '/api/ha/sync/config'),
    updateConfig: (data: HaSyncConfigInput) =>
      request<HaSyncConfig>('PUT', '/api/ha/sync/config', data),
    generateToken: () => request<{ token: string }>('POST', '/api/ha/sync/generate-token'),
    test: () => request<HaSyncTestResult>('POST', '/api/ha/sync/test'),
    push: () => request<HaSyncPushResult>('POST', '/api/ha/sync/push'),
    getLog: () => request<HaSyncLog[]>('GET', '/api/ha/sync/log'),
  },

  tls: {
    status: () => request<TlsStatus>('GET', '/api/tls/status'),
    upload: (data: { cert_pem: string; key_pem: string }) =>
      request<ApplyWithRollback>('POST', '/api/tls/upload', data),
    regenerate: () =>
      request<ApplyWithRollback>('POST', '/api/tls/regenerate-self-signed', {}),
    confirmApply: (pending_id: number) =>
      request<{ status: string; id: number }>('POST', `/api/tls/confirm-apply/${pending_id}`),
    rollbackApply: (pending_id: number) =>
      request<{ status: string; id: number; error?: string | null }>('POST', `/api/tls/rollback-apply/${pending_id}`),
  },

  ssh: {
    status: () => request<SshStatus>('GET', '/api/ssh/status'),
    install: () => request<VpnInstallResult>('POST', '/api/ssh/install'),
    getConfig: () => request<SshConfig>('GET', '/api/ssh/config'),
    updateConfig: (data: SshConfigInput) =>
      request<SshConfig>('PUT', '/api/ssh/config', data),
    apply: (opts?: { skip_rollback?: boolean }) =>
      request<ApplyWithRollback>('POST', `/api/ssh/apply${opts?.skip_rollback ? '?skip_rollback=true' : ''}`),
    confirmApply: (pending_id: number) =>
      request<{ status: string; id: number }>('POST', `/api/ssh/confirm-apply/${pending_id}`),
    rollbackApply: (pending_id: number) =>
      request<{ status: string; id: number; error?: string | null }>('POST', `/api/ssh/rollback-apply/${pending_id}`),
    listKeys: () => request<SshAuthorizedKey[]>('GET', '/api/ssh/keys'),
    addKey: (key_text: string) =>
      request<{ added: boolean; fingerprint?: string | null; message?: string | null }>('POST', '/api/ssh/keys', { key_text }),
    deleteKey: (key_b64: string) =>
      request<{ deleted: boolean }>('DELETE', `/api/ssh/keys/${encodeURIComponent(key_b64)}`),
    setRootPassword: (new_password: string, current_ui_password: string) =>
      request<{ applied: boolean; message: string }>('POST', '/api/ssh/root-password', { new_password, current_ui_password }),
    toggleService: (enabled: boolean) =>
      request<SshServiceToggleResult>('POST', '/api/ssh/service/toggle', { enabled }),
  },

  diag: {
    ping: (target: string, count = 4) =>
      request<DiagResult>('POST', '/api/diag/ping', { target, count }),
    traceroute: (target: string, max_hops = 20) =>
      request<DiagResult>('POST', '/api/diag/traceroute', { target, max_hops }),
    dns: (target: string, record_type = 'A', resolver?: string) =>
      request<DiagResult>('POST', '/api/diag/dns', { target, record_type, resolver }),
    capture: (interface_: string, count = 50, filter_expr?: string) =>
      request<DiagResult>('POST', '/api/diag/capture', { interface: interface_, count, filter_expr }),
    conntrack: (filter?: string, limit = 200) =>
      request<DiagResult>('POST', '/api/diag/conntrack', { filter, limit }),
    portTest: (target: string, port: number, protocol = 'tcp', timeout = 5) =>
      request<DiagResult>('POST', '/api/diag/port-test', { target, port, protocol, timeout }),
    interfaces: () => request<string[]>('GET', '/api/diag/interfaces'),
    routes: () => request<DiagResult>('GET', '/api/diag/routes'),
    addresses: () => request<DiagResult>('GET', '/api/diag/addresses'),
    nft: () => request<DiagResult>('GET', '/api/diag/nft'),
    publicIp: (family: 'auto' | 'v4' | 'v6' = 'auto') =>
      request<DiagResult>('POST', '/api/diag/public-ip', { family }),
  },

  http: {
    status: () => request<HttpServiceStatus>('GET', '/api/http/status'),
    getConfig: () => request<HttpConfig>('GET', '/api/http/config'),
    updateConfig: (data: HttpConfigInput) =>
      request<HttpConfig>('PUT', '/api/http/config', data),
    apply: (opts?: { skip_rollback?: boolean }) =>
      request<ApplyWithRollback>('POST', `/api/http/apply${opts?.skip_rollback ? '?skip_rollback=true' : ''}`),
    confirmApply: (pending_id: number) =>
      request<{ status: string; id: number }>('POST', `/api/http/confirm-apply/${pending_id}`),
    rollbackApply: (pending_id: number) =>
      request<{ status: string; id: number; error?: string | null }>('POST', `/api/http/rollback-apply/${pending_id}`),
  },

  systemActions: {
    reboot: () => request<{ scheduled: boolean; message: string }>('POST', '/api/system/reboot'),
    shutdown: () => request<{ scheduled: boolean; message: string }>('POST', '/api/system/shutdown'),
    listServices: () => request<SystemService[]>('GET', '/api/system/services'),
    listenAddresses: () => request<ListenAddress[]>('GET', '/api/system/listen-addresses'),
    publicIp: () => request<{ ip: string; source: string }>('GET', '/api/system/public-ip'),
  },

  systemSettings: {
    // Reads the system-wide knobs (apply confirmation timeout, etc.).
    // The endpoint returns value + default + allowed choices so the
    // UI does not have to hardcode the list.
    get: () => request<{
      apply_confirm_timeout: { value: number; default: number; choices: number[] }
    }>('GET', '/api/system/settings'),
    setApplyConfirmTimeout: (value: number) =>
      request<{ value: number }>('PUT', '/api/system/settings/apply-confirm-timeout', { value }),
  },

  notifications: {
    getConfig: () => request<NotificationConfig>('GET', '/api/notifications/config'),
    updateConfig: (data: NotificationConfigInput) =>
      request<NotificationConfig>('PUT', '/api/notifications/config', data),
    sendTest: () => request<NotificationTestResult>('POST', '/api/notifications/test'),
    listRules: () => request<NotificationRule[]>('GET', '/api/notifications/rules'),
    updateRule: (id: number, data: NotificationRuleUpdate) =>
      request<NotificationRule>('PUT', `/api/notifications/rules/${id}`, data),
    getLog: () => request<NotificationLog[]>('GET', '/api/notifications/log'),
    status: () =>
      request<{ service_state: string; version: string | null }>(
        'GET', '/api/notifications/status',
      ),
  },

  snmp: {
    status: () => request<SnmpStatus>('GET', '/api/snmp/status'),
    install: () => request<VpnInstallResult>('POST', '/api/snmp/install'),
    getConfig: () => request<SnmpConfig>('GET', '/api/snmp/config'),
    updateConfig: (data: SnmpConfigInput) =>
      request<SnmpConfig>('PUT', '/api/snmp/config', data),
    apply: () => request<SnmpApplyResult>('POST', '/api/snmp/apply'),
    pending: () => request<ServicePending>('GET', '/api/snmp/pending'),
  },

  dhcp: {
    status: () => request<DhcpStatus>('GET', '/api/dhcp/status'),
    getConfig: () => request<DhcpConfig>('GET', '/api/dhcp/config'),
    updateConfig: (data: DhcpConfigInput) =>
      request<DhcpConfig>('PUT', '/api/dhcp/config', data),
    listPools: () => request<DhcpPool[]>('GET', '/api/dhcp/pools'),
    createPool: (data: DhcpPoolInput) =>
      request<DhcpPool>('POST', '/api/dhcp/pools', data),
    updatePool: (id: number, data: DhcpPoolInput) =>
      request<DhcpPool>('PUT', `/api/dhcp/pools/${id}`, data),
    deletePool: (id: number) => request<void>('DELETE', `/api/dhcp/pools/${id}`),
    listLeases: () => request<DhcpStaticLease[]>('GET', '/api/dhcp/leases'),
    createLease: (data: DhcpStaticLeaseInput) =>
      request<DhcpStaticLease>('POST', '/api/dhcp/leases', data),
    updateLease: (id: number, data: DhcpStaticLeaseInput) =>
      request<DhcpStaticLease>('PUT', `/api/dhcp/leases/${id}`, data),
    deleteLease: (id: number) => request<void>('DELETE', `/api/dhcp/leases/${id}`),
    activeLeases: () => request<DhcpActiveLease[]>('GET', '/api/dhcp/leases/active'),
    apply: () => request<{ applied: boolean } & ServicePending>('POST', '/api/dhcp/apply'),
    pending: () => request<ServicePending>('GET', '/api/dhcp/pending'),
  },

  dnsServer: {
    status: () => request<DnsServerStatus>('GET', '/api/dns/recursive/status'),
    getConfig: () => request<DnsServerConfig>('GET', '/api/dns/recursive/config'),
    updateConfig: (data: DnsServerConfigInput) =>
      request<DnsServerConfig>('PUT', '/api/dns/recursive/config', data),
    listRecords: () => request<DnsLocalRecord[]>('GET', '/api/dns/recursive/records'),
    createRecord: (data: DnsLocalRecordInput) =>
      request<DnsLocalRecord>('POST', '/api/dns/recursive/records', data),
    updateRecord: (id: number, data: DnsLocalRecordInput) =>
      request<DnsLocalRecord>('PUT', `/api/dns/recursive/records/${id}`, data),
    deleteRecord: (id: number) =>
      request<void>('DELETE', `/api/dns/recursive/records/${id}`),
    apply: () => request<{ applied: boolean } & ServicePending>('POST', '/api/dns/recursive/apply'),
    pending: () => request<ServicePending>('GET', '/api/dns/recursive/pending'),
  },

  // Cross-service endpoints : aggregated pending state for the sidebar
  // (single poll surfacing every managed service) and the audit log
  // (Save / Apply trail per service).
  services: {
    pending: () => request<ServicesPendingAggregate>('GET', '/api/services/pending'),
    log: (name?: string, limit = 50) =>
      request<{ entries: ServiceApplyLogEntry[] }>(
        'GET',
        `/api/services/log?limit=${limit}${name ? `&name=${encodeURIComponent(name)}` : ''}`,
      ),
  },
}



export type HaConfig = {
  enabled: boolean
  role: 'primary' | 'secondary'
  peer_address: string
  sync_interface: string
  conntrack_sync: boolean
  preempt: boolean
}

export type HaVip = {
  id: number
  vrid: number
  interface: string
  vip_cidr: string
  auth_pass: string
  priority: number | null
  description: string | null
  enabled: boolean
}

export type HaVipInput = Omit<HaVip, 'id'>

export type HaApplyResult = {
  applied: boolean
  dry_run: boolean
  message: string
}

export type HaStatus = {
  keepalived_active: boolean
  conntrackd_active: boolean
  keepalived_state?: string
  conntrackd_state?: string
  keepalived_installed: boolean
  conntrackd_installed: boolean
  keepalived_version: string | null
  conntrackd_version: string | null
  vrrp_instances: { name: string; state: string }[]
  conntrack_stats: Record<string, number>
}

export type HaInstallResult = {
  installed: boolean
  already_present: string[]
  newly_installed: string[]
  output_tail: string
}

export type VpnInstallResult = HaInstallResult

export type WireGuardInterface = {
  name: string
  peers: number
  listen_port: number | null
}

export type WireGuardStatus = {
  installed: boolean
  version: string | null
  interfaces: WireGuardInterface[]
  service_active: boolean
  service_state?: string
}

export type IpsecSa = {
  name: string
  state: string
  details: string
}

export type IpsecStatus = {
  installed: boolean
  version: string | null
  service_active: boolean
  service_state?: string
  service_name: string | null
  active_sas: IpsecSa[]
  globally_enabled: boolean
}

export type IpsecGlobalConfig = { enabled: boolean }

export type WireGuardConfigInput = {
  enabled: boolean
  interface_name: string
  address_cidr: string
  listen_port: number
  private_key: string
  public_key: string
  mtu: number | null
  public_endpoint: string
}

export type WireGuardConfig = WireGuardConfigInput & { id: number }

export type WireGuardPeerInput = {
  name: string
  public_key: string
  preshared_key: string | null
  allowed_ips: string
  client_allowed_ips: string
  endpoint: string | null
  persistent_keepalive: number
  description: string | null
  enabled: boolean
}

export type WireGuardPeer = WireGuardPeerInput & { id: number }

export type WireGuardKeypair = {
  private_key: string
  public_key: string
}

export type WireGuardApplyResult = {
  message: string
  interface?: string | null
  config_preview?: string | null
}

export type WireGuardPeerExport = {
  config_text: string
  qr_svg: string | null
}

export type IpsecConnectionInput = {
  name: string
  auth_mode: string
  local_addrs: string
  remote_addrs: string
  local_id: string | null
  remote_id: string | null
  psk: string
  local_cert_id: number | null
  remote_cert_id: number | null
  local_ts: string
  remote_ts: string
  ike_proposals: string
  esp_proposals: string
  start_action: string
  description: string | null
  enabled: boolean
}

export type IpsecCa = {
  id: number
  subject_cn: string
  subject_o: string
  cert_pem: string
  validity_days: number
  created_at: string
  expires_at: string | null
}

export type IpsecCaGenerate = {
  subject_cn: string
  subject_o: string
  validity_days: number
}

export type IpsecCert = {
  id: number
  name: string
  subject_cn: string
  san: string | null
  cert_pem: string
  is_local: boolean
  serial: string
  revoked: boolean
  revoked_at: string | null
  validity_days: number
  created_at: string
  expires_at: string | null
  has_key: boolean
}

export type IpsecCertGenerate = {
  name: string
  subject_cn: string
  san: string | null
  validity_days: number
  is_local: boolean
}

export type IpsecCertImport = {
  name: string
  cert_pem: string
}

export type IpsecConnection = IpsecConnectionInput & { id: number }

export type IpsecApplyResult = {
  message: string
  service?: string | null
  swanctl_output?: string | null
  conf_preview?: string | null
}

export type NotificationConfigInput = {
  enabled: boolean
  smtp_host: string
  smtp_port: number
  smtp_user: string | null
  smtp_password: string | null
  use_tls: boolean
  from_addr: string
  to_addrs: string
}

export type NotificationConfig = NotificationConfigInput & { id: number }

export type NotificationRule = {
  id: number
  event_type: string
  enabled: boolean
  throttle_minutes: number
  description: string | null
}

export type NotificationRuleUpdate = {
  enabled: boolean
  throttle_minutes: number
}

export type NotificationLog = {
  id: number
  event_type: string
  subject: string
  body: string
  success: boolean
  error: string | null
  created_at: string
}

export type NotificationTestResult = {
  sent: boolean
  reason: string | null
}

export type TlsStatus = {
  present: boolean
  subject_cn: string | null
  issuer_cn: string | null
  san: string[]
  not_before: string | null
  not_after: string | null
  days_remaining: number | null
  fingerprint_sha256: string | null
  is_self_signed: boolean | null
  key_present: boolean
  error?: string | null
}

export type DiagResult = {
  command: string
  returncode: number
  stdout: string
  stderr: string
  duration_ms: number
  timed_out: boolean
}

export type ApplyWithRollback = {
  applied: boolean
  message: string
  preview?: string | null
  pending_apply_id?: number | null
  rollback_timeout_seconds?: number | null
}

export type SshConfigInput = {
  port: number
  listen_address: string
  permit_root_login: 'yes' | 'no' | 'prohibit-password'
  password_authentication: boolean
  pubkey_authentication: boolean
  max_auth_tries: number
  client_alive_interval: number
  client_alive_count_max: number
  confirm_loopback?: boolean
  skip_rollback?: boolean
}

export type SshAuthorizedKey = {
  type: string
  key_b64: string
  comment: string
  fingerprint: string
  line: number
}

export type ListenAddress = {
  label: string
  address: string
  interface: string
  loopback: boolean
}

export type HttpConfigInput = {
  listen_address: string
  port_https: number
  port_http: number
  redirect_http_to_https: boolean
  confirm_loopback?: boolean
  skip_rollback?: boolean
}

export type HttpConfig = HttpConfigInput & { id: number }

export type SshConfig = SshConfigInput & { id: number }

export type AuditLogEntry = {
  id: number
  timestamp: string
  user_id: number | null
  username: string | null
  method: string
  path: string
  status_code: number
  client_ip: string | null
  duration_ms: number
  action_summary: string | null
}

export type SystemService = {
  unit: string
  display_name: string
  page: string
  category: string  // muros / core / ha / vpn / obs
  status: string    // active / inactive / failed / activating / unknown
}

export type SshStatus = {
  sshd_installed: boolean
  service_active: boolean
  service_state?: string
  version: string | null
  dropin_present: boolean
  dropin_path: string
  admin_disabled: boolean
}

export type SshServiceToggleResult = {
  applied: boolean
  admin_disabled: boolean
  service_active: boolean
  message: string
}

export type HaSyncRole = {
  role: string  // MASTER / BACKUP / FAULT / STANDALONE
  writable: boolean
}

export type HaSyncConfigInput = {
  enabled: boolean
  peer_url: string
  peer_token: string
  sync_mode: string
  verify_tls: boolean
}

export type HaSyncConfig = HaSyncConfigInput & { id: number }

export type HaSyncLog = {
  id: number
  direction: string
  success: boolean
  error: string | null
  duration_ms: number
  db_size_bytes: number
  triggered_by: string
  created_at: string
}

export type HaSyncTestResult = {
  success: boolean
  peer_role?: string | null
  peer_version?: string | null
  error?: string | null
}

export type HaSyncPushResult = {
  success: boolean
  duration_ms: number
  db_size_bytes: number
}

export type SnmpConfigInput = {
  enabled: boolean
  port: number
  community: string
  allowed_networks: string
  syscontact: string
  syslocation: string
}

export type SnmpConfig = SnmpConfigInput & { id: number }

export type SnmpStatus = {
  installed: boolean
  snmpd_installed: boolean
  snmp_tools_installed: boolean
  service_active: boolean
  service_state?: string
  version: string | null
}

export type HttpServiceStatus = {
  installed: boolean
  service_active: boolean
  service_state?: string
  version: string | null
}

export type SnmpApplyResult = {
  message: string
  service?: string | null
  conf_preview?: string | null
}

// --- DHCP (Kea) ---

export type DhcpStatus = {
  enabled: boolean
  installed: boolean
  service_state: string
  version: string | null
  pools_count: number
  static_leases_count: number
  active_leases_count: number
  config_path: string
  leases_path: string
}

export type DhcpConfigInput = {
  enabled: boolean
  authoritative: boolean
  default_lease_seconds: number
  domain: string | null
}

export type DhcpConfig = DhcpConfigInput

export type DhcpPoolInput = {
  interface_id: number
  range_start: string
  range_end: string
  gateway: string | null
  dns_servers: string | null
  lease_seconds: number | null
  enabled: boolean
  comment: string | null
}

export type DhcpPool = DhcpPoolInput & { id: number }

export type DhcpStaticLeaseInput = {
  pool_id: number
  mac: string
  ip: string
  hostname: string | null
  comment: string | null
}

export type DhcpStaticLease = DhcpStaticLeaseInput & { id: number }

export type DhcpActiveLease = {
  expiry: number
  mac: string
  ip: string
  hostname: string | null
  client_id: string | null
}

// --- DNS recursive (Unbound) ---

export type DnsServerStatus = {
  enabled: boolean
  installed: boolean
  service_state: string
  version: string | null
  records_count: number
  system_resolver_active: boolean
  config_path: string
}

export type DnsServerConfigInput = {
  enabled: boolean
  allow_query_cidrs: string
  dnssec: boolean
  prefetch: boolean
  forwarders: string | null
  use_as_system_resolver: boolean
}

export type DnsServerConfig = DnsServerConfigInput

export type DnsLocalRecordInput = {
  record_type: 'A' | 'AAAA' | 'CNAME' | 'TXT' | 'MX' | 'SRV' | 'PTR'
  name: string
  value: string
  comment: string | null
}

export type DnsLocalRecord = DnsLocalRecordInput & { id: number }

// --- Types Systeme ---
export type Backup = {
  name: string
  size_bytes: number
  created_at: string
  label: string
  manifest: Record<string, unknown>
}

export type BackupRestoreResult = {
  restored: string
  db_restored: boolean
  extracted_to: string
  manifest: Record<string, unknown>
}

export type NtpStatus = {
  available: boolean
  backend?: 'chrony' | 'none'
  ref_name?: string
  stratum?: number
  last_offset_seconds?: number
  rms_offset_seconds?: number
  leap_status?: string
  ntp_synchronized?: boolean
  ntp_active?: boolean
  timezone?: string
}

export type NtpServers = {
  servers: string[]
  config_path: string
  serve_lan: boolean
  served_subnets: string[]
}

export type DnsConfig = {
  resolvers: string[]
  search_domains: string[]
  config_path: string
}

export type UpdatePackage = {
  name: string
  new_version: string
  current_version: string
}

export type UpdateStatus = {
  last_check_at: string | null
  packages: UpdatePackage[]
  packages_count: number
  apt_available: boolean
}

export type UpdateInstallResult = {
  installed: boolean
  output_tail: string
  snapshot?: { name?: string | null; error?: string } | null
}

export type MurosUpgradeProgress = {
  state: 'idle' | 'running' | 'done' | 'failed' | 'unknown'
  detail: string | null
  log_tail: string
  package: { status: string; version: string } | null
}

export type MurosUpdateStatus = {
  apt_available: boolean
  installed: string | null
  candidate: string | null
  upgrade_available: boolean
  pending_packages: UpdatePackage[]
  last_check_at: string | null
  deb_url: string | null
  release_notes: string | null
  release_published_at: string | null
}

// --- Hardening sysctl ---
export type HardeningItem = {
  key: string
  recommended: string
  current: string | null
  managed_by_muros: boolean
  ok: boolean
  description: string
  category: string
}

export type HardeningStatus = {
  items: HardeningItem[]
  hardened: boolean
  dropin_path: string
  dropin_exists: boolean
  apply_enabled: boolean
}

// --- Remote backup ---
export type BackupRemoteConfig = {
  enabled: boolean
  host: string
  user: string
  port: number
  path: string
  ssh_key_path: string
  last_push_at: string | null
  last_error: string | null
}

export type BackupPushResult = {
  pushed: boolean
  dry_run: boolean
  message: string
  command?: string
  output_tail?: string[]
}

export type BackupRemoteTestResult = {
  ok: boolean
  dry_run: boolean
  message: string
}

export type SshKey = {
  exists?: boolean
  generated?: boolean
  dry_run?: boolean
  message?: string
  key_path: string
  public_key: string
}

export type PendingChange = {
  id: string
  kind: 'interface' | 'route' | 'vlan'
  description: string
  started_at: string
  expires_at: string
  timeout_seconds: number
  state: 'pending' | 'committed' | 'rolled_back' | 'rollback_failed'
  message: string | null
  detail: Record<string, unknown>
}
