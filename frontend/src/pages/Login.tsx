import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Sparkles } from 'lucide-react'
import GoogleSignInButton from '../components/auth/GoogleSignInButton'
import ThemeToggle from '../components/ThemeToggle'
import { useAuthStore } from '../store/authStore'
import { useThemeStore } from '../store/themeStore'

const HAS_GOOGLE = Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID)
const IS_DEV = import.meta.env.DEV

export default function Login() {
  const loginDev = useAuthStore((s) => s.loginDev)
  const theme = useThemeStore((s) => s.theme)
  const navigate = useNavigate()

  const handleDevLogin = async () => {
    try {
      await loginDev()
      navigate('/chat')
    } catch {
      toast.error('Dev login failed — is the backend running?')
    }
  }

  return (
    <div
      className={`flex h-full items-center justify-center ${
        theme === 'light' ? 'bg-gradient-to-b from-slate-50 to-brand-50' : ''
      }`}
    >
      <div className="clip-panel fui-brackets w-full max-w-sm rounded-2xl border border-line bg-panel p-8 shadow-sm">
        <div className="fui-label mb-3 text-center">clearance &amp; access</div>
        <div className="mb-6 text-center">
          <div className="app-title fui-glow mb-2 inline-flex items-center gap-2 text-accent-strong">
            <Sparkles size={28} />
            <span className="text-2xl font-bold">finzorr.ai</span>
          </div>
          <p className="text-sm text-ink-dim">
            Your AI assistant — general questions, Indian stock markets, your documents.
          </p>
        </div>
        <div className="space-y-3">
          <GoogleSignInButton />
          {!HAS_GOOGLE && !IS_DEV && (
            <p className="text-center text-xs text-ink-faint">Sign-in is not configured yet.</p>
          )}
          {IS_DEV && (
            <button
              onClick={handleDevLogin}
              className="clip-btn w-full rounded-lg border border-line-strong px-4 py-2 text-sm font-medium text-ink-mid hover:bg-surface"
            >
              Continue as Dev User (local only)
            </button>
          )}
        </div>
        <div className="mt-6 flex items-center justify-center">
          <ThemeToggle />
        </div>
        <p className="mt-4 text-center text-[11px] leading-4 text-ink-faint">
          Market data may be delayed. Nothing here is investment advice.
        </p>
        <div className="fui-label mt-3 text-center">unit fzr-001 · read only</div>
      </div>
    </div>
  )
}
