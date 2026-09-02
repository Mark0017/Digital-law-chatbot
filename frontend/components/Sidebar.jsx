import {
  LogOut,
  MessageSquareText,
  PanelLeftClose,
  Plus,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'

function Sidebar({ conversations, currentId, userEmail, open, onClose, onNew, onSelect, onDelete, onLogout }) {
  return (
    <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Conversations">
      <div className="sidebar-top">
        <div className="brand-lockup sidebar-brand">
          <span className="brand-mark" aria-hidden="true">
            <ShieldCheck size={22} strokeWidth={1.8} />
          </span>
          <span>DigitalLaw PH</span>
        </div>
        <button type="button" className="icon-button desktop-collapse" onClick={onClose} aria-label="Close sidebar" title="Close sidebar">
          <PanelLeftClose size={19} />
        </button>
        <button type="button" className="icon-button mobile-close" onClick={onClose} aria-label="Close sidebar" title="Close sidebar">
          <X size={20} />
        </button>
      </div>

      <button type="button" className="new-chat-button" onClick={onNew}>
        <Plus size={18} aria-hidden="true" />
        <span>New conversation</span>
      </button>

      <div className="conversation-section">
        <p className="sidebar-label">Conversations</p>
        <nav className="conversation-list">
          {conversations.length === 0 ? (
            <p className="empty-history">Your saved conversations will appear here.</p>
          ) : (
            conversations.map((conversation) => (
              <div className={`conversation-row ${currentId === conversation.id ? 'active' : ''}`} key={conversation.id}>
                <button type="button" className="conversation-select" onClick={() => onSelect(conversation.id)} title={conversation.title}>
                  <MessageSquareText size={16} aria-hidden="true" />
                  <span>{conversation.title}</span>
                </button>
                <button type="button" className="icon-button conversation-delete" onClick={() => onDelete(conversation.id)} aria-label={`Delete ${conversation.title}`} title="Delete conversation">
                  <Trash2 size={15} />
                </button>
              </div>
            ))
          )}
        </nav>
      </div>

      <div className="sidebar-account">
        <div className="account-avatar" aria-hidden="true">{userEmail?.charAt(0).toUpperCase() || 'U'}</div>
        <div className="account-details">
          <span>Signed in</span>
          <strong title={userEmail}>{userEmail}</strong>
        </div>
        <button type="button" className="icon-button" onClick={onLogout} aria-label="Sign out" title="Sign out">
          <LogOut size={18} />
        </button>
      </div>
    </aside>
  )
}

export default Sidebar
