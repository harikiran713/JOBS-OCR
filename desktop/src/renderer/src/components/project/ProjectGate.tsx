import { FolderKanban } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { EmptyState, Spinner } from '@/components/ui/display'
import { useCurrentProject } from '@/hooks/queries'
import type { Project } from '@/lib/types'
import { useApp } from '@/stores/app'

/** Renders children only when a project is selected (and still exists). */
export function ProjectGate({ children }: { children: (project: Project) => ReactNode }): ReactNode {
  const q = useCurrentProject()
  const navigate = useNavigate()
  const setProject = useApp((s) => s.setProject)
  // A remembered project that no longer exists is cleared rather than shown as an error.
  useEffect(() => {
    if (q.isError) setProject(null)
  }, [q.isError, setProject])
  if (!q.projectId || q.isError) {
    return (
      <EmptyState
        className="h-full"
        icon={<FolderKanban />}
        title="No project selected"
        description="Choose a project in the project switcher, or create one for a vessel and import its manuals."
        action={
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => navigate('/projects')}>
              Browse projects
            </Button>
            <Button variant="primary" onClick={() => navigate('/projects?new=1')}>
              New project
            </Button>
          </div>
        }
      />
    )
  }
  if (q.isLoading || !q.data) return <Spinner />
  return children(q.data)
}
