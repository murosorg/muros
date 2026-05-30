type Point = { x: number; y: number }  // x en ms timestamp, y en valeur

type Props = {
  label: string
  series: { name: string; color: string; points: Point[] }[]
  yMax?: number
  yFormat?: (v: number) => string
  height?: number
  headerExtra?: React.ReactNode
  // When set, the x axis is pinned to a fixed time window ending now
  // ([now - xSpanMs, now]) instead of being derived from the data
  // extent. Keeps a live chart from horizontally rescaling on every
  // sample and lets short windows render second-level tick labels.
  xSpanMs?: number
}

export default function TimeChart({
  label,
  series,
  yMax,
  yFormat = (v) => v.toFixed(0),
  height = 160,
  headerExtra,
  xSpanMs,
}: Props) {
  const allPoints = series.flatMap((s) => s.points)
  if (allPoints.length === 0) {
    return (
      <div className="border border-gray-200 bg-white rounded p-3">
        <div className="text-[10px] uppercase tracking-wider text-gray-700 mb-2">{label}</div>
        <div className="h-32 flex items-center justify-center text-xs text-gray-700">No data</div>
      </div>
    )
  }

  const width = 640  // logique, SVG est responsive via viewBox
  const padding = { top: 8, right: 12, bottom: 18, left: 40 }
  const innerW = width - padding.left - padding.right
  const innerH = height - padding.top - padding.bottom

  const xMax = xSpanMs ? Date.now() : Math.max(...allPoints.map((p) => p.x))
  const xMin = xSpanMs ? xMax - xSpanMs : Math.min(...allPoints.map((p) => p.x))
  const yObservedMax = Math.max(...allPoints.map((p) => p.y), 1)
  const yLimit = yMax ?? yObservedMax

  const sx = (x: number) => padding.left + (xMax === xMin ? 0 : ((x - xMin) / (xMax - xMin)) * innerW)
  const sy = (y: number) => padding.top + innerH - (y / yLimit) * innerH

  // Graduations Y
  const yTicks = [0, 0.5, 1].map((p) => p * yLimit)

  // Graduations X (3 reperes)
  const xTicks = [0, 0.5, 1].map((p) => xMin + p * (xMax - xMin))
  // For sub-hour windows the minute alone repeats across ticks, so we
  // show minutes:seconds. Longer spans keep the hour:minute format.
  const shortSpan = (xSpanMs ?? xMax - xMin) <= 30 * 60_000
  const formatTime = (ts: number) => {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-US', shortSpan
      ? { minute: '2-digit', second: '2-digit', hour12: false }
      : { hour: '2-digit', minute: '2-digit', hour12: false })
  }

  return (
    <div className="border border-gray-200 bg-white rounded p-3">
      <div className="flex items-center justify-between mb-2 gap-3">
        <div className="text-[10px] uppercase tracking-wider text-gray-700">{label}</div>
        <div className="flex items-center gap-3 text-[11px]">
          {series.map((s) => (
            <div key={s.name} className="flex items-center gap-1.5 text-gray-800">
              <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: s.color }} />
              {s.name}
            </div>
          ))}
          {headerExtra}
        </div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" preserveAspectRatio="none">
        {/* Grille Y */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={padding.left} x2={width - padding.right} y1={sy(t)} y2={sy(t)} stroke="#e5e7eb" strokeWidth={1} />
            <text x={padding.left - 6} y={sy(t) + 3} textAnchor="end" fontSize="9" fill="#6b7280" fontFamily="ui-monospace, monospace">
              {yFormat(t)}
            </text>
          </g>
        ))}
        {/* Axe X */}
        {xTicks.map((t, i) => (
          <text key={i} x={sx(t)} y={height - 4} textAnchor="middle" fontSize="9" fill="#6b7280" fontFamily="ui-monospace, monospace">
            {formatTime(t)}
          </text>
        ))}
        {/* Series */}
        {series.map((s) => {
          if (s.points.length < 2) return null
          const path = s.points
            .map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`)
            .join(' ')
          return (
            <g key={s.name}>
              <path d={path} fill="none" stroke={s.color} strokeWidth={1.5} strokeLinejoin="round" />
            </g>
          )
        })}
      </svg>
    </div>
  )
}
