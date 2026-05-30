import { ChangeEvent } from 'react'

/**
 * IP input + CIDR select.
 *
 * The admin types the IP "192.168.1.70" in the left field and picks /24
 * from the dropdown on the right. The exposed value is always in the
 * "ip/prefix" form (canonical CIDR on the API side), which prevents the
 * admin from forgetting the mask (a real lockout cause).
 *
 * Offered prefixes:
 *   - IPv4:  /8 to /30
 *   - IPv6:  /32 to /128 in steps (the component detects the version)
 *
 * /31 and /32 (resp. /127 and /128) are not offered by default: they
 * would isolate the interface from its LAN. The API rejects them anyway.
 */
type Props = {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
}

const V4_PREFIXES = [8, 16, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30]
const V6_PREFIXES = [32, 48, 56, 60, 64, 80, 96, 112, 126]

function split(value: string): { ip: string; prefix: string } {
  const trimmed = (value || '').trim()
  const idx = trimmed.indexOf('/')
  if (idx < 0) return { ip: trimmed, prefix: '' }
  return { ip: trimmed.slice(0, idx), prefix: trimmed.slice(idx + 1) }
}

function isV6(ip: string): boolean {
  return ip.includes(':')
}

export default function CidrInput({
  value, onChange, placeholder, className, disabled,
}: Props) {
  const { ip, prefix } = split(value)
  const v6 = isV6(ip)
  const prefixes = v6 ? V6_PREFIXES : V4_PREFIXES
  const defaultPrefix = v6 ? 64 : 24
  const effectivePrefix = prefix || (ip ? String(defaultPrefix) : '')

  const onIp = (e: ChangeEvent<HTMLInputElement>) => {
    const nextIp = e.target.value
    if (!nextIp) {
      onChange('')
      return
    }
    onChange(`${nextIp}/${effectivePrefix || defaultPrefix}`)
  }

  const onPrefix = (e: ChangeEvent<HTMLSelectElement>) => {
    const nextPrefix = e.target.value
    if (!ip) return
    onChange(`${ip}/${nextPrefix}`)
  }

  return (
    <div className={`flex gap-1 ${className || ''}`}>
      <input
        className="input font-mono flex-1"
        placeholder={placeholder || '192.168.1.1'}
        value={ip}
        onChange={onIp}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
      />
      <select
        className="input font-mono w-20"
        value={effectivePrefix}
        onChange={onPrefix}
        disabled={disabled || !ip}
        aria-label="CIDR mask"
      >
        {!effectivePrefix && <option value="">/?</option>}
        {prefixes.map((p) => (
          <option key={p} value={p}>/{p}</option>
        ))}
      </select>
    </div>
  )
}
