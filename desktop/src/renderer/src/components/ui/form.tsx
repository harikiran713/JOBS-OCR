import { Checkbox as CheckboxPrimitive, Label as LabelPrimitive, Switch as SwitchPrimitive } from 'radix-ui'
import { Check, Minus } from 'lucide-react'
import { forwardRef, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      'h-8 w-full rounded-md border border-line bg-surface px-2.5 text-[13px] text-text shadow-sm placeholder:text-subtle',
      'focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-60',
      'aria-[invalid=true]:border-danger',
      className
    )}
    {...props}
  />
))
Input.displayName = 'Input'

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        'w-full rounded-md border border-line bg-surface px-2.5 py-2 text-[13px] leading-relaxed text-text shadow-sm placeholder:text-subtle',
        'focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/15',
        className
      )}
      {...props}
    />
  )
)
Textarea.displayName = 'Textarea'

export function Label({ className, ...props }: LabelPrimitive.LabelProps): ReactNode {
  return <LabelPrimitive.Root className={cn('text-xs font-medium text-muted', className)} {...props} />
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  className
}: {
  label: ReactNode
  hint?: ReactNode
  error?: string
  children: ReactNode
  htmlFor?: string
  className?: string
}): ReactNode {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? <p className="text-xs text-danger">{error}</p> : hint ? <p className="text-xs text-subtle">{hint}</p> : null}
    </div>
  )
}

export function Checkbox({
  checked,
  onCheckedChange,
  className,
  ...props
}: CheckboxPrimitive.CheckboxProps): ReactNode {
  return (
    <CheckboxPrimitive.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      className={cn(
        'flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-line-strong bg-surface',
        'data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=indeterminate]:border-accent data-[state=indeterminate]:bg-accent',
        className
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="text-white">
        {checked === 'indeterminate' ? <Minus className="size-3" strokeWidth={3} /> : <Check className="size-3" strokeWidth={3} />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
}

export function Switch({ className, ...props }: SwitchPrimitive.SwitchProps): ReactNode {
  return (
    <SwitchPrimitive.Root
      className={cn(
        'relative inline-flex h-[18px] w-8 shrink-0 cursor-pointer items-center rounded-full border border-transparent bg-line-strong transition-colors',
        'data-[state=checked]:bg-accent disabled:opacity-50',
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block size-3.5 translate-x-0.5 rounded-full bg-white shadow-sm transition-transform data-[state=checked]:translate-x-[15px]" />
    </SwitchPrimitive.Root>
  )
}

export function NativeSelect({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>): ReactNode {
  return (
    <select
      className={cn(
        'h-8 rounded-md border border-line bg-surface px-2 pr-7 text-[13px] text-text shadow-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/15',
        className
      )}
      {...props}
    >
      {children}
    </select>
  )
}
