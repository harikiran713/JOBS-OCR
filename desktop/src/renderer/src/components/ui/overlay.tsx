import { AlertDialog as AD, Dialog as D, DropdownMenu as DM, Popover as P, Tabs as T, Tooltip as TT } from 'radix-ui'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Button } from './button'

/* ----------------------------------------------------------------- Dialog */
export const Dialog = D.Root
export const DialogTrigger = D.Trigger
export const DialogClose = D.Close

export function DialogContent({
  title,
  description,
  children,
  className,
  footer
}: {
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  className?: string
  footer?: ReactNode
}): ReactNode {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-40 bg-[#0c1524]/45 animate-in" />
      <D.Content
        className={cn(
          'fixed left-1/2 top-1/2 z-50 flex max-h-[88vh] w-[520px] max-w-[94vw] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-line bg-surface shadow-md animate-in',
          className
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div>
            <D.Title className="text-[15px] font-semibold text-text">{title}</D.Title>
            {description ? <D.Description className="mt-0.5 text-xs text-muted">{description}</D.Description> : <D.Description className="sr-only">{String(title)}</D.Description>}
          </div>
          <D.Close asChild>
            <Button variant="ghost" size="icon-sm" aria-label="Close">
              <X />
            </Button>
          </D.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">{children}</div>
        {footer ? <div className="flex justify-end gap-2 border-t border-line bg-surface-2 px-5 py-3">{footer}</div> : null}
      </D.Content>
    </D.Portal>
  )
}

/* ------------------------------------------------------------ AlertDialog */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  destructive,
  onConfirm,
  loading
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel?: string
  destructive?: boolean
  onConfirm: () => void
  loading?: boolean
}): ReactNode {
  return (
    <AD.Root open={open} onOpenChange={onOpenChange}>
      <AD.Portal>
        <AD.Overlay className="fixed inset-0 z-40 bg-[#0c1524]/45 animate-in" />
        <AD.Content className="fixed left-1/2 top-1/2 z-50 w-[440px] max-w-[94vw] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-line bg-surface p-5 shadow-md animate-in">
          <AD.Title className="text-[15px] font-semibold">{title}</AD.Title>
          <AD.Description asChild>
            <div className="mt-2 text-[13px] text-muted">{description}</div>
          </AD.Description>
          <div className="mt-5 flex justify-end gap-2">
            <AD.Cancel asChild>
              <Button variant="secondary">Cancel</Button>
            </AD.Cancel>
            <Button variant={destructive ? 'danger' : 'primary'} loading={loading} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          </div>
        </AD.Content>
      </AD.Portal>
    </AD.Root>
  )
}

/* ----------------------------------------------------------- Dropdown menu */
export const Menu = DM.Root
export const MenuTrigger = DM.Trigger

export function MenuContent({ children, align = 'end', className }: { children: ReactNode; align?: 'start' | 'end' | 'center'; className?: string }): ReactNode {
  return (
    <DM.Portal>
      <DM.Content
        align={align}
        sideOffset={4}
        className={cn('z-50 min-w-[180px] rounded-md border border-line bg-surface p-1 shadow-md animate-in', className)}
      >
        {children}
      </DM.Content>
    </DM.Portal>
  )
}

export function MenuItem({ children, onSelect, danger, disabled }: { children: ReactNode; onSelect?: () => void; danger?: boolean; disabled?: boolean }): ReactNode {
  return (
    <DM.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        'flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] outline-none data-[disabled]:opacity-40 data-[highlighted]:bg-surface-3 [&_svg]:size-4 [&_svg]:text-subtle',
        danger && 'text-danger [&_svg]:text-danger'
      )}
    >
      {children}
    </DM.Item>
  )
}

export function MenuCheckItem({ children, checked, onCheckedChange }: { children: ReactNode; checked: boolean; onCheckedChange: (v: boolean) => void }): ReactNode {
  return (
    <DM.CheckboxItem
      checked={checked}
      onCheckedChange={onCheckedChange}
      onSelect={(e) => e.preventDefault()}
      className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] outline-none data-[highlighted]:bg-surface-3"
    >
      <span className={cn('flex size-3.5 items-center justify-center rounded-[3px] border border-line-strong', checked && 'border-accent bg-accent')}>
        {checked ? <span className="block size-1.5 rounded-[1px] bg-white" /> : null}
      </span>
      {children}
    </DM.CheckboxItem>
  )
}

export const MenuSeparator = (): ReactNode => <DM.Separator className="my-1 h-px bg-line" />
export const MenuLabel = ({ children }: { children: ReactNode }): ReactNode => (
  <DM.Label className="px-2 py-1 text-2xs font-semibold uppercase tracking-wide text-subtle">{children}</DM.Label>
)

/* ----------------------------------------------------------------- Popover */
export const Popover = P.Root
export const PopoverTrigger = P.Trigger
export function PopoverContent({ children, className, align = 'end' }: { children: ReactNode; className?: string; align?: 'start' | 'end' | 'center' }): ReactNode {
  return (
    <P.Portal>
      <P.Content align={align} sideOffset={6} className={cn('z-50 rounded-lg border border-line bg-surface p-3 shadow-md animate-in', className)}>
        {children}
      </P.Content>
    </P.Portal>
  )
}

/* ----------------------------------------------------------------- Tooltip */
export const TooltipProvider = TT.Provider
export function Tooltip({ content, children, side = 'top' }: { content: ReactNode; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }): ReactNode {
  if (!content) return children
  return (
    <TT.Root delayDuration={350}>
      <TT.Trigger asChild>{children}</TT.Trigger>
      <TT.Portal>
        <TT.Content side={side} sideOffset={6} className="z-50 max-w-xs rounded-md bg-[#0c1524] px-2 py-1 text-xs text-white shadow-md animate-in">
          {content}
        </TT.Content>
      </TT.Portal>
    </TT.Root>
  )
}

/* -------------------------------------------------------------------- Tabs */
export const Tabs = T.Root
export function TabsList({ children, className }: { children: ReactNode; className?: string }): ReactNode {
  return <T.List className={cn('flex items-center gap-1 border-b border-line', className)}>{children}</T.List>
}
export function TabsTrigger({ value, children, className }: { value: string; children: ReactNode; className?: string }): ReactNode {
  return (
    <T.Trigger
      value={value}
      className={cn(
        '-mb-px flex items-center gap-1.5 border-b-2 border-transparent px-3 py-2 text-[13px] font-medium text-muted hover:text-text',
        'data-[state=active]:border-accent data-[state=active]:text-text [&_svg]:size-4',
        className
      )}
    >
      {children}
    </T.Trigger>
  )
}
export const TabsContent = T.Content
