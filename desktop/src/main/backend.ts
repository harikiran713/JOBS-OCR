/**
 * Lifecycle of the local Python processing backend.
 *
 * - Spawned with a random per-launch API token (passed via environment, not argv).
 * - Binds to 127.0.0.1 on an OS-assigned port, announced on stdout as `MMI_READY port=N`.
 * - Receives our PID so it exits if this process dies unexpectedly.
 */
import { ChildProcess, spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync, mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { app } from 'electron'

export type BackendState =
  | { status: 'starting' }
  | { status: 'ready'; url: string; token: string }
  | { status: 'error'; message: string; logHint: string }

type Listener = (state: BackendState) => void

export class Backend {
  private proc: ChildProcess | null = null
  private state: BackendState = { status: 'starting' }
  private listeners = new Set<Listener>()
  private stopping = false
  readonly token = randomBytes(32).toString('hex')
  readonly dataDir: string

  constructor() {
    this.dataDir = process.env.MMI_DATA_DIR || join(app.getPath('userData'), 'data')
    mkdirSync(this.dataDir, { recursive: true })
  }

  getState(): BackendState {
    return this.state
  }

  onChange(fn: Listener): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private set(state: BackendState): void {
    this.state = state
    for (const fn of this.listeners) fn(state)
  }

  /** Resolves the backend command for development (venv) or production (bundled executable). */
  private command(): { cmd: string; args: string[]; cwd: string } {
    if (app.isPackaged) {
      const dir = join(process.resourcesPath, 'backend')
      return { cmd: join(dir, 'mmi-backend.exe'), args: [], cwd: dir }
    }
    const backendDir = resolve(app.getAppPath(), '..', 'backend')
    const venvPython =
      process.platform === 'win32'
        ? join(backendDir, '.venv', 'Scripts', 'python.exe')
        : join(backendDir, '.venv', 'bin', 'python')
    const python = process.env.MMI_PYTHON || (existsSync(venvPython) ? venvPython : 'python')
    return { cmd: python, args: ['-m', 'mmi'], cwd: backendDir }
  }

  start(): void {
    if (this.proc) return
    this.stopping = false
    this.set({ status: 'starting' })
    const { cmd, args, cwd } = this.command()
    const logHint = join(this.dataDir, 'logs', 'backend.log')
    if (!existsSync(cmd) && cmd.includes('\\')) {
      this.set({ status: 'error', message: `Processing engine not found at ${cmd}`, logHint })
      return
    }
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      MMI_API_TOKEN: this.token,
      MMI_DATA_DIR: this.dataDir,
      PYTHONUNBUFFERED: '1',
      PYTHONIOENCODING: 'utf-8'
    }
    delete env.ELECTRON_RUN_AS_NODE
    const child = spawn(cmd, [...args, '--port', '0', '--data-dir', this.dataDir, '--parent-pid', String(process.pid)], {
      cwd,
      env,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    this.proc = child
    let stderrTail = ''
    const timer = setTimeout(() => {
      if (this.state.status === 'starting') {
        this.set({ status: 'error', message: 'The processing engine did not start within 90 seconds.', logHint })
        child.kill()
      }
    }, 90_000)

    child.stdout?.setEncoding('utf8')
    child.stdout?.on('data', (chunk: string) => {
      for (const line of chunk.split(/\r?\n/)) {
        const ready = /^MMI_READY port=(\d+)/.exec(line)
        if (ready) {
          clearTimeout(timer)
          this.set({ status: 'ready', url: `http://127.0.0.1:${ready[1]}`, token: this.token })
        }
        const fatal = /^MMI_FATAL (.*)/.exec(line)
        if (fatal) {
          clearTimeout(timer)
          this.set({ status: 'error', message: fatal[1], logHint })
        }
      }
    })
    child.stderr?.setEncoding('utf8')
    child.stderr?.on('data', (chunk: string) => {
      stderrTail = (stderrTail + chunk).slice(-4000)
    })
    child.on('error', (err) => {
      clearTimeout(timer)
      this.proc = null
      this.set({ status: 'error', message: `Could not start the processing engine: ${err.message}`, logHint })
    })
    child.on('exit', (code) => {
      clearTimeout(timer)
      this.proc = null
      if (this.stopping) return
      const tail = stderrTail.trim().split(/\r?\n/).slice(-3).join(' ')
      this.set({
        status: 'error',
        message: `The processing engine stopped unexpectedly (exit code ${code}). ${tail}`.trim(),
        logHint
      })
    })
  }

  async stop(): Promise<void> {
    this.stopping = true
    const p = this.proc
    if (!p) return
    await new Promise<void>((done) => {
      const t = setTimeout(() => {
        p.kill('SIGKILL')
        done()
      }, 4000)
      p.once('exit', () => {
        clearTimeout(t)
        done()
      })
      p.kill()
    })
    this.proc = null
  }

  async restart(): Promise<void> {
    await this.stop()
    this.start()
  }

  /** Authenticated request from the main process (used for dialog-driven actions). */
  async request<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
    if (this.state.status !== 'ready') throw new Error('Processing engine is not ready')
    const res = await fetch(`${this.state.url}${path}`, {
      method: init.method ?? 'GET',
      headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
      body: init.body === undefined ? undefined : JSON.stringify(init.body)
    })
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown }
    if (!res.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`
      throw new Error(detail)
    }
    return data as T
  }
}
