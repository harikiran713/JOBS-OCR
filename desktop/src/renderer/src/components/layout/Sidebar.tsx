import { useQuery } from '@tanstack/react-query'
import {
  FolderKanban,
  PanelLeftClose,
  PanelLeftOpen,
  Settings
} from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import logo from '@/assets/logo.png'
import { Tooltip } from '@/components/ui/overlay'
import { api } from '@/lib/api'
import { cn, formatBytes } from '@/lib/utils'
import { useApp } from '@/stores/app'

const NAV = [
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/settings', label: 'Settings', icon: Settings }
]

const AI_DOT: Record<string, string> = {
  ready: 'bg-[#3cc68a]',
  degraded: 'bg-[#e3a64a]',
  unavailable: 'bg-[#f2756a]',
  disabled: 'bg-[#5d6b82]'
}

export function Sidebar(): ReactNode {
  const collapsed = useApp((s) => s.sidebarCollapsed)
  const toggle = useApp((s) => s.toggleSidebar)
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system, refetchInterval: 30_000 })

  const aiLabel =
    system?.ai.state === 'disabled'
      ? 'Local AI off'
      : system?.ai.state === 'ready'
        ? `AI ready · ${system.ai.chat_model || system.ai.provider}`
        : system?.ai.state === 'degraded'
          ? 'AI: model missing'
          : system
            ? 'AI unavailable'
            : 'Checking AI…'

  return (
    <aside className={cn('flex h-full shrink-0 flex-col bg-nav text-nav-text transition-[width] duration-150', collapsed ? 'w-[60px]' : 'w-[232px]')}>
      <div className={cn('flex h-12 items-center gap-2.5 border-b border-nav-line', collapsed ? 'justify-center px-0' : 'px-4')}>
        <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-white p-0.5">
          <img src={logo} alt="" className="size-full object-contain" draggable={false} />
        </div>
        {!collapsed ? (
          <div className="min-w-0 leading-tight">
            <div className="truncate text-[13px] font-semibold text-white">Marine Maintenance</div>
            <div className="text-2xs text-nav-muted">Intelligence</div>
          </div>
        ) : null}
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3" aria-label="Main">
        <ul className="flex flex-col gap-0.5">
          {NAV.map((item) => {
            const Icon = item.icon
            const link = (
              <NavLink
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'group flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] transition-colors',
                    isActive ? 'bg-nav-3 text-white' : 'text-nav-text hover:bg-nav-2 hover:text-white',
                    collapsed && 'justify-center px-0'
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon className={cn('size-4 shrink-0', isActive ? 'text-[#5fd0ea]' : 'text-nav-muted group-hover:text-nav-text')} />
                    {!collapsed ? <span className="flex-1 truncate">{item.label}</span> : null}
                  </>
                )}
              </NavLink>
            )
            return <li key={item.to}>{collapsed ? <Tooltip content={item.label} side="right">{link}</Tooltip> : link}</li>
          })}
        </ul>
      </nav>

      <div className="border-t border-nav-line px-2 py-2">
        {!collapsed ? (
          <div className="mb-1 space-y-1.5 px-2.5 py-1.5 text-2xs text-nav-muted">
            <Tooltip content={system?.ai.message} side="right">
              <div className="flex items-center gap-2">
                <span className={cn('size-2 shrink-0 rounded-full', AI_DOT[system?.ai.state ?? 'disabled'])} />
                <span className="truncate">{aiLabel}</span>
              </div>
            </Tooltip>
            <div className="flex items-center justify-between">
              <span>Storage</span>
              <span className="tabular">{system ? formatBytes(system.storage.app_bytes) : '—'}</span>
            </div>
            <div className="flex items-center justify-between">
              <span>Version</span>
              <span className="tabular">{system?.version ?? '—'}</span>
            </div>
          </div>
        ) : (
          <Tooltip content={aiLabel} side="right">
            <div className="flex justify-center py-2">
              <span className={cn('size-2 rounded-full', AI_DOT[system?.ai.state ?? 'disabled'])} />
            </div>
          </Tooltip>
        )}
        <button
          onClick={toggle}
          className={cn('flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-[13px] text-nav-muted hover:bg-nav-2 hover:text-white', collapsed && 'justify-center px-0')}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
          {!collapsed ? 'Collapse' : null}
        </button>
      </div>
    </aside>
  )
}
