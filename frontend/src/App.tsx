import { useEffect, type ReactElement } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import Chat from './pages/Chat'
import Login from './pages/Login'
import { useAuthStore } from './store/authStore'

function Protected({ children }: { children: ReactElement }) {
  const { user, loading } = useAuthStore()
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-400">
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
