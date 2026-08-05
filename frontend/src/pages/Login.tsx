import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Sparkles } from 'lucide-react'
import GoogleSignInButton from '../components/auth/GoogleSignInButton'
import { useAuthStore } from '../store/authStore'

const HAS_GOOGLE = Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID)
const IS_DEV = import.meta.env.DEV

export default function Login() {
  const loginDev = useAuthStore((s) => s.loginDev)
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
    <div className="flex h-full items-center justify-center bg-gradient-to-b from-slate-50 to-brand-50">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-6 text-center">
          <div className="mb-2 inline-flex items-center gap-2 text-brand-600">
            <Sparkles size={28} />
            <span className="text-2xl font-bold">finzorr.ai</span>
          </div>
          <p className="text-sm text-slate-500">
            Your AI assistant — general questions, Indian stock markets, your documents.
          </p>
        </div>
        <div className="space-y-3">
          <GoogleSignInButton />
          {!HAS_GOOGLE && !IS_DEV && (
            <p className="text-center text-xs text-slate-400">
              Sign-in is not configured yet.
            </p>
          )}
          {IS_DEV && (
            <button
              onClick={handleDevLogin}
              className="w-full rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
            >
              Continue as Dev User (local only)
            </button>
          )}
        </div>
        <p className="mt-6 text-center text-[11px] leading-4 text-slate-400">
          Market data may be delayed. Nothing here is investment advice.
        </p>
      </div>
    </div>
  )
}
