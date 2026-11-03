import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AppState {
  currentProjectId: string | null
  sidebarCollapsed: boolean
  jobsListWidth: number
  setJobsListWidth: (w: number) => void
  setProject: (id: string | null) => void
  toggleSidebar: () => void
}

/** UI preferences persisted locally (no document content is stored here). */
export const useApp = create<AppState>()(
  persist(
    (set) => ({
      currentProjectId: null,
      sidebarCollapsed: false,
      jobsListWidth: 380,
      setJobsListWidth: (w) => set({ jobsListWidth: w }),
      setProject: (id) => set({ currentProjectId: id }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed }))
    }),
    { name: 'mmi-ui' }
  )
)
