import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter'
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash'
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css'
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript'
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json'
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx'
import markdown from 'react-syntax-highlighter/dist/esm/languages/prism/markdown'
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import tsx from 'react-syntax-highlighter/dist/esm/languages/prism/tsx'
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript'
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml'
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import remarkGfm from 'remark-gfm'
import { CopyButton } from '../ui/Disclosure'
import { useStore } from '../../store/useStore'

// Register only the languages this app realistically renders, so the bundle
// stays a fraction of the full Prism build.
SyntaxHighlighter.registerLanguage('bash', bash)
SyntaxHighlighter.registerLanguage('shell', bash)
SyntaxHighlighter.registerLanguage('css', css)
SyntaxHighlighter.registerLanguage('javascript', javascript)
SyntaxHighlighter.registerLanguage('js', javascript)
SyntaxHighlighter.registerLanguage('json', json)
SyntaxHighlighter.registerLanguage('jsx', jsx)
SyntaxHighlighter.registerLanguage('markdown', markdown)
SyntaxHighlighter.registerLanguage('python', python)
SyntaxHighlighter.registerLanguage('py', python)
SyntaxHighlighter.registerLanguage('sql', sql)
SyntaxHighlighter.registerLanguage('tsx', tsx)
SyntaxHighlighter.registerLanguage('typescript', typescript)
SyntaxHighlighter.registerLanguage('ts', typescript)
SyntaxHighlighter.registerLanguage('yaml', yaml)

interface Props {
  content: string
  compact?: boolean
}

/**
 * Markdown renderer for model output.
 *
 * Answers are untrusted input: raw HTML is never enabled, and links open in a
 * new tab with `noopener`.
 */
export const Markdown = memo(function Markdown({ content, compact = false }: Props) {
  const theme = useStore((state) => state.theme)
  const codeStyle = theme === 'dark' ? oneDark : oneLight

  return (
    <div className={compact ? 'markdown markdown-compact' : 'markdown'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
          pre: ({ children }) => <>{children}</>,
          code({ className, children, ...props }) {
            const text = String(children ?? '').replace(/\n$/, '')
            const match = /language-([\w-]+)/.exec(className ?? '')
            const isBlock = Boolean(match) || text.includes('\n')

            if (!isBlock) {
              return (
                <code className={className} {...props}>
                  {children}
                </code>
              )
            }

            return (
              <div className="group relative my-3 overflow-hidden rounded-card border border-hairline">
                <div className="flex items-center justify-between border-b border-hairline bg-sunken px-2.5 py-1">
                  <span className="font-mono text-2xs uppercase tracking-wide text-ink-faint">
                    {match?.[1] ?? 'text'}
                  </span>
                  <CopyButton text={text} label="Copy code" />
                </div>
                <SyntaxHighlighter
                  language={match?.[1] ?? 'text'}
                  style={codeStyle}
                  customStyle={{
                    margin: 0,
                    padding: '0.75rem',
                    background: 'transparent',
                    fontSize: '0.8125rem',
                    lineHeight: 1.6,
                  }}
                  codeTagProps={{ style: { fontFamily: 'inherit' } }}
                  wrapLongLines={false}
                >
                  {text}
                </SyntaxHighlighter>
              </div>
            )
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
})
