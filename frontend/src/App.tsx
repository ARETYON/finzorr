import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'

// Placeholder routes — Login/Chat pages land in the frontend chat-core step.
function Placeholder({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <h1 className="text-2xl font-semibold text-brand-600">finzorr.ai</h1>
        <p className="mt-2 text-sm text-slate-500">{label}</p>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Toaster position="top-right" />
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/login" element={<Placeholder label="Login — coming next" />} />
        <Route path="/chat" element={<Placeholder label="Chat — coming next" />} />
      </Routes>
    </BrowserRouter>
  )
}
