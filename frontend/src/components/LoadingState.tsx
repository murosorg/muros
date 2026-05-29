// Indicateur de chargement standard pour MurOS.
//
// Pattern volontairement sobre : pas de spinner clinquant, juste un texte
// 'Loading...' avec un point clignotant discret. L'idee est de signaler
// que ca travaille sans transformer l'UI en sapin de Noel.
//
// Usage table :
//   <tr><td colSpan={5}><LoadingState /></td></tr>
//
// Usage inline :
//   {loading ? <LoadingState variant="inline" /> : <Content />}

type Props = {
  text?: string
  variant?: 'inline' | 'block'
}

export default function LoadingState({ text = 'Loading...', variant = 'block' }: Props) {
  if (variant === 'inline') {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-gray-600">
        <span className="inline-block w-1.5 h-1.5 bg-gray-400 rounded-full animate-pulse" />
        {text}
      </span>
    )
  }
  return (
    <div className="px-3 py-8 text-center">
      <span className="inline-flex items-center gap-2 text-sm text-gray-600">
        <span className="inline-block w-1.5 h-1.5 bg-gray-400 rounded-full animate-pulse" />
        {text}
      </span>
    </div>
  )
}
