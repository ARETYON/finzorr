// Google Identity Services button. Renders only when VITE_GOOGLE_CLIENT_ID is
// configured; the Login page shows the dev-bypass button otherwise (dev only).

import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useAuthStore } from '../../store/authStore'

const CLIENT_ID: string = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: object) => void
          renderButton: (el: HTMLElement, options: object) => void
        }
      }
    }
  }
}

export default function GoogleSignInButton() {
  const buttonRef = useRef<HTMLDivElement>(null)
  const loginGoogle = useAuthStore((s) => s.loginGoogle)
  const navigate = useNavigate()

  useEffect(() => {
    if (!CLIENT_ID) return
    const script = document.createElement('script')
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.onload = () => {
      if (!window.google || !buttonRef.current) return
      window.google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: async (response: { credential: string }) => {
          try {
            await loginGoogle(response.credential)
            navigate('/chat')
          } catch {
            toast.error('Google sign-in failed')
          }
        },
      })
      window.google.accounts.id.renderButton(buttonRef.current, {
        theme: 'outline',
        size: 'large',
        width: 280,
      })
    }
    document.head.appendChild(script)
    return () => {
      document.head.removeChild(script)
    }
  }, [loginGoogle, navigate])

  if (!CLIENT_ID) return null
  return <div ref={buttonRef} className="flex justify-center" />
}
