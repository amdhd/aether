import { render, screen } from '@testing-library/react'
import ReactMarkdown from 'react-markdown'
import { describe, expect, it } from 'vitest'
import { MarkdownLink } from './MarkdownLink'

function renderMarkdown(markdown: string) {
  return render(<ReactMarkdown components={{ a: MarkdownLink }}>{markdown}</ReactMarkdown>)
}

describe('MarkdownLink', () => {
  it('reveals where a disguised link really goes', () => {
    renderMarkdown('[your report](https://evil.example/c?notes=secret)')
    const link = screen.getByRole('link', { name: 'your report' })
    expect(link).toHaveAttribute('title', 'https://evil.example/c?notes=secret')
    expect(screen.getByText('(evil.example)')).toBeInTheDocument()
  })

  it('opens in a new tab without opener or referrer', () => {
    renderMarkdown('[docs](https://example.com/docs)')
    const link = screen.getByRole('link', { name: 'docs' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(link.getAttribute('rel')).toContain('noreferrer')
    expect(link).toHaveAttribute('referrerpolicy', 'no-referrer')
  })

  it('does not repeat the host when the text is already the URL', () => {
    renderMarkdown('<https://example.com/a>')
    expect(screen.getByRole('link', { name: 'https://example.com/a' })).toBeInTheDocument()
    expect(screen.queryByText('(example.com)')).not.toBeInTheDocument()
  })

  it.each(['javascript:alert(1)', 'data:text/html,hi', 'vbscript:x', 'relative/path'])(
    'renders %s as plain text, not a link',
    (href) => {
      renderMarkdown(`[click me](${href})`)
      expect(screen.queryByRole('link')).not.toBeInTheDocument()
      expect(screen.getByText('click me')).toBeInTheDocument()
    },
  )
})
