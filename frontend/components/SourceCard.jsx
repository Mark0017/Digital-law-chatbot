import { ExternalLink, FileText } from 'lucide-react'

function getSourceLabel(source) {
  if (source.section) return `${source.section}${source.page ? ` · Page ${source.page}` : ''}`
  if (source.page) return `Page ${source.page}`
  if (!source.url) return 'Uploaded document'

  try {
    const hostname = new URL(source.url).hostname
    if (hostname.endsWith('privacy.gov.ph')) return 'National Privacy Commission'
    if (hostname.endsWith('judiciary.gov.ph')) return 'Supreme Court E-Library'
    return hostname.replace(/^www\./, '')
  } catch {
    return 'Official source'
  }
}

function SourceCard({ source, number }) {
  return (
    <article className="source-card">
      <FileText size={18} aria-hidden="true" />
      <div>
        <strong>[{number}] {source.title || 'Source'}</strong>
        <span>{source.origin === 'knowledge_base' ? 'Knowledge Base Source' : source.origin === 'web' ? 'Web Source' : 'Source'}</span>
        <span>{getSourceLabel(source)}</span>
        {source.publication_date && <span>Published: {source.publication_date}</span>}
        {source.retrieved_at && <span>Retrieved: {source.retrieved_at}</span>}
      </div>
      {source.url && (
        <a href={source.url} target="_blank" rel="noreferrer" aria-label={`Open ${source.title || 'source'}`} title="Open source">
          <ExternalLink size={17} />
        </a>
      )}
    </article>
  )
}

export default SourceCard
