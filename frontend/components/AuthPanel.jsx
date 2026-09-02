import { useState } from 'react'
import {
  ArrowRight,
  Eye,
  EyeOff,
  LockKeyhole,
  Scale,
  ShieldCheck,
} from 'lucide-react'

function AuthPanel({ client, configured }) {
  const [mode, setMode] = useState('login')
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const isRegister = mode === 'register'

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setNotice('')

    if (!client) {
      setError('Add your Supabase project URL and publishable key to frontend/.env.')
      return
    }

    setLoading(true)
    try {
      if (isRegister) {
        const { data, error: signUpError } = await client.auth.signUp({
          email: email.trim(),
          password,
          options: { data: { full_name: fullName.trim() } },
        })
        if (signUpError) throw signUpError
        if (!data.session) {
          setNotice('Account created. Check your email before signing in.')
        }
      } else {
        const { error: signInError } = await client.auth.signInWithPassword({
          email: email.trim(),
          password,
        })
        if (signInError) throw signInError
      }
    } catch (authError) {
      setError(authError.message || 'Authentication failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  function changeMode(nextMode) {
    setMode(nextMode)
    setError('')
    setNotice('')
  }

  return (
    <main className="auth-shell">
      <section className="auth-context" aria-labelledby="product-title">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            <ShieldCheck size={25} strokeWidth={1.8} />
          </span>
          <span>DigitalLaw PH</span>
        </div>

        <div className="auth-intro">
          <p className="eyebrow">Philippine legal information</p>
          <h1 id="product-title">Philippine Digital Law AI Assistant</h1>
          <p>
            Research Philippine privacy, cybercrime, electronic-commerce,
            archives, ICT, digital-government, and online child-protection laws.
          </p>
        </div>

        <div className="trust-list" aria-label="Service principles">
          <div>
            <Scale size={20} aria-hidden="true" />
            <span>Grounded in official Philippine legal sources</span>
          </div>
          <div>
            <LockKeyhole size={20} aria-hidden="true" />
            <span>Your conversations are private to your account</span>
          </div>
        </div>

        <p className="legal-note">
          This service is not operated by a Philippine government agency and
          does not provide legal advice.
        </p>
      </section>

      <section className="auth-form-region" aria-labelledby="auth-heading">
        <div className="auth-form-wrap">
          <div className="auth-tabs" role="tablist" aria-label="Account access">
            <button
              type="button"
              role="tab"
              aria-selected={!isRegister}
              className={!isRegister ? 'active' : ''}
              onClick={() => changeMode('login')}
            >
              Sign in
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={isRegister}
              className={isRegister ? 'active' : ''}
              onClick={() => changeMode('register')}
            >
              Create account
            </button>
          </div>

          <div className="auth-form-heading">
            <h2 id="auth-heading">
              {isRegister ? 'Create your account' : 'Welcome back'}
            </h2>
            <p>
              {isRegister
                ? 'Create an account to keep your research history.'
                : 'Sign in to continue your legal research.'}
            </p>
          </div>

          {!configured && (
            <div className="inline-alert warning" role="status">
              Supabase is not configured. Complete the values in frontend/.env.
            </div>
          )}

          <form className="auth-form" onSubmit={handleSubmit}>
            {isRegister && (
              <label>
                <span>Full name</span>
                <input
                  type="text"
                  autoComplete="name"
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  maxLength={120}
                  placeholder="Juan Dela Cruz"
                />
              </label>
            )}

            <label>
              <span>Email address</span>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                placeholder="you@example.com"
              />
            </label>

            <label>
              <span>Password</span>
              <div className="password-field">
                <input
                  type={showPassword ? 'text' : 'password'}
                  autoComplete={isRegister ? 'new-password' : 'current-password'}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  minLength={8}
                  placeholder="At least 8 characters"
                />
                <button
                  type="button"
                  className="icon-button password-toggle"
                  onClick={() => setShowPassword((visible) => !visible)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  title={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </label>

            {error && <div className="inline-alert error" role="alert">{error}</div>}
            {notice && <div className="inline-alert success" role="status">{notice}</div>}

            <button className="primary-button" type="submit" disabled={loading}>
              <span>
                {loading
                  ? 'Please wait...'
                  : isRegister
                    ? 'Create account'
                    : 'Sign in'}
              </span>
              {!loading && <ArrowRight size={18} aria-hidden="true" />}
            </button>
          </form>
        </div>
      </section>
    </main>
  )
}

export default AuthPanel
