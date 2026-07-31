import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './layout/AppShell'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { ClosedTradesPage } from './pages/ClosedTradesPage'
import { DashboardPage } from './pages/DashboardPage'
import { OpenTradesPage } from './pages/OpenTradesPage'
import { PendingTradesPage } from './pages/PendingTradesPage'
import { SettingsPage } from './pages/SettingsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="open" element={<OpenTradesPage />} />
          <Route path="pending" element={<PendingTradesPage />} />
          <Route path="closed" element={<ClosedTradesPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
