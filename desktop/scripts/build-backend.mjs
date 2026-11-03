// Freezes the Python backend with PyInstaller into backend/dist/mmi-backend (bundled by electron-builder).
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'

const backend = resolve(import.meta.dirname, '..', '..', 'backend')
const py = join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
if (!existsSync(py)) {
  console.error(`Backend virtual environment not found at ${py}. See docs/DEVELOPMENT.md.`)
  process.exit(1)
}
const r = spawnSync(py, ['-m', 'PyInstaller', 'mmi-backend.spec', '--noconfirm', '--log-level', 'WARN'], { cwd: backend, stdio: 'inherit' })
process.exit(r.status ?? 1)
