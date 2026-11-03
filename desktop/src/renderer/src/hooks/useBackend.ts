import { useEffect, useState } from 'react'
import { configureApi } from '@/lib/api'

export type BackendState =
  | { status: 'starting' }
  | { status: 'ready'; url: string; token: string }
  | { status: 'error'; message: string; logHint: string }

/** Tracks the processing engine started by the Electron main process. */
export function useBackend(): { state: BackendState; restart: () => void } {
  const [state, setState] = useState<BackendState>({ status: 'starting' })
  useEffect(() => {
    const apply = (s: BackendState): void => {
      if (s.status === 'ready') configureApi(s.url, s.token)
      setState(s)
    }
    void window.mmi.getBackendState().then(apply)
    return window.mmi.onBackendState(apply)
  }, [])
  return {
    state,
    restart: () => {
      setState({ status: 'starting' })
      void window.mmi.restartBackend().then((s) => {
        if (s.status === 'ready') configureApi(s.url, s.token)
        setState(s)
      })
    }
  }
}
