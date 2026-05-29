// Helpers de formatage centralises pour MurOS.
//
// Objectif : ne plus avoir 5 facons differentes d'afficher une date dans
// l'app (toLocaleString natif vs ISO brut vs formate a la main). Une seule
// source de verite, on remplace les call-sites au fur et a mesure.
//
// Format choisi : DD/MM/YYYY HH:mm en datetime, sans secondes pour rester
// lisible. Relatif court ("il y a 3 min", "hier", "2 j") pour le sentiment
// d'immediatete sur les flux d'evenements (logs, sync, derniere MAJ).

function pad(n: number): string { return n < 10 ? '0' + n : String(n) }

function parse(input: string | number | Date | null | undefined): Date | null {
  if (input === null || input === undefined || input === '') return null
  const d = input instanceof Date ? input : new Date(input)
  return Number.isFinite(d.getTime()) ? d : null
}

function datetime(input: string | number | Date | null | undefined): string {
  // Format : YYYY-MM-DD HH:mm (ISO 8601 court, neutre en langue, standard
  // dans l'OSS / les outils tech). Vide -> '-'.
  const d = parse(input)
  if (!d) return '-'
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

function date(input: string | number | Date | null | undefined): string {
  // Format : YYYY-MM-DD (ISO 8601). Vide -> '-'.
  const d = parse(input)
  if (!d) return '-'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function time(input: string | number | Date | null | undefined): string {
  // Format : HH:mm:ss. Vide -> '-'.
  const d = parse(input)
  if (!d) return '-'
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function relative(input: string | number | Date | null | undefined): string {
  // Format relatif court anglais : '3 min ago', 'yesterday', '2 d ago', ou
  // la date si plus de 7 jours. Vide -> '-'.
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
  // Format : '47 min', '2 h 15 min', '3 j 5 h'. Pour les uptime/durations.
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '-'
  if (seconds < 60) return `${Math.round(seconds)} s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const rm = m % 60
  if (h < 24) return `${h} h${rm ? ' ' + rm + ' min' : ''}`
  const dd = Math.floor(h / 24)
  const rh = h % 24
  return `${dd} j${rh ? ' ' + rh + ' h' : ''}`
}

function bytes(b: number | null | undefined): string {
  // Format binaire : 'K', 'M', 'G', 'T'. Pour les tailles d'archives, stats iface.
  if (b === null || b === undefined || !Number.isFinite(b)) return '-'
  // < 1024 : arrondi entier. Sinon on hesite entre "868.8415446071904 B"
  // (precision absurde) et "869 B" (lisible). On choisit lisible.
  if (b < 1024) return `${Math.round(b)} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = b / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`
}

export const fmt = { datetime, date, time, relative, duration, bytes }
