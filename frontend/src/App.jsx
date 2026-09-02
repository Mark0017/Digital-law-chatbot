import { useCallback, useEffect, useState } from 'react'
import AuthPanel from '../components/AuthPanel'
import ChatView from '../components/ChatView'
import Sidebar from '../components/Sidebar'
import { isSupabaseConfigured, supabase } from './lib/supabase'
import './App.css'

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

function App() {
  const [session, setSession] = useState(null)
  const [checkingSession, setCheckingSession] = useState(Boolean(supabase))
  const [conversations, setConversations] = useState([])
  const [currentConversationId, setCurrentConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const loadConversations = useCallback(async (userId) => {
    if (!supabase || !userId) return

    const { data, error: queryError } = await supabase
      .from('conversations')
      .select('id,title,created_at,updated_at')
      .eq('user_id', userId)
      .order('updated_at', { ascending: false })

    if (queryError) {
      setError(queryError.message)
      return
    }

    setConversations(data || [])
    setCurrentConversationId((currentId) => currentId || data?.[0]?.id || null)
  }, [])

  useEffect(() => {
    if (!supabase) return undefined

    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setCheckingSession(false)
    })

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession)
      setCheckingSession(false)
      if (!nextSession) {
        setConversations([])
        setCurrentConversationId(null)
        setMessages([])
      }
    })

    return () => subscription.unsubscribe()
  }, [])

  useEffect(() => {
    if (!session?.user?.id) return undefined

    const task = window.setTimeout(() => loadConversations(session.user.id), 0)
    return () => window.clearTimeout(task)
  }, [loadConversations, session?.user?.id])

  useEffect(() => {
    if (!supabase || !currentConversationId) return undefined

    let active = true

    supabase
      .from('messages')
      .select('id,role,content,sources,created_at')
      .eq('conversation_id', currentConversationId)
      .order('created_at', { ascending: true })
      .then(({ data, error: queryError }) => {
        if (!active) return
        if (queryError) setError(queryError.message)
        setMessages(data || [])
        setLoadingMessages(false)
      })

    return () => {
      active = false
    }
  }, [currentConversationId])

  function startNewConversation() {
    setCurrentConversationId(null)
    setMessages([])
    setLoadingMessages(false)
    setError('')
    setSidebarOpen(false)
  }

  function selectConversation(conversationId) {
    setLoadingMessages(true)
    setCurrentConversationId(conversationId)
    setError('')
    setSidebarOpen(false)
  }

  async function deleteConversation(conversationId) {
    if (!supabase) return

    const { error: deleteError } = await supabase
      .from('conversations')
      .delete()
      .eq('id', conversationId)

    if (deleteError) {
      setError(deleteError.message)
      return
    }

    const remaining = conversations.filter((item) => item.id !== conversationId)
    setConversations(remaining)

    if (currentConversationId === conversationId) {
      setLoadingMessages(Boolean(remaining[0]?.id))
      setCurrentConversationId(remaining[0]?.id || null)
      setMessages([])
    }
  }

  async function sendMessage(content) {
    if (!supabase || !session?.user) return

    setSending(true)
    setError('')
    let conversationId = currentConversationId

    try {
      if (!conversationId) {
        const title = content.length > 72 ? `${content.slice(0, 69)}...` : content
        const { data: conversation, error: conversationError } = await supabase
          .from('conversations')
          .insert({ user_id: session.user.id, title })
          .select('id,title,created_at,updated_at')
          .single()

        if (conversationError) throw conversationError

        conversationId = conversation.id
        setCurrentConversationId(conversationId)
        setConversations((items) => [conversation, ...items])
      }

      const { data: userMessage, error: messageError } = await supabase
        .from('messages')
        .insert({
          conversation_id: conversationId,
          user_id: session.user.id,
          role: 'user',
          content,
        })
        .select('id,role,content,sources,created_at')
        .single()

      if (messageError) throw messageError
      setMessages((items) => [...items, userMessage])

      const response = await fetch(`${apiBaseUrl}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ conversation_id: conversationId, message: content }),
      })

      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || 'The chat service is unavailable.')
      }

      const assistantMessage = {
        id: payload.message_id || crypto.randomUUID(),
        role: 'assistant',
        content: payload.answer,
        sources: payload.sources || [],
        created_at: new Date().toISOString(),
      }

      setMessages((items) => [...items, assistantMessage])
      await loadConversations(session.user.id)
    } catch (sendError) {
      const isConnectionError = sendError instanceof TypeError && sendError.message === 'Failed to fetch'
      setError(
        isConnectionError
          ? `Cannot reach the chat API at ${apiBaseUrl}. Make sure the backend is running.`
          : sendError.message || 'Unable to send your message.',
      )
    } finally {
      setSending(false)
    }
  }

  async function logout() {
    if (supabase) await supabase.auth.signOut()
  }

  if (checkingSession) {
    return (
      <div className="app-loading" role="status">
        <span className="spinner" aria-hidden="true" />
        <p>Preparing your workspace...</p>
      </div>
    )
  }

  if (!session) {
    return <AuthPanel client={supabase} configured={isSupabaseConfigured} />
  }

  return (
    <div className="app-shell">
      <Sidebar
        conversations={conversations}
        currentId={currentConversationId}
        userEmail={session.user.email}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNew={startNewConversation}
        onSelect={selectConversation}
        onDelete={deleteConversation}
        onLogout={logout}
      />
      {sidebarOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          onClick={() => setSidebarOpen(false)}
          aria-label="Close conversations"
        />
      )}
      <ChatView
        messages={messages}
        loadingMessages={loadingMessages}
        sending={sending}
        error={error}
        onOpenSidebar={() => setSidebarOpen(true)}
        onSend={sendMessage}
      />
    </div>
  )
}

export default App
