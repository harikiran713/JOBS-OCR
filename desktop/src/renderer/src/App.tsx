import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Loader2, RotateCw, TriangleAlert } from 'lucide-react'
import logo from '@/assets/logo.png'
import { useEffect, type ReactNode } from 'react'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Sidebar } from '@/components/layout/Sidebar'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import { TooltipProvider } from '@/components/ui/overlay'
import { useBackend } from '@/hooks/useBackend'
import { useSettings } from '@/hooks/queries'
import { DocumentViewerPage } from '@/pages/DocumentViewer'
import { JobsPage } from '@/pages/Jobs'
import { ProjectOverviewPage } from '@/pages/ProjectOverview'
import { ProjectsPage } from '@/pages/Projects'
import { ReviewWorkspacePage } from '@/pages/ReviewWorkspace'
import { SettingsPage } from '@/pages/Settings'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 5_000 },
    mutations: { retry: 0 }
  }
})

function ThemeSync(): null {
  const { data } = useSettings()
  const theme = data?.['ui.theme'] ?? 'system'
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = (): void => {
      const dark = theme === 'dark' || (theme === 'system' && mq.matches)
      document.documentElement.classList.toggle('dark', dark)
    }
    apply()
    void window.mmi.setTheme(theme)
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [theme])
  return null
}

function Shell(): ReactNode {
  return (
    <div className="flex h-full">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="min-h-0 flex-1 overflow-hidden">
          <Routes>
            <Route path="/" element={<Navigate to="/projects" replace />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/:projectId" element={<ProjectOverviewPage />} />
            <Route path="/projects/:projectId/jobs" element={<JobsPage />} />
            <Route path="/documents/:documentId" element={<DocumentViewerPage />} />
            <Route path="/review/:jobId" element={<ReviewWorkspacePage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/projects" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

function Splash({ children }: { children: ReactNode }): ReactNode {
  return (
    <div className="flex h-full items-center justify-center bg-bg">
      <div className="w-[440px] rounded-lg border border-line bg-surface p-8 text-center shadow-md">
        <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-lg border border-line bg-white p-1">
          <img src={logo} alt="" className="size-full object-contain" draggable={false} />
        </div>
        <h1 className="text-[16px] font-semibold">Marine Maintenance Intelligence</h1>
        {children}
      </div>
    </div>
  )
}

export function App(): ReactNode {
  const { state, restart } = useBackend()
  if (state.status === 'starting') {
    return (
      <Splash>
        <p className="mt-3 flex items-center justify-center gap-2 text-[13px] text-muted">
          <Loader2 className="size-4 animate-spin" /> Starting the local processing engine…
        </p>
      </Splash>
    )
  }
  if (state.status === 'error') {
    return (
      <Splash>
        <div className="mt-4 rounded-md border border-danger/25 bg-danger-soft p-3 text-left text-[13px] text-danger">
          <div className="flex items-center gap-2 font-medium">
            <TriangleAlert className="size-4" /> The processing engine is not running
          </div>
          <p className="mt-1 break-words opacity-90">{state.message}</p>
        </div>
        <p className="mt-3 text-xs text-muted">Your projects and data are safe. Details are written to the application log.</p>
        <div className="mt-4 flex justify-center gap-2">
          <Button variant="secondary" onClick={() => void window.mmi.openLogs()}>
            Open logs
          </Button>
          <Button variant="primary" onClick={restart}>
            <RotateCw /> Restart engine
          </Button>
        </div>
      </Splash>
    )
  }
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <HashRouter>
          <ThemeSync />
          <Shell />
        </HashRouter>
        <Toaster position="bottom-right" toastOptions={{ className: 'text-[13px]' }} closeButton />
      </TooltipProvider>
    </QueryClientProvider>
  )
}
