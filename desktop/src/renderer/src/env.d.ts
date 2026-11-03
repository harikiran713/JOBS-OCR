/// <reference types="vite/client" />
import type { MmiBridge } from '../../preload'

declare global {
  interface Window {
    mmi: MmiBridge
  }
}

export {}
