// Deterministic per-zone color, derived from a simple hash of the name.
// Reusable everywhere (Zones, Rules, Network) so a zone maps to the same
// color visually regardless of the page.

const PALETTE: { bg: string; text: string; border: string; dot: string }[] = [
  { bg: 'bg-sky-100',     text: 'text-sky-800',     border: 'border-sky-200',     dot: 'bg-sky-500'     },
  { bg: 'bg-emerald-100', text: 'text-emerald-800', border: 'border-emerald-200', dot: 'bg-emerald-500' },
  { bg: 'bg-amber-100',   text: 'text-amber-800',   border: 'border-amber-200',   dot: 'bg-amber-500'   },
  { bg: 'bg-violet-100',  text: 'text-violet-800',  border: 'border-violet-200',  dot: 'bg-violet-500'  },
  { bg: 'bg-rose-100',    text: 'text-rose-800',    border: 'border-rose-200',    dot: 'bg-rose-500'    },
  { bg: 'bg-indigo-100',  text: 'text-indigo-800',  border: 'border-indigo-200',  dot: 'bg-indigo-500'  },
  { bg: 'bg-lime-100',    text: 'text-lime-800',    border: 'border-lime-200',    dot: 'bg-lime-500'    },
  { bg: 'bg-pink-100',    text: 'text-pink-800',    border: 'border-pink-200',    dot: 'bg-pink-500'    },
  { bg: 'bg-cyan-100',    text: 'text-cyan-800',    border: 'border-cyan-200',    dot: 'bg-cyan-500'    },
  { bg: 'bg-orange-100',  text: 'text-orange-800',  border: 'border-orange-200',  dot: 'bg-orange-500'  },
]

// Simple FNV-like hash. Stable and fast, good enough for zone names.
function hashStr(s: string): number {
  let h = 2166136261 >>> 0
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = (h * 16777619) >>> 0
  }
  return h
}

export function zoneColor(name: string | null | undefined) {
  if (!name) return PALETTE[0]
  // Common pseudo-convention cases: easily recognizable colors.
  const lower = name.toLowerCase()
  if (lower === 'wan' || lower === 'internet') return PALETTE[2]   // amber
  if (lower === 'lan' || lower === 'local')    return PALETTE[1]   // emerald
  if (lower === 'dmz')                          return PALETTE[3]   // violet
  if (lower === 'guest' || lower === 'wifi')   return PALETTE[0]   // sky
  if (lower === 'mgmt' || lower === 'admin')   return PALETTE[5]   // indigo
  return PALETTE[hashStr(name) % PALETTE.length]
}

export function ZoneBadge({ name, className }: { name: string; className?: string }) {
  const c = zoneColor(name)
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded border font-mono ${c.bg} ${c.text} ${c.border} ${className || ''}`}>
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {name}
    </span>
  )
}
