// Skeleton placeholder pour les tables en cours de chargement. Plus pro
// qu'un texte 'Loading...' : montre des lignes grises animees qui evoquent
// la structure attendue. Animation pulse synchronisee via Tailwind.
//
// Usage typique dans une table :
//   {loading ? <TableSkeleton rows={5} cols={6} /> : data.map(...)}
export default function TableSkeleton({ rows = 5, cols = 6 }: { rows?: number; cols?: number }) {
  // Largeurs heterogenes pour eviter l'effet damier : on cycle sur 4 widths
  // pour donner l'impression d'un vrai contenu varie.
  const widths = ['w-3/4', 'w-1/2', 'w-2/3', 'w-5/6']
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="border-t border-gray-200 animate-pulse">
          {Array.from({ length: cols }).map((_, c) => (
            <td key={c} className="px-3 py-2.5">
              <div className={`h-3 bg-gray-200 rounded ${widths[(r + c) % widths.length]}`} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}
