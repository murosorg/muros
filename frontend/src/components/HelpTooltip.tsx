// Help tooltip for technical concepts (MTU, rp_filter, AllowedIPs...).
//
// Petite icone (?) gris discret a cote du label. Au hover/focus, affiche un
// bulle texte. Volontairement sobre : pas d'animation, fond blanc bordure
// grise, max 280px de large.
//
// Usage :
//   <label>MTU <HelpTooltip text="Maximum Transmission Unit. Defaut 1500. ..." /></label>

import { useState } from 'react'

type Props = {
  text: string
}

export default function HelpTooltip({ text }: Props) {
  const [open, setOpen] = useState(false)
  return (
    <span
      className="relative inline-flex align-baseline"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full bg-gray-200 text-gray-700 text-[10px] font-bold leading-none hover:bg-gray-300 focus:outline-none focus:ring-2 focus:ring-gray-400"
        aria-label="Aide"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => { e.preventDefault(); setOpen((v) => !v) }}
      >
        ?
      </button>
      {open && (
        <span
          className="absolute left-5 top-0 z-30 bg-white border border-gray-300 shadow-md rounded px-3 py-2 text-xs text-gray-800 normal-case font-normal w-72 max-w-[18rem]"
          role="tooltip"
        >
          {text}
        </span>
      )}
    </span>
  )
}
