import { zodResolver } from '@hookform/resolvers/zod'
import { Undo2 } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/display'
import { Checkbox, Field, Input, NativeSelect } from '@/components/ui/form'
import { Tooltip } from '@/components/ui/overlay'
import type { JobDetail } from '@/lib/types'

const FREQ_TYPES: Array<[string, string]> = [
  ['calendar', 'Calendar'],
  ['operating_hours', 'Operating hours'],
  ['running_hours', 'Running hours'],
  ['engine_hours', 'Engine hours'],
  ['hours_unqualified', 'Hours (basis not stated)'],
  ['cycle', 'Cycles / starts'],
  ['event', 'Event-based'],
  ['condition', 'Condition-based'],
  ['unspecified', 'Not specified']
]
const UNITS = ['', 'hour', 'day', 'week', 'month', 'year', 'cycle']

const schema = z.object({
  title: z.string().trim().min(1, 'Title is required').max(500),
  equipment: z.string().trim().max(300),
  component: z.string().trim().max(300),
  frequency_original: z.string().max(500),
  frequency_value: z.string().refine((v) => v.trim() === '' || (!Number.isNaN(Number(v)) && Number(v) > 0), 'Enter a positive number or leave empty'),
  frequency_unit: z.string(),
  frequency_type: z.string(),
  frequency_uncertain: z.boolean(),
  extra: z.record(z.string(), z.string())
})
type FormValues = z.infer<typeof schema>

function toForm(job: JobDetail): FormValues {
  return {
    title: job.title,
    equipment: job.equipment,
    component: job.component,
    frequency_original: job.frequency_original,
    frequency_value: job.frequency_value == null ? '' : String(job.frequency_value),
    frequency_unit: job.frequency_unit,
    frequency_type: job.frequency_type,
    frequency_uncertain: job.frequency_uncertain,
    extra: Object.fromEntries(job.custom_fields.map((f) => [f.key, String(job.extra_fields[f.key] ?? '')]))
  }
}

function MachineHint({ job, field, onRevert }: { job: JobDetail; field: string; onRevert: () => void }): ReactNode {
  if (!job.edited_fields.includes(field)) return null
  const machine = (job.machine as unknown as Record<string, unknown>)[field]
  return (
    <span className="flex items-center gap-1.5 text-2xs text-subtle">
      <Badge tone="human">Reviewer edit</Badge>
      Machine value: “{machine === null || machine === '' ? 'empty' : String(machine)}”
      <Tooltip content="Revert to machine value">
        <button type="button" onClick={onRevert} className="text-accent-text hover:underline" aria-label={`Revert ${field}`}>
          <Undo2 className="size-3" />
        </button>
      </Tooltip>
    </span>
  )
}

export function JobFieldsForm({ job, saving, onSave }: { job: JobDetail; saving: boolean; onSave: (changes: Record<string, unknown>) => void }): ReactNode {
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: toForm(job) })
  useEffect(() => form.reset(toForm(job)), [job, form])
  const e = form.formState.errors

  const submit = form.handleSubmit((v) => {
    const changes: Record<string, unknown> = {}
    const dirty = form.formState.dirtyFields
    for (const k of ['title', 'equipment', 'component', 'frequency_original', 'frequency_unit', 'frequency_type'] as const) {
      if (dirty[k]) changes[k] = v[k].trim()
    }
    if (dirty.frequency_value) changes.frequency_value = v.frequency_value.trim() === '' ? null : Number(v.frequency_value)
    if (dirty.frequency_uncertain) changes.frequency_uncertain = v.frequency_uncertain
    for (const f of job.custom_fields) if (dirty.extra?.[f.key]) changes[`extra.${f.key}`] = v.extra[f.key]
    if (Object.keys(changes).length) onSave(changes)
  })
  const revert = (field: string): void => {
    const m = job.machine as unknown as Record<string, unknown>
    onSave({ [field]: m[field] })
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <Field label={<span className="flex items-center justify-between">Job title <MachineHint job={job} field="title" onRevert={() => revert('title')} /></span>} error={e.title?.message}>
        <Input {...form.register('title')} aria-invalid={!!e.title} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Equipment" hint={<MachineHint job={job} field="equipment" onRevert={() => revert('equipment')} />}>
          <Input {...form.register('equipment')} />
        </Field>
        <Field label="Component" hint={<MachineHint job={job} field="component" onRevert={() => revert('component')} />}>
          <Input {...form.register('component')} />
        </Field>
      </div>
      <div className="rounded-md border border-line bg-surface-2 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-medium text-muted">Frequency</span>
          {job.machine.frequency_notes ? <span className="max-w-[60%] truncate text-2xs text-subtle" title={job.machine.frequency_notes}>{job.machine.frequency_notes}</span> : null}
        </div>
        <Field label="Original text in source" hint={<MachineHint job={job} field="frequency_original" onRevert={() => revert('frequency_original')} />}>
          <Input {...form.register('frequency_original')} placeholder="Not specified in source" />
        </Field>
        <div className="mt-3 grid grid-cols-[1fr_1fr_1.4fr] gap-2">
          <Field label="Value" error={e.frequency_value?.message}>
            <Input {...form.register('frequency_value')} inputMode="decimal" placeholder="—" />
          </Field>
          <Field label="Unit">
            <NativeSelect {...form.register('frequency_unit')}>
              {UNITS.map((u) => (
                <option key={u} value={u}>
                  {u || '—'}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Interval type">
            <NativeSelect {...form.register('frequency_type')}>
              {FREQ_TYPES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </NativeSelect>
          </Field>
        </div>
        <label className="mt-3 flex items-center gap-2 text-[13px]">
          <Checkbox checked={form.watch('frequency_uncertain')} onCheckedChange={(v) => form.setValue('frequency_uncertain', !!v, { shouldDirty: true })} />
          Frequency interpretation is uncertain
        </label>
        {job.machine.frequency_alternatives?.length ? (
          <p className="mt-2 text-2xs text-subtle">Also found: {job.machine.frequency_alternatives.map((a) => a.label || a.original).join(', ')}</p>
        ) : null}
      </div>
      {job.custom_fields.length ? (
        <div className="grid grid-cols-2 gap-3">
          {job.custom_fields.map((f) => (
            <Field key={f.key} label={f.label}>
              <Input {...form.register(`extra.${f.key}`)} />
            </Field>
          ))}
        </div>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" disabled={!form.formState.isDirty} onClick={() => form.reset(toForm(job))}>
          Discard
        </Button>
        <Button type="submit" variant="primary" size="sm" disabled={!form.formState.isDirty} loading={saving}>
          Save corrections
        </Button>
      </div>
    </form>
  )
}
