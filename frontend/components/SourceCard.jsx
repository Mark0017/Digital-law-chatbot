import { ExternalLink, FileText } from 'lucide-react'

function getSourceLabel(source) {
  if (source.section) return source.section
  if (source.page) return `Page ${source.page}`
  if (!source.url) return 'Official source'

  try {
    const hostname = new URL(source.url).hostname
    if (hostname.endsWith('privacy.gov.ph')) return 'National Privacy Commission'
    if (hostname.endsWith('judiciary.gov.ph')) return 'Supreme Court E-Library'
    return hostname.replace(/^www\./, '')
  } catch {
    return 'Official source'
  }
}

function SourceCard({ source }) {
  return (
    <article className="source-card">
      <FileText size={18} aria-hidden="true" />
      <div>
        <strong>{source.title || 'Official source'}</strong>
        <span>{getSourceLabel(source)}</span>
      </div>
      {source.url && (
        <a href={source.url} target="_blank" rel="noreferrer" aria-label={`Open ${source.title || 'source'}`} title="Open official source">
          <ExternalLink size={17} />
        </a>
      )}
    </article>
  )
}

export default SourceCard
