import { useEffect, useRef, useState } from 'react'
import { BookOpenCheck, Check, Copy, Menu, Send, ShieldCheck } from 'lucide-react'
import SourceCard from './SourceCard'

const suggestions = [
  'What rights do data subjects have under RA 10173?',
  'Can an employer collect employee fingerprints?',
  'What is the latest NPC circular on data privacy?',
]

function ChatView({ messages, loadingMessages, sending, error, onOpenSidebar, onSend }) {
  const [draft, setDraft] = useState('')
  const [copiedId, setCopiedId] = useState(null)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  function submitMessage(event) {
    event.preventDefault()
    const message = draft.trim()
    if (!message || sending) return
    setDraft('')
    onSend(message)
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  async function copyMessage(message) {
    await navigator.clipboard.writeText(message.content)
    setCopiedId(message.id)
    window.setTimeout(() => setCopiedId(null), 1600)
  }

  return (
    <main className="chat-main">
      <header className="chat-header">
        <button type="button" className="icon-button sidebar-open" onClick={onOpenSidebar} aria-label="Open conversations" title="Open conversations">
          <Menu size={21} />
        </button>
        <div>
          <strong>Philippine Data Privacy Assistant</strong>
          <span><i aria-hidden="true" /> Grounded in official sources</span>
        </div>
        <ShieldCheck className="header-shield" size={24} aria-hidden="true" />
      </header>

      <section className="message-scroll" aria-live="polite">
        {loadingMessages ? (
          <div className="center-status"><span className="spinner" aria-hidden="true" /><p>Loading conversation...</p></div>
        ) : messages.length === 0 ? (
          <div className="empty-chat">
            <span className="empty-chat-icon" aria-hidden="true"><BookOpenCheck size={31} /></span>
            <p className="eyebrow">Official-source research</p>
            <h1>What would you like to understand?</h1>
            <p className="empty-chat-copy">Ask about Philippine data privacy, personal data protection, data subject rights, or National Privacy Commission guidance.</p>
            <div className="suggestion-list">
              {suggestions.map((suggestion) => (
                <button type="button" key={suggestion} onClick={() => onSend(suggestion)} disabled={sending}>
                  <span>{suggestion}</span><Send size={15} aria-hidden="true" />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="message-list">
            {messages.map((message) => {
              const isAssistant = message.role === 'assistant'
              const sources = Array.isArray(message.sources) ? message.sources : []
              return (
                <article className={`message ${isAssistant ? 'assistant' : 'user'}`} key={message.id}>
                  <div className="message-meta">
                    <span>{isAssistant ? 'DigitalLaw PH' : 'You'}</span>
                    {isAssistant && (
                      <button type="button" className="icon-button copy-button" onClick={() => copyMessage(message)} aria-label="Copy answer" title="Copy answer">
                        {copiedId === message.id ? <Check size={16} /> : <Copy size={16} />}
                      </button>
                    )}
                  </div>
                  <div className="message-content">{message.content}</div>
                  {sources.length > 0 && (
                    <div className="sources-block">
                      <p>Sources</p>
                      <div className="source-list">
                        {sources.map((source, index) => <SourceCard source={source} number={index + 1} key={`${source.url || source.title}-${index}`} />)}
                      </div>
                    </div>
                  )}
                </article>
              )
            })}
            {sending && <div className="retrieval-status" role="status"><span className="spinner" aria-hidden="true" />Checking privacy documents and supporting sources...</div>}
          </div>
        )}
        <div ref={bottomRef} />
      </section>

      <footer className="composer-region">
        {error && <div className="chat-error" role="alert">{error}</div>}
        <form className="composer" onSubmit={submitMessage}>
          <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={handleKeyDown} placeholder="Ask about Philippine data privacy..." rows={1} maxLength={4000} aria-label="Message" />
          <button type="submit" className="send-button" disabled={!draft.trim() || sending} aria-label="Send message" title="Send message"><Send size={19} /></button>
        </form>
        <p className="composer-disclaimer">General information only. Verify important matters with the relevant agency or a qualified legal professional.</p>
      </footer>
    </main>
  )
}

export default ChatView
