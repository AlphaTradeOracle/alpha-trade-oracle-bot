import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { DeskDataProvider } from './context/DeskDataContext'
import { AppShell } from './layout/AppShell'
import { ClosedTradesPage } from './pages/ClosedTradesPage'
import { DashboardPage } from './pages/DashboardPage'
import { OpenTradesPage } from './pages/OpenTradesPage'
import { PendingTradesPage } from './pages/PendingTradesPage'

export default function App() {
  return (
    <BrowserRouter>
      <DeskDataProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<DashboardPage />} />
            <Route path="open" element={<OpenTradesPage />} />
            <Route path="pending" element={<PendingTradesPage />} />
            <Route path="closed" element={<ClosedTradesPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </DeskDataProvider>
    </BrowserRouter>
  )
}
