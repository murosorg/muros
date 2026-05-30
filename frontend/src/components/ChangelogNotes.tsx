import { type ReactNode } from 'react'

// Inline renderer for backtick `code` spans inside a changelog line. We
// keep it intentionally tiny: the changelog is our own text, not arbitrary
// markdown, so we only need code spans on top of plain text.
function renderInline(text: string, keyBase: string) {
  return text.split(/(`[^`]+`)/g).map((part, i) => {
    if (part.length > 1 && part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={`${keyBase}-${i}`} className="font-mono text-[11px] bg-gray-100 rounded px-1 py-0.5">
          {part.slice(1, -1)}
        </code>
      )
    }
    return <span key={`${keyBase}-${i}`}>{part}</span>
  })
}

// Minimal Keep a Changelog renderer: turns "### Heading" into a small
// uppercase label and "- item" bullets (with wrapped continuation lines)
// into a list. Avoids pulling in a full markdown dependency for the few
// constructs our changelog actually uses.
export default function ChangelogNotes({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let bullets: string[] = []
  let key = 0

  const flush = () => {
    if (bullets.length === 0) return
    const items = bullets
    const k = key++
    blocks.push(
      <ul key={`ul-${k}`} className="list-disc pl-5 space-y-1 text-gray-700">
        {items.map((b, i) => (
          <li key={i}>{renderInline(b, `li-${k}-${i}`)}</li>
        ))}
      </ul>,
    )
    bullets = []
  }

  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    if (/^#{2,4}\s+/.test(line)) {
      flush()
      blocks.push(
        <div key={`h-${key++}`} className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold mt-2 first:mt-0">
          {line.replace(/^#{2,4}\s+/, '')}
        </div>,
      )
    } else if (/^[-*]\s+/.test(line)) {
      bullets.push(line.replace(/^[-*]\s+/, ''))
    } else if (line.trim() === '') {
      // Blank lines separate blocks in Keep a Changelog; nothing to emit.
    } else if (bullets.length > 0) {
      // Wrapped continuation of the previous bullet (indented line).
      bullets[bullets.length - 1] += ' ' + line.trim()
    } else {
      flush()
      blocks.push(
        <p key={`p-${key++}`} className="text-gray-700">
          {renderInline(line.trim(), `p-${key}`)}
        </p>,
      )
    }
  }
  flush()

  return <div className="space-y-1 text-xs">{blocks}</div>
}
