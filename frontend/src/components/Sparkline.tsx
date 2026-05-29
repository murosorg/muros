type Props = {
  values: number[]
  width?: number
  height?: number
  max?: number
  color?: string
  fill?: boolean
  // When true, render a small "min - max" caption under the curve so a
  // flat-looking line is no longer mistaken for missing data. The caller
  // can pass a `format` function to render unit-aware values (e.g. %, MB).
  showRange?: boolean
  format?: (n: number) => string
}

export default function Sparkline({
  values,
  width = 140,
  height = 36,
  max,
  color = '#111827',
  fill = true,
  showRange = false,
  format,
}: Props) {
  if (values.length < 2) return <div style={{ width, height }} />

  const maxValue = max ?? Math.max(...values, 1)
  const step = width / (values.length - 1)
  const points = values.map((v, i) => {
    const x = i * step
    const y = height - (v / maxValue) * (height - 2) - 1
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  const areaPath = `M0,${height} L${points.split(' ').join(' L')} L${width},${height} Z`

  const svg = (
    <svg width={width} height={height} className="block">
      {fill && <path d={areaPath} fill={color} fillOpacity={0.08} />}
      <polyline points={points} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  )

  if (!showRange) return svg

  const fmt = format ?? ((n: number) => String(Math.round(n)))
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  return (
    <div style={{ width }}>
      {svg}
      <div
        className="flex items-center justify-between text-[9px] font-mono text-gray-500 leading-none mt-0.5 tabular-nums"
        title={`Range over ${values.length} samples`}
      >
        <span>{fmt(lo)}</span>
        <span>{fmt(hi)}</span>
      </div>
    </div>
  )
}
