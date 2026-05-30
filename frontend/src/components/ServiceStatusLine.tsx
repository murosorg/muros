/**
 * Compact single-line status indicator for an optional system service
 * (sshd, snmpd, strongswan, wireguard, keepalived, ...). The package is
 * always installed by MurOS, so the only useful pieces of info are the
 * live service state and the binary version.
 */
export type ServiceState = 'active' | 'inactive' | 'failed' | 'unknown'

// Strip Debian / Ubuntu packaging suffixes from a version string so the
// pill displays "unbound 1.19" instead of "unbound 1.19-1+deb13u1".
// The full string is still exposed via the title (hover tooltip).
//
// Examples:
//   "2.91-1+deb13u1"   -> "2.91"
//   "6.0.1-6+deb13u5"  -> "6.0.1"
//   "1.22.0-2+deb13u3" -> "1.22.0"
//   "1:2.3.3-1"        -> "1:2.3.3"
//   "1.0.20210914-3"   -> "1.0.20210914"
//   "0.9.0-rc110"      -> "0.9.0-rc110" (left untouched, no Debian rev)
function stripPackageSuffix(v?: string | null): string {
  if (!v) return ''
  // Drop Debian/Ubuntu build metadata: a trailing "-<num>(+...)" only
  // when the "+..." part starts with "deb", "ubuntu", "dfsg" or "build".
  // Plain "-rcXX", "-beta", "-3" without that marker are preserved.
  return v.replace(/-\d+\+(?:deb|ubuntu|dfsg|build)[\w.]*$/, '')
}

const STATE_STYLE: Record<ServiceState, { dot: string; label: string; text: string }> = {
  active:   { dot: 'bg-emerald-500',  label: 'active',   text: 'text-emerald-700' },
  inactive: { dot: 'bg-slate-400',    label: 'inactive', text: 'text-slate-600' },
  failed:   { dot: 'bg-red-500',      label: 'failed',   text: 'text-red-700' },
  unknown:  { dot: 'bg-slate-300',    label: 'unknown',  text: 'text-slate-500' },
}

/**
 * Inline variant for placement inside a page banner (PageHeader status slot)
 * or a section banner (CardHeader children). Renders dot + optional service
 * name + state label + version with no border, no background and no padding.
 * Omit `name` when the surrounding page title already identifies the service
 * (e.g. "DHCP server" implies Kea).
 */
// When a caller passes both `name="keepalived"` and a `version` produced
// by `pkg_version("keepalived")` that already prepends the package name
// ("keepalived 1:2.3.3-1"), we would render the package name twice as
// "keepalived inactive keepalived 1:2.3.3-1". Strip that leading prefix
// in the display so the version slot shows just the numeric part.
function stripNamePrefix(version: string, name?: string): string {
  if (!name) return version
  const prefix = `${name} `
  return version.startsWith(prefix) ? version.slice(prefix.length) : version
}

export function ServiceStatusInline({
  name, state, version,
}: { name?: string; state: ServiceState; version?: string | null }) {
  const s = STATE_STYLE[state] ?? STATE_STYLE.unknown
  const short = stripNamePrefix(stripPackageSuffix(version), name)
  return (
    <span className="inline-flex items-center gap-2 text-xs">
      <span className={`inline-block w-2 h-2 rounded-full ${s.dot}`}></span>
      {name && <span className="font-mono text-gray-800">{name}</span>}
      <span className={`font-medium ${s.text}`}>{s.label}</span>
      <span className="font-mono text-gray-500 truncate max-w-[18rem]" title={version || ''}>
        {short || 'n/a'}
      </span>
    </span>
  )
}
