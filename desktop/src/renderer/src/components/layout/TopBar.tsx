import { useQuery } from '@tanstack/react-query'
import { Bell, ChevronDown, ChevronRight, CircleHelp, FolderKanban, Plus, Settings } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/display'
import { Kbd } from '@/components/ui/display'
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Popover, PopoverContent, PopoverTrigger } from '@/components/ui/overlay'
import { api } from '@/lib/api'
import { cn, pct } from '@/lib/utils'
import { useApp } from '@/stores/app'

const TITLES: Record<string, string> = {
  projects: 'Projects',
  documents: 'Manual',
  settings: 'Settings'
}

function Breadcrumbs(): ReactNode {
  const { pathname } = useLocation()
  const parts = pathname.split('/').filter(Boolean)
  const section = parts[0] ?? ''
  const projectId = useApp((st) => st.currentProjectId)
  const crumbs: Array<{ label: string; to?: string }> =
    section === 'documents'
      ? [{ label: 'Project', to: projectId ? `/projects/${projectId}` : '/projects' }]
      : section === 'review'
        ? [{ label: 'Jobs', to: projectId ? `/projects/${projectId}/jobs` : '/projects' }]
      : [{ label: TITLES[section] ?? section, to: parts.length > 1 ? `/${section}` : undefined }]
  if (section === 'projects' && parts[2] === 'jobs') {
    crumbs.push({ label: 'Overview', to: `/projects/${parts[1]}` }, { label: 'Jobs' })
  } else if (parts.length > 1) {
    crumbs.push({ label: section === 'review' ? 'Review workspace' : section === 'documents' ? 'Manual viewer' : section === 'projects' ? 'Overview' : 'Detail' })
  }
  return (
    <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 text-[13px]">
      {crumbs.map((c, i) => (
        <span key={i} className="flex min-w-0 items-center gap-1">
          {i > 0 ? <ChevronRight className="size-3.5 shrink-0 text-subtle" /> : null}
          {c.to ? (
            <Link to={c.to} className="truncate text-muted hover:text-text">
              {c.label}
            </Link>
          ) : (
            <span className="truncate font-medium text-text">{c.label}</span>
          )}
        </span>
      ))}
    </nav>
  )
}

function ProjectSwitcher(): ReactNode {
  const projectId = useApp((s) => s.currentProjectId)
  const setProject = useApp((s) => s.setProject)
  const navigate = useNavigate()
  const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const current = projects?.find((p) => p.id === projectId)
  const active = projects?.filter((p) => p.status === 'active') ?? []
  return (
    <Menu>
      <MenuTrigger asChild>
        <button className="flex h-8 max-w-[260px] items-center gap-2 rounded-md border border-line bg-surface px-2.5 text-[13px] shadow-sm hover:border-line-strong">
          <FolderKanban className="size-4 shrink-0 text-subtle" />
          <span className={cn('truncate', current ? 'font-medium' : 'text-muted')}>{current ? current.name : 'Select project'}</span>
          {current?.vessel_name ? <span className="truncate text-xs text-subtle">· {current.vessel_name}</span> : null}
          <ChevronDown className="size-3.5 shrink-0 text-subtle" />
        </button>
      </MenuTrigger>
      <MenuContent align="start" className="w-72">
        <MenuLabel>Projects</MenuLabel>
        {active.length === 0 ? <div className="px-2 py-1.5 text-xs text-muted">No active projects</div> : null}
        {active.slice(0, 12).map((p) => (
          <MenuItem key={p.id} onSelect={() => setProject(p.id)}>
            <span className={cn('size-1.5 rounded-full', p.id === projectId ? 'bg-accent' : 'bg-transparent')} />
            <span className="flex-1 truncate">{p.name}</span>
            <span className="tabular text-2xs text-subtle">{p.stats.documents} docs</span>
          </MenuItem>
        ))}
        <MenuSeparator />
        <MenuItem onSelect={() => navigate('/projects?new=1')}>
          <Plus /> New project
        </MenuItem>
        <MenuItem onSelect={() => navigate('/projects')}>
          <FolderKanban /> All projects
        </MenuItem>
      </MenuContent>
    </Menu>
  )
}

function ProcessingNotifications(): ReactNode {
  const projectId = useApp((s) => s.currentProjectId)
  const { data: dash } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: 4000 })
  const { data: status } = useQuery({
    queryKey: ['processing-status', projectId],
    queryFn: () => api.processingStatus(projectId!),
    enabled: !!projectId,
    refetchInterval: 3000
  })
  const active = (dash?.running_tasks ?? 0) + (dash?.queued_tasks ?? 0) > 0
  const errors = dash?.recent_errors ?? []
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Processing notifications" className="relative">
          <Bell />
          {active ? <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-accent ring-2 ring-surface" /> : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0">
        <div className="border-b border-line px-3 py-2 text-xs font-semibold">Processing</div>
        <div className="space-y-3 p-3">
          {status && status.state !== 'idle' ? (
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className="font-medium">{status.state === 'paused' ? 'Paused' : status.stage ?? 'Working'}</span>
                <span className="tabular text-muted">
                  {status.pages.done}/{status.pages.total} pages
                </span>
              </div>
              <Progress value={pct(status.pages.done, status.pages.total)} tone={status.state === 'paused' ? 'warn' : 'accent'} />
              {status.running[0]?.message ? <p className="truncate text-2xs text-muted">{status.running[0].message}</p> : null}
            </div>
          ) : (
            <p className="text-xs text-muted">{active ? 'Other projects are processing.' : 'No processing running.'}</p>
          )}
          {errors.length ? (
            <div>
              <div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-subtle">Recent issues</div>
              <ul className="space-y-1">
                {errors.slice(0, 4).map((e) => (
                  <li key={e.id} className="truncate text-xs text-muted" title={e.message}>
                    <span className={e.category === 'application' ? 'text-danger' : 'text-warn'}>●</span> {e.message}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  )
}

function Help(): ReactNode {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Help">
          <CircleHelp />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-96 text-[13px]">
        <h3 className="mb-2 font-semibold">How it works</h3>
        <ol className="list-decimal space-y-1 pl-4 text-muted">
          <li>Create a project and import the ship's manuals (PDF).</li>
          <li>Start processing. Every page gets an explicit status; scanned pages are OCR'd locally.</li>
          <li>Jobs and intervals are read from maintenance schedules; procedures are located across all manuals.</li>
          <li>Review: confirm procedures, correct fields and approve jobs. Source pages are always one click away.</li>
          <li>Export approved jobs to Excel using a column template for your maintenance system.</li>
        </ol>
        <h3 className="mb-1 mt-3 font-semibold">Review shortcuts</h3>
        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted">
          <Kbd>A</Kbd> <span>Approve job</span>
          <Kbd>J / K</Kbd> <span>Next / previous job in queue</span>
          <Kbd>Ctrl + F</Kbd> <span>Search in the source document</span>
        </div>
        <p className="mt-3 text-xs text-subtle">All processing runs on this computer. Documents are never uploaded.</p>
      </PopoverContent>
    </Popover>
  )
}

export function TopBar(): ReactNode {
  const navigate = useNavigate()
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
      <ProjectSwitcher />
      <div className="h-5 w-px bg-line" />
      <Breadcrumbs />
      <div className="flex-1" />
      <ProcessingNotifications />
      <Help />
      <Button variant="ghost" size="icon" aria-label="Settings" onClick={() => navigate('/settings')}>
        <Settings />
      </Button>
    </header>
  )
}
