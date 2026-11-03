import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cpu, Database, Download, FolderOpen, Plus, ScanText, Shield, SlidersHorizontal, Sparkles, Trash2, User } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge, ErrorState, PageHeader, Panel, Progress, Spinner } from '@/components/ui/display'
import { Input, NativeSelect, Switch } from '@/components/ui/form'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/overlay'
import { api } from '@/lib/api'
import type { Settings } from '@/lib/types'
import { formatBytes, pct } from '@/lib/utils'

type Draft = Partial<Settings>

function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }): ReactNode {
  return (
    <div className="grid grid-cols-[280px_1fr] items-start gap-6 border-b border-line py-3.5 last:border-0">
      <div>
        <div className="text-[13px] font-medium">{label}</div>
        {hint ? <div className="mt-0.5 text-xs text-muted">{hint}</div> : null}
      </div>
      <div className="max-w-md">{children}</div>
    </div>
  )
}

function NumberInput({ value, onChange, min, max, step = 1 }: { value: number; onChange: (n: number) => void; min: number; max: number; step?: number }): ReactNode {
  return (
    <Input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={(e) => {
        const n = Number(e.target.value)
        if (!Number.isNaN(n)) onChange(Math.max(min, Math.min(max, n)))
      }}
      className="w-32"
    />
  )
}

function AiTab({ s, set }: { s: Settings; set: (k: keyof Settings, v: unknown) => void }): ReactNode {
  const hw = useQuery({ queryKey: ['hardware'], queryFn: api.hardware, staleTime: Infinity })
  const models = useQuery({ queryKey: ['ai-models', s['ai.provider'], s['ai.base_url']], queryFn: api.aiModels, enabled: s['ai.provider'] !== 'none', retry: false })
  const pulls = useQuery({ queryKey: ['ai-pulls'], queryFn: api.aiPullStatus, refetchInterval: (q) => (q.state.data?.some((p) => p.status === 'running') ? 1000 : false) })
  const qc = useQueryClient()
  const test = useMutation({ mutationFn: api.aiTest, onSuccess: (r) => toast.success(`AI test: ${Object.entries(r).map(([k, v]) => `${k} ${v}`).join(', ') || 'no models configured'}`), onError: (e: Error) => toast.error(e.message) })
  const pull = useMutation({
    mutationFn: api.aiPull,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['ai-pulls'] }),
    onError: (e: Error) => toast.error(e.message)
  })
  const installed = new Set((models.data?.models ?? []).map((m) => m.name))
  const isInstalled = (n: string): boolean => installed.has(n) || installed.has(`${n}:latest`)

  return (
    <div className="space-y-5">
      <Panel title="Inference runtime">
        <p className="mb-2 text-xs text-muted">
          Local AI is optional. Long descriptions are always copied word for word from the manual. For short descriptions a model may only remove words from the copied points — every edit is checked, and any point that fails keeps the manual's wording. A model can also help find jobs (it must quote the page) and check procedure candidates.
        </p>
        <Row label="Provider" hint="Ollama, or any OpenAI-compatible local server such as llama.cpp's llama-server or LM Studio.">
          <NativeSelect value={s['ai.provider']} onChange={(e) => set('ai.provider', e.target.value)} className="w-full">
            <option value="none">Off (rule-based only)</option>
            <option value="ollama">Ollama</option>
            <option value="openai_compatible">OpenAI-compatible server (llama.cpp, LM Studio…)</option>
          </NativeSelect>
        </Row>
        {s['ai.provider'] !== 'none' ? (
          <>
            <Row label="Server URL" hint={s['ai.provider'] === 'ollama' ? 'Default: http://127.0.0.1:11434' : 'e.g. http://127.0.0.1:8080 for llama-server'}>
              <Input value={s['ai.base_url']} onChange={(e) => set('ai.base_url', e.target.value)} />
            </Row>
            <Row label="Chat / extraction model">
              <Input value={s['ai.chat_model']} onChange={(e) => set('ai.chat_model', e.target.value)} placeholder={hw.data?.suggested.chat ?? 'model name'} list="mmi-models" />
            </Row>
            <Row label="Embedding model" hint="Enables semantic procedure search (stage 3).">
              <Input value={s['ai.embedding_model']} onChange={(e) => set('ai.embedding_model', e.target.value)} placeholder={hw.data?.suggested.embedding ?? 'model name'} list="mmi-models" />
            </Row>
            <Row label="Vision model" hint="Optional, for image-heavy pages.">
              <Input value={s['ai.vision_model']} onChange={(e) => set('ai.vision_model', e.target.value)} placeholder={hw.data?.suggested.vision ?? 'model name'} list="mmi-models" />
            </Row>
            <datalist id="mmi-models">
              {models.data?.models.map((m) => (
                <option key={m.name} value={m.name} />
              ))}
            </datalist>
            <Row label="Use the model for">
              <div className="space-y-2">
                {(
                  [
                    ['ai.use_for_job_extraction', 'Finding jobs the rules missed (results must quote the page)'],
                    ['ai.use_for_procedure_verification', 'Verifying procedure candidates'],
                    ['ai.use_for_short_descriptions', 'Tidying short descriptions (may only remove words; checked against the manual)']
                  ] as const
                ).map(([k, l]) => (
                  <label key={k} className="flex items-center gap-2 text-[13px]">
                    <Switch checked={s[k]} onCheckedChange={(v) => set(k, v)} /> {l}
                  </label>
                ))}
              </div>
            </Row>
            <Row label="Request timeout" hint="Seconds per model call. CPU-only inference can be slow.">
              <NumberInput value={s['ai.timeout_s']} min={10} max={1800} onChange={(n) => set('ai.timeout_s', n)} />
            </Row>
            <Row label="Allow non-local endpoints" hint="Off by default: document text is only sent to servers on this computer.">
              <Switch checked={s['ai.allow_remote']} onCheckedChange={(v) => set('ai.allow_remote', v)} />
            </Row>
            <div className="flex items-center gap-2 pt-3">
              <Button onClick={() => test.mutate()} loading={test.isPending}>
                Test connection
              </Button>
              {models.error ? <span className="text-xs text-danger">{(models.error as Error).message}</span> : models.data ? <span className="text-xs text-muted">{models.data.models.length} models available on the server</span> : null}
            </div>
          </>
        ) : null}
      </Panel>

      <Panel title={<span className="flex items-center gap-2"><Cpu className="size-4 text-subtle" /> This computer & suggested models</span>}>
        {!hw.data ? (
          <Spinner />
        ) : (
          <>
            <div className="mb-3 flex flex-wrap gap-4 text-[13px]">
              <span>{hw.data.hardware.physical_cores} CPU cores</span>
              <span>{hw.data.hardware.ram_total_gb} GB RAM</span>
              <span>{hw.data.hardware.gpus.length ? hw.data.hardware.gpus.map((g) => `${g.name} (${(g.vram_mb / 1024).toFixed(0)} GB)`).join(', ') : 'No supported GPU detected — CPU inference'}</span>
            </div>
            <table className="w-full text-[13px]">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="py-1.5 font-medium">Model</th>
                  <th className="font-medium">Role</th>
                  <th className="font-medium">Download</th>
                  <th className="font-medium">Licence</th>
                  <th className="font-medium">Fit</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {hw.data.models.map((m) => {
                  const p = pulls.data?.find((x) => x.model === m.name)
                  return (
                    <tr key={m.name} className="border-b border-line last:border-0" title={m.notes}>
                      <td className="py-2 font-medium">{m.name}</td>
                      <td className="capitalize text-muted">{m.role}</td>
                      <td className="tabular text-muted">≈{m.approx_download_gb} GB</td>
                      <td className="text-muted">{m.licence}</td>
                      <td>{m.fits ? <Badge tone="ok">{m.gpu_offload ? 'GPU' : 'CPU'}</Badge> : <Badge tone="warn">Needs {m.min_ram_gb} GB RAM</Badge>}</td>
                      <td className="w-44 text-right">
                        {isInstalled(m.name) ? (
                          <Badge tone="ok">Installed</Badge>
                        ) : p?.status === 'running' ? (
                          <div className="space-y-1">
                            <Progress value={pct(p.completed, p.total)} />
                            <span className="text-2xs text-muted">{p.total ? `${formatBytes(p.completed)} / ${formatBytes(p.total)}` : p.message}</span>
                          </div>
                        ) : s['ai.provider'] === 'ollama' ? (
                          <Button size="sm" onClick={() => pull.mutate(m.name)}>
                            <Download /> Download
                          </Button>
                        ) : null}
                        {p?.status === 'failed' ? <p className="text-2xs text-danger">{p.message}</p> : null}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-muted">{hw.data.disclaimer}</p>
            <p className="mt-1 text-xs text-muted">Downloads are performed by the local Ollama server from its model registry (internet access required). Nothing else is downloaded.</p>
          </>
        )}
      </Panel>
    </div>
  )
}

export function SettingsPage(): ReactNode {
  const qc = useQueryClient()
  const { data, error, refetch } = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system })
  const { data: info } = useQuery({ queryKey: ['app-info'], queryFn: () => window.mmi.appInfo() })
  const ocrTest = useMutation({ mutationFn: api.ocrTest, onSuccess: (r) => toast.success(`OCR (${r.engine}): “${r.text}” — ${Math.round(r.confidence * 100)}% confidence`), onError: (e: Error) => toast.error(e.message) })
  const [draft, setDraft] = useState<Draft>({})
  useEffect(() => setDraft({}), [data])
  const s = useMemo(() => ({ ...(data ?? {}), ...draft }) as Settings, [data, draft])
  const set = (k: keyof Settings, v: unknown): void => setDraft((d) => ({ ...d, [k]: v }))
  const dirty = Object.keys(draft).filter((k) => JSON.stringify(draft[k]) !== JSON.stringify((data as Settings | undefined)?.[k]))
  const save = useMutation({
    mutationFn: () => api.updateSettings(Object.fromEntries(dirty.map((k) => [k, draft[k]])) as Partial<Settings>),
    onSuccess: (r) => {
      qc.setQueryData(['settings'], r)
      void qc.invalidateQueries({ queryKey: ['system'] })
      void qc.invalidateQueries({ queryKey: ['ai-models'] })
      toast.success('Settings saved')
    },
    onError: (e: Error) => toast.error(e.message)
  })

  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return <Spinner />
  const fields = s['extraction.custom_fields'] ?? []

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Settings"
        description="Stored locally on this computer"
        actions={
          <>
            {dirty.length ? <span className="text-xs text-warn">{dirty.length} unsaved change(s)</span> : null}
            <Button variant="ghost" disabled={!dirty.length} onClick={() => setDraft({})}>
              Discard
            </Button>
            <Button variant="primary" disabled={!dirty.length} loading={save.isPending} onClick={() => save.mutate()}>
              Save changes
            </Button>
          </>
        }
      />
      <Tabs defaultValue="general" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="bg-surface px-6">
          <TabsTrigger value="general"><User /> General</TabsTrigger>
          <TabsTrigger value="processing"><SlidersHorizontal /> Processing</TabsTrigger>
          <TabsTrigger value="ocr"><ScanText /> OCR</TabsTrigger>
          <TabsTrigger value="ai"><Sparkles /> Local AI</TabsTrigger>
          <TabsTrigger value="fields"><Database /> Checklist fields</TabsTrigger>
          <TabsTrigger value="data"><Shield /> Data & privacy</TabsTrigger>
        </TabsList>
        <div className="min-h-0 flex-1 overflow-auto p-6">
          <TabsContent value="general">
            <Panel>
              <Row label="Reviewer name" hint="Recorded with every review decision and correction.">
                <Input value={s['ui.reviewer_name']} onChange={(e) => set('ui.reviewer_name', e.target.value)} />
              </Row>
              <Row label="Theme">
                <NativeSelect value={s['ui.theme']} onChange={(e) => set('ui.theme', e.target.value)}>
                  <option value="system">Follow Windows</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </NativeSelect>
              </Row>
            </Panel>
          </TabsContent>
          <TabsContent value="processing">
            <Panel>
              <Row label="Parallel workers" hint="Pages processed simultaneously. Higher uses more CPU and memory.">
                <NumberInput value={s['processing.concurrency']} min={1} max={8} onChange={(n) => set('processing.concurrency', n)} />
              </Row>
              <Row label="Pages per batch" hint="Smaller batches checkpoint more often.">
                <NumberInput value={s['processing.batch_size']} min={1} max={200} onChange={(n) => set('processing.batch_size', n)} />
              </Row>
              <Row label="Retry limit" hint="Attempts before a page or task is marked failed.">
                <NumberInput value={s['processing.max_attempts']} min={1} max={10} onChange={(n) => set('processing.max_attempts', n)} />
              </Row>
              <Row label="Minimum free memory (MB)" hint="New work waits while available RAM is below this.">
                <NumberInput value={s['processing.min_free_memory_mb']} min={256} max={32768} step={256} onChange={(n) => set('processing.min_free_memory_mb', n)} />
              </Row>
              <Row label="Maximum procedure length (pages)" hint="Upper bound for automatic boundary detection.">
                <NumberInput value={s['processing.max_procedure_pages']} min={1} max={200} onChange={(n) => set('processing.max_procedure_pages', n)} />
              </Row>
            </Panel>
          </TabsContent>
          <TabsContent value="ocr">
            <Panel>
              <Row label="OCR for scanned pages" hint="Pages without usable digital text are recognised locally.">
                <Switch checked={s['ocr.enabled']} onCheckedChange={(v) => set('ocr.enabled', v)} />
              </Row>
              <Row label="Engine" hint={system ? `Installed: ${Object.entries(system.ocr.available).filter(([, v]) => v).map(([k]) => k).join(', ') || 'none'}` : undefined}>
                <NativeSelect value={s['ocr.engine']} onChange={(e) => set('ocr.engine', e.target.value)}>
                  <option value="auto">Automatic</option>
                  <option value="rapidocr">RapidOCR (PaddleOCR models, ONNX Runtime)</option>
                  <option value="paddleocr">PaddleOCR (PaddlePaddle)</option>
                </NativeSelect>
              </Row>
              <Row label="Render resolution (DPI)" hint="Higher improves small print but is slower.">
                <NumberInput value={s['ocr.dpi']} min={100} max={400} step={25} onChange={(n) => set('ocr.dpi', n)} />
              </Row>
              <Row label="Text threshold" hint="Pages with fewer extractable characters are OCR candidates.">
                <NumberInput value={s['ocr.min_chars']} min={0} max={2000} onChange={(n) => set('ocr.min_chars', n)} />
              </Row>
              <Row label="Minimum confidence" hint="Pages below this are flagged for review.">
                <NumberInput value={s['ocr.min_confidence']} min={0} max={1} step={0.05} onChange={(n) => set('ocr.min_confidence', n)} />
              </Row>
              <div className="pt-3">
                <Button onClick={() => ocrTest.mutate()} loading={ocrTest.isPending}>
                  Test OCR
                </Button>
              </div>
            </Panel>
          </TabsContent>
          <TabsContent value="ai">
            <AiTab s={s} set={set} />
          </TabsContent>
          <TabsContent value="fields">
            <Panel title="Custom checklist fields">
              <p className="mb-3 text-xs text-muted">Add fields required by your maintenance system (e.g. criticality, department, job type). They appear in the review workspace and can be mapped to export columns.</p>
              <div className="space-y-2">
                {fields.map((f, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input value={f.label} placeholder="Label" onChange={(e) => set('extraction.custom_fields', fields.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} className="w-64" />
                    <Input
                      value={f.key}
                      placeholder="key"
                      onChange={(e) => set('extraction.custom_fields', fields.map((x, j) => (j === i ? { ...x, key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') } : x)))}
                      className="w-48 font-mono text-xs"
                    />
                    <Button size="icon" variant="ghost" onClick={() => set('extraction.custom_fields', fields.filter((_, j) => j !== i))} aria-label="Remove field">
                      <Trash2 />
                    </Button>
                  </div>
                ))}
                <Button size="sm" variant="ghost" onClick={() => set('extraction.custom_fields', [...fields, { key: `field_${fields.length + 1}`, label: 'New field' }])}>
                  <Plus /> Add field
                </Button>
              </div>
            </Panel>
          </TabsContent>
          <TabsContent value="data">
            <div className="space-y-5">
              <Panel title="Storage">
                <Row label="Data folder" hint="Database, project copies of manuals, page image cache and logs.">
                  <div className="space-y-2">
                    <code className="selectable block break-all rounded bg-surface-2 px-2 py-1 text-xs">{info?.dataDir ?? system?.storage.data_dir}</code>
                    <Button size="sm" onClick={() => void window.mmi.openDataFolder()}>
                      <FolderOpen /> Open folder
                    </Button>
                  </div>
                </Row>
                {system ? (
                  <>
                    <Row label="Used by this application">{formatBytes(system.storage.app_bytes)} (manuals {formatBytes(system.storage.documents_bytes)})</Row>
                    <Row label="Free disk space">{formatBytes(system.storage.disk_free_bytes)}</Row>
                  </>
                ) : null}
                <Row label="Backup" hint="Close the application, then copy the whole data folder. Restore by copying it back.">
                  <span className="text-xs text-muted">The database is backed up automatically before every schema upgrade (in the backups folder).</span>
                </Row>
                <Row label="Logs">
                  <Button size="sm" onClick={() => void window.mmi.openLogs()}>
                    <FolderOpen /> Open logs
                  </Button>
                </Row>
              </Panel>
              <Panel title="Privacy">
                <ul className="list-disc space-y-1.5 pl-5 text-[13px] text-muted">
                  <li>All document processing runs on this computer. Manuals are never uploaded.</li>
                  <li>No telemetry or usage data is collected.</li>
                  <li>The processing engine listens only on 127.0.0.1 and requires a per-session key.</li>
                  <li>Network access happens only when you choose to: AI model downloads (through Ollama) and, if enabled, non-local AI endpoints.</li>
                </ul>
                <p className="mt-3 text-xs text-subtle">Version {system?.version} · {info?.platform}</p>
              </Panel>
            </div>
          </TabsContent>
        </div>
      </Tabs>
    </div>
  )
}
