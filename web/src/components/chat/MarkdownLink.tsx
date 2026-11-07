import type { ComponentPropsWithoutRef } from 'react'

const ALLOWED_PROTOCOLS = new Set(['http:', 'https:', 'mailto:'])

function parse(href: string | undefined): URL | null {
  if (!href) return null
  try {
    const url = new URL(href)
    return ALLOWED_PROTOCOLS.has(url.protocol) ? url : null
  } catch {
    return null
  }
}

/**
 * Links in assistant replies.
 *
 * The model writes these, and text it read from a web page or a calendar invite
 * can steer what it writes — including a link whose URL carries private data in
 * its query string. Clicking is still the user's decision, so the link has to
 * say where it goes: the full URL on hover, and the destination host next to any
 * link whose text is not the URL itself. It opens in a new tab with no opener
 * and no Referer, so the destination learns nothing about this page.
 */
// Only href and children are used: every other attribute is set here, never
// taken from the rendered markdown.
export function MarkdownLink({ href, children }: ComponentPropsWithoutRef<'a'>) {
  const url = parse(href)
  if (!url) return <span>{children}</span>

  const destination = url.protocol === 'mailto:' ? url.pathname : url.host
  const text = typeof children === 'string' ? children : null
  const showsItsOwnUrl = text !== null && (text === href || text === destination)

  return (
    <>
      <a
        href={url.href}
        title={url.href}
        target="_blank"
        rel="noopener noreferrer nofollow"
        referrerPolicy="no-referrer"
      >
        {children}
      </a>
      {!showsItsOwnUrl && <span className="ml-1 text-xs text-muted-foreground">({destination})</span>}
    </>
  )
}
