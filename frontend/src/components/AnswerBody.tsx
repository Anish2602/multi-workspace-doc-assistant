import { useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Citation } from '../api'

function where(c: Citation): string {
  return [c.page ? `p. ${c.page}` : null, c.section].filter(Boolean).join(' · ')
}

/** Markdown answer with inline [n] citations turned into clickable chips. */
export default function AnswerBody({ content, citations }: { content: string; citations: Citation[] }) {
  const [open, setOpen] = useState<number | null>(null)
  const byN = new Map(citations.map((c) => [c.n, c]))
  // [n] -> markdown link to a fragment we intercept below.
  const linked = content.replace(/\[(\d+)\]/g, (m, n) => (byN.has(Number(n)) ? `[${n}](#cite-${n})` : m))
  const shown = open != null ? byN.get(open) : undefined

  return (
    <div>
      <div className="prose-sm leading-relaxed text-slate-800 [&_li]:ml-4 [&_ol]:list-decimal [&_p+p]:mt-2 [&_strong]:font-semibold [&_ul]:list-disc">
        <Markdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              const n = href?.startsWith('#cite-') ? Number(href.slice(6)) : null
              if (n != null) {
                return (
                  <button
                    type="button"
                    onClick={() => setOpen(open === n ? null : n)}
                    title={byN.get(n)?.filename}
                    className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-indigo-100 px-1 align-text-top text-[10px] font-semibold text-indigo-700 hover:bg-indigo-200"
                  >
                    {children}
                  </button>
                )
              }
              return (
                <a href={href} target="_blank" rel="noreferrer noopener" className="text-indigo-600 underline">
                  {children}
                </a>
              )
            },
          }}
        >
          {linked}
        </Markdown>
      </div>

      {shown && (
        <div className="mt-2 rounded-lg border border-indigo-100 bg-indigo-50/60 p-3 text-xs">
          <div className="font-medium text-indigo-800">
            [{shown.n}] {shown.filename} {where(shown) && <span className="font-normal">· {where(shown)}</span>}
          </div>
          <p className="mt-1 whitespace-pre-wrap text-slate-600">{shown.snippet}…</p>
        </div>
      )}

      {citations.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-100 pt-2">
          {citations.map((c) => (
            <button
              key={c.n}
              type="button"
              onClick={() => setOpen(open === c.n ? null : c.n)}
              className={`rounded-md border px-2 py-0.5 text-xs ${
                open === c.n
                  ? 'border-indigo-300 bg-indigo-50 text-indigo-800'
                  : 'border-slate-200 text-slate-600 hover:bg-slate-50'
              }`}
            >
              [{c.n}] {c.filename}
              {where(c) && <span className="text-slate-400"> · {where(c)}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
