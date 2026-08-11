import { useEffect, type ReactElement } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import { Toaster } from 'react-hot-toast'
import '@fontsource/rajdhani/500.css'
import '@fontsource/rajdhani/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/700.css'
import Chat from './pages/Chat'
import Login from './pages/Login'
import Share from './pages/Share'
import { useAuthStore } from './store/authStore'
import './store/themeStore' // applies persisted theme before first paint

function Protected({ children }: { children: ReactElement }) {
  const { user, loading } = useAuthStore()
  if (loading) {
    return (
      <div className="flex h-dvh items-center justify-center text-sm text-ink-faint">
        Loading…
      </div>
    )
  }
  return user ? children : <Navigate to="/login" replace />
}

export default function App() {
  const load = useAuthStore((s) => s.load)
  useEffect(() => {
    void load()
  }, [load])

  return (
    <BrowserRouter>
      <Toaster position="top-right" />
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/login" element={<Login />} />
        <Route path="/share/:token" element={<Share />} />
        <Route
          path="/chat"
          element={
            <Protected>
              <Chat />
            </Protected>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
