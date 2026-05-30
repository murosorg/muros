// Centralized formatting helpers for MurOS.
//
// Goal: stop having 5 different ways to display a date across the app
// (native toLocaleString vs raw ISO vs hand-rolled). A single source of
// truth, replacing the call sites as we go.
//
// Chosen format: YYYY-MM-DD HH:mm for datetime, without seconds to stay
// readable. Short relative form ("3 min ago", "yesterday", "2 d ago") for
// the sense of immediacy on event streams (logs, sync, last update).

function pad(n: number): string { return n < 10 ? '0' + n : String(n) }

function parse(input: string | number | Date | null | undefined): Date | null {
  if (input === null || input === undefined || input === '') return null
  const d = input instanceof Date ? input : new Date(input)
  return Number.isFinite(d.getTime()) ? d : null
}

function datetime(input: string | number | Date | null | undefined): string {
  // Format: YYYY-MM-DD HH:mm (short ISO 8601, language-neutral, standard
  // in OSS / tech tooling). Empty -> '-'.
  const d = parse(input)
  if (!d) return '-'
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

function date(input: string | number | Date | null | undefined): string {
  // Format: YYYY-MM-DD (ISO 8601). Empty -> '-'.
  const d = parse(input)
  if (!d) return '-'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function relative(input: string | number | Date | null | undefined): string {
  // Short English relative form: '3 min ago', 'yesterday', '2 d ago', or
  // the date when older than 7 days. Empty -> '-'.
  const d = parse(input)
  if (!d) return '-'
  const diffMs = Date.now() - d.getTime()
  const diffS = Math.round(diffMs / 1000)
  if (diffS < 0) return 'in the future'
  if (diffS < 60) return 'just now'
  const diffM = Math.round(diffS / 60)
  if (diffM < 60) return `${diffM} min ago`
  const diffH = Math.round(diffM / 60)
  if (diffH < 24) return `${diffH} h ago`
  const diffD = Math.round(diffH / 24)
  if (diffD === 1) return 'yesterday'
  if (diffD < 7) return `${diffD} d ago`
  return date(d)
}

function duration(seconds: number | null | undefined): string {
  // Format: '47 min', '2 h 15 min', '3 d 5 h'. For uptimes/durations.
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '-'
  if (seconds < 60) return `${Math.round(seconds)} s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const rm = m % 60
  if (h < 24) return `${h} h${rm ? ' ' + rm + ' min' : ''}`
  const dd = Math.floor(h / 24)
  const rh = h % 24
  return `${dd} d${rh ? ' ' + rh + ' h' : ''}`
}

function bytes(b: number | null | undefined): string {
  // Binary format: 'K', 'M', 'G', 'T'. For archive sizes, iface stats.
  if (b === null || b === undefined || !Number.isFinite(b)) return '-'
  // < 1024: integer rounding. Otherwise we'd waver between
  // "868.8415446071904 B" (absurd precision) and "869 B" (readable). We
  // pick readable.
  if (b < 1024) return `${Math.round(b)} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = b / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`
}

export const fmt = { datetime, date, relative, duration, bytes }
