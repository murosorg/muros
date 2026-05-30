import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useMemo, useState } from 'react'
import { LogOut } from 'lucide-react'
import { api, auth, Health, SystemInfo, User } from '../lib/api'
import RollbackModal from './RollbackModal'
import PendingChangesBar from './PendingChangesBar'
import { useConfirm } from './ConfirmModal'


// Sidebar nav entries. Labels are hardcoded English strings: MurOS is EN-only.
// `service` (optional) links the entry to a managed service name from
// `service_apply_state`. When that service is dirty, an orange dot is
// rendered next to the entry to surface the pending apply globally.
const navItems: { to: string; label: string; section: string | null; service?: string; adminOnly?: boolean }[] = [
  { to: '/', label: 'Dashboard', section: null },
  { to: '/network', label: 'Interfaces', section: 'Network' },
  { to: '/routes', label: 'Routing', section: 'Network' },
  { to: '/wan', label: 'WAN gateways', section: 'Network' },
  { to: '/zones', label: 'Zones', section: 'Firewall' },
  { to: '/firewall/rules', label: 'Filter rules', section: 'Firewall' },
  { to: '/firewall/services', label: 'Services', section: 'Firewall' },
  { to: '/nat', label: 'NAT', section: 'Firewall' },
  { to: '/services/dhcp', label: 'DHCP server', section: 'Services', service: 'dhcp' },
  { to: '/services/dns', label: 'DNS server', section: 'Services', service: 'dns' },
  { to: '/services/ntp', label: 'NTP server', section: 'Services' },
  { to: '/vpn/wireguard', label: 'WireGuard', section: 'VPN', service: 'wireguard' },
  { to: '/vpn/ipsec', label: 'IPsec', section: 'VPN', service: 'ipsec' },
  { to: '/logs', label: 'Logs', section: 'Observability' },
  { to: '/notifications', label: 'Notifications', section: 'Observability', service: 'notifications' },
  { to: '/snmp', label: 'SNMP', section: 'Observability', service: 'snmp' },
  { to: '/diagnostic', label: 'Diagnostics', section: 'Administration' },
  { to: '/ha', label: 'High availability', section: 'Administration', service: 'ha' },
  { to: '/system', label: 'System', section: 'Administration' },
  { to: '/access/http', label: 'HTTP access', section: 'Administration', service: 'http' },
  { to: '/ssh', label: 'SSH access', section: 'Administration', service: 'ssh' },
  { to: '/access/users', label: 'Users', section: 'Administration', adminOnly: true },
]

export default function Layout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [health, setHealth] = useState<Health | null>(null)
  // Counter of available updates (apt + muros). Shows an amber dot
  // next to the version in the sidebar when non-zero. Polled every
  // 5 min through GET endpoints (cached on the backend, cheap).
  const [updatesAvailable, setUpdatesAvailable] = useState(0)
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [me, setMe] = useState<User | null>(null)
  const [haRole, setHaRole] = useState<{ role: string; writable: boolean } | null>(null)
  // Set of service names currently dirty (saved but not applied).
  // Drives the orange dot next to sidebar entries linked to a service.
  // Single poll for all services via /api/services/pending every 5s.
  const [dirtyServices, setDirtyServices] = useState<Set<string>>(new Set())

  // Compute the active parent section to highlight the section title
  // when one of its children is selected. Lets the admin always know
  // which group they are in on flat hierarchies.
  const activeSection = useMemo(() => {
    const m = navItems.find((it) => {
      if (it.to === '/') return location.pathname === '/'
      return location.pathname === it.to || location.pathname.startsWith(it.to + '/')
    })
    return m?.section ?? null
  }, [location.pathname])

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
    api.systemInfo().then(setInfo).catch(() => setInfo(null))
    api.auth.me().then(setMe).catch(() => setMe(null))
    api.haSync.getRole().then(setHaRole).catch(() => setHaRole(null))
    // Updates counter: read the cache (GET, cheap), aggregate apt +
    // muros. Refreshed every 5 min so the dot badge does not stay
    // displayed after the admin has installed the updates.
    const refreshUpdates = () => {
      Promise.all([
        api.updates.status().catch(() => null),
        api.updates.murosStatus().catch(() => null),
      ]).then(([apt, muros]) => {
        const aptCount = apt?.packages_count ?? 0
        const murosUp = muros?.upgrade_available ? 1 : 0
        setUpdatesAvailable(aptCount + murosUp)
      })
    }
    refreshUpdates()
    const id = window.setInterval(refreshUpdates, 5 * 60 * 1000)
    return () => window.clearInterval(id)
  }, [])
  useEffect(() => {
    const id = setInterval(() => {
      api.haSync.getRole().then(setHaRole).catch(() => {})
    }, 10000)
    return () => clearInterval(id)
  }, [])

  // Poll the aggregated services pending endpoint every 5s. One single
  // round-trip surfaces the dirty state of every managed service, the
  // sidebar then decorates the matching nav entries with an orange dot.
  useEffect(() => {
    let stopped = false
    const tick = async () => {
      try {
        const r = await api.services.pending()
        if (stopped) return
        setDirtyServices(new Set(r.dirty_services))
      } catch { /* silent */ }
    }
    void tick()
    const id = window.setInterval(tick, 5000)
    return () => { stopped = true; window.clearInterval(id) }
  }, [])

  const { confirm: confirmLogout, ConfirmHost: LogoutConfirmHost } = useConfirm()

  // Logout asks for confirmation: the button sits next to the admin
  // username in the sidebar header where it is easy to misclick, and
  // logging out kicks the user back to the login screen which is
  // disruptive when triggered by mistake.
  const logout = async () => {
    const ok = await confirmLogout({
      title: 'Log out',
      message: 'You will be returned to the login screen. Unsaved changes in modals will be lost.',
      confirmLabel: 'Log out',
    })
    if (!ok) return
    auth.logout()
    navigate('/login', { replace: true })
  }

  const grouped = useMemo(() => {
    const g: Record<string, typeof navItems> = {}
    for (const item of navItems) {
      // Admin-only entries (user management) are hidden from accounts
      // that root has not promoted to administrator.
      if (item.adminOnly && !me?.is_admin) continue
      const key = item.section || ''
      if (!g[key]) g[key] = []
      g[key].push(item)
    }
    return g
  }, [me])

  return (
    <div className="flex h-full">
      <aside className="w-60 shrink-0 bg-neutral-900 border-r border-neutral-950 flex flex-col">
        {/* Bloc logo/identite. Logo seul sur sa ligne, version en dessous,
            plus petite et neutre, pour laisser respirer le logo. */}
        <div className="px-3 py-3 border-b border-neutral-800">
          <div className="flex items-center justify-between gap-2">
            <NavLink
              to="/"
              className="inline-flex items-center shrink-0 rounded cursor-pointer opacity-90 hover:opacity-100 transition-opacity focus:outline-none focus:ring-1 focus:ring-yellow-500/60"
              title="Back to dashboard"
              aria-label="Back to dashboard"
            >
              <img src="/logo.svg" alt="MurOS" className="h-6 w-auto" />
            </NavLink>
            {health && (
              <NavLink
                to="/system/updates"
                className="text-[10px] font-mono text-neutral-200 hover:text-white bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 inline-flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors"
                title={updatesAvailable > 0
                  ? `${updatesAvailable} update(s) available - click to review`
                  : `MurOS v${health.version} - click to check for updates`}
              >
                v{health.version}
                {updatesAvailable > 0 && (
                  <span
                    className="relative inline-flex w-1.5 h-1.5"
                    title={`${updatesAvailable} update(s) available - click to review`}
                    aria-label={`${updatesAvailable} update(s) available`}
                  >
                    <span className="absolute inset-0 rounded-full bg-amber-400 opacity-60 animate-ping" />
                    <span className="relative inline-flex rounded-full w-1.5 h-1.5 bg-amber-400" />
                  </span>
                )}
              </NavLink>
            )}
          </div>
          {(me || info) && (
            <div className="mt-1.5 flex items-center justify-between gap-2 text-xs">
              <div className="min-w-0 flex-1">
                {me && (
                  <NavLink
                    to="/access/http"
                    className="font-mono text-white hover:text-steel-300 block truncate"
                    title={`${me.username}${info?.hostname ? ' @ ' + info.hostname : ''}`}
                  >
                    {me.username}
                    {info?.hostname && (
                      <span className="text-neutral-500"> @{info.hostname}</span>
                    )}
                  </NavLink>
                )}
              </div>
              {me && (
                <button
                  onClick={logout}
                  className="text-neutral-300 hover:text-white shrink-0 text-xs flex items-center gap-1"
                  title="Log out"
                  aria-label="Log out"
                >
                  <LogOut size={12} aria-hidden="true" />
                  Logout
                </button>
              )}
            </div>
          )}
          {info && !info.apply_enabled && (
            <div
              className="mt-1.5 inline-block text-[10px] font-mono uppercase tracking-wider bg-amber-500 text-white px-1.5 py-0.5 rounded"
              title="Dry-run mode: no system change is applied (MUROS_APPLY off)."
            >
              Dry-run
            </div>
          )}
        </div>

        <nav className="flex-1 py-3 overflow-y-auto">
          {Object.entries(grouped).map(([section, items]) => (
            <div key={section || 'root'} className="mb-5">
              {section && (
                <div
                  className={`pl-2 mt-3 mb-2 text-sm font-bold uppercase tracking-widest select-none transition-colors ${
                    activeSection === section ? 'text-steel-300' : 'text-white'
                  }`}
                >
                  {section}
                </div>
              )}
              {items.map((item) => {
                const pending = !!item.service && dirtyServices.has(item.service)
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === '/'}
                    className={({ isActive }) =>
                      `flex items-center justify-between pr-4 py-1.5 text-sm transition-colors ${
                        isActive
                          ? 'bg-neutral-800 text-steel-300 border-l-2 border-steel-400 pl-[22px] font-medium'
                          : 'text-white hover:bg-neutral-800 border-l-2 border-transparent pl-6'
                      }`
                    }
                  >
                    <span>{item.label}</span>
                    {pending && (
                      <span
                        className="w-2 h-2 rounded-full bg-orange-500 shrink-0"
                        title="Saved but not applied yet"
                        aria-label="pending apply"
                      />
                    )}
                  </NavLink>
                )
              })}
            </div>
          ))}
        </nav>

      </aside>

      <main className="flex-1 overflow-y-auto bg-white">
        {info?.apply_enabled === false && (
          <div className="bg-amber-50 border-b border-amber-300 px-6 py-2 text-xs text-amber-900 flex items-center gap-3">
            <span className="font-semibold uppercase tracking-wider text-[10px] bg-amber-300 text-amber-900 px-1.5 py-0.5 rounded">
              Dry-run mode
            </span>
            <span>Changes are saved to the database but not applied to the Linux kernel. Start the backend with MUROS_APPLY=true as root to apply them for real.</span>
          </div>
        )}
        {haRole && !haRole.writable && haRole.role !== 'STANDALONE' && (
          <div className="bg-red-50 border-b border-red-400 px-6 py-2 text-xs text-red-900 flex items-center gap-3">
            <span className="font-semibold uppercase tracking-wider text-[10px] bg-red-500 text-white px-1.5 py-0.5 rounded">
              Role {haRole.role}
            </span>
            <span>
              This node is in {haRole.role} VRRP state. All changes must be made
              on the MASTER node, they will be replicated here automatically.
            </span>
          </div>
        )}
        <Outlet />
      </main>
      <PendingChangesBar />
      <RollbackModal />
      <LogoutConfirmHost />
    </div>
  )
}
