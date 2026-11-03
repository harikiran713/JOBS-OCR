import { useQuery } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2, Maximize2, PanelLeft, ScanText, Search, X, ZoomIn, ZoomOut } from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/display'
import { Tooltip } from '@/components/ui/overlay'
import { api, pageImageUrl } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface HighlightBand {
  page: number
  /** normalised 0..1 vertical range on the page */
  y0: number
  y1: number
  tone: 'procedure' | 'schedule' | 'candidate'
  label?: string
}

export interface HighlightRect {
  page: number
  rect: number[] // normalised x0,y0,x1,y1
  tone: 'schedule' | 'search'
}

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3]
const RENDER_SCALES = [0.5, 1, 1.5, 2, 3]

const measureCanvas = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null

const TextLayer = memo(function TextLayer({ words, width, height }: { words: Array<{ t: string; b: number[] }>; width: number; height: number }) {
  return (
    <div className="text-layer selectable absolute inset-0" aria-hidden={false}>
      {words.map((w, i) => {
        const [x0, y0, x1, y1] = w.b
        const h = (y1 - y0) * height
        const targetW = (x1 - x0) * width
        const fontSize = Math.max(h * 0.92, 1)
        let scaleX = 1
        if (measureCanvas) {
          measureCanvas.font = `${fontSize}px sans-serif`
          const m = measureCanvas.measureText(w.t).width
          if (m > 0) scaleX = targetW / m
        }
        return (
          <span key={i} style={{ left: x0 * width, top: y0 * height, fontSize, transform: `scaleX(${scaleX})` }}>
            {w.t + ' '}
          </span>
        )
      })}
    </div>
  )
})

function Thumbnails({ documentId, pageCount, current, onSelect, marked }: { documentId: string; pageCount: number; current: number; onSelect: (p: number) => void; marked: Set<number> }): ReactNode {
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState<Set<number>>(new Set([1, 2, 3, 4, 5, 6]))
  useEffect(() => {
    const root = ref.current
    if (!root) return
    const obs = new IntersectionObserver(
      (entries) => {
        setVisible((prev) => {
          const next = new Set(prev)
          for (const e of entries) {
            const p = Number((e.target as HTMLElement).dataset.page)
            if (e.isIntersecting) next.add(p)
          }
          return next
        })
      },
      { root, rootMargin: '400px 0px' }
    )
    root.querySelectorAll('[data-page]').forEach((el) => obs.observe(el))
    return () => obs.disconnect()
  }, [pageCount, documentId])
  useEffect(() => {
    ref.current?.querySelector(`[data-page="${current}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [current])
  // Only a window of thumbnails is ever mounted as images; others are placeholders.
  return (
    <div ref={ref} className="w-[112px] shrink-0 overflow-y-auto border-r border-line bg-surface-2 p-2">
      {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
        <button
          key={p}
          data-page={p}
          onClick={() => onSelect(p)}
          className={cn('mb-2 block w-full rounded border bg-surface p-1 text-center', p === current ? 'border-accent ring-2 ring-accent/20' : 'border-line hover:border-line-strong')}
        >
          <div className="relative flex aspect-[1/1.41] items-center justify-center overflow-hidden bg-white">
            {visible.has(p) ? <img src={pageImageUrl(documentId, p, 0.25)} alt="" loading="lazy" className="max-h-full max-w-full" /> : null}
            {marked.has(p) ? <span className="absolute right-0.5 top-0.5 size-2 rounded-full bg-accent" /> : null}
          </div>
          <div className="tabular mt-0.5 text-2xs text-muted">{p}</div>
        </button>
      ))}
    </div>
  )
}

function ContinuousPages({
  documentId,
  pageCount,
  page,
  onPageChange,
  width,
  height,
  renderScale,
  bands,
  rects,
  scrollRef
}: {
  documentId: string
  pageCount: number
  page: number
  onPageChange: (p: number) => void
  width: number
  height: number
  renderScale: number
  bands: HighlightBand[]
  rects: HighlightRect[]
  scrollRef: React.RefObject<HTMLDivElement | null>
}): ReactNode {
  const gap = 16
  /** True while we are programmatically moving to a page — ignore scroll sync. */
  const locking = useRef(true)
  /** Page changes that came from the user scrolling should not re-trigger scrollToIndex. */
  const fromScroll = useRef(false)
  const pageRef = useRef(page)
  pageRef.current = page

  const virt = useVirtualizer({
    count: pageCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => height + gap,
    overscan: 3
  })

  const jumpTo = (p: number): void => {
    locking.current = true
    const idx = Math.max(0, Math.min(pageCount - 1, p - 1))
    virt.scrollToIndex(idx, { align: 'start' })
    window.setTimeout(() => {
      locking.current = false
    }, 120)
  }

  // Open / toolbar / page-link navigation — not user scrolling.
  useEffect(() => {
    if (fromScroll.current) {
      fromScroll.current = false
      return
    }
    jumpTo(page)
  }, [page, documentId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the same page in view when zoom/size changes (do not jump to page 1).
  useEffect(() => {
    virt.measure()
    jumpTo(pageRef.current)
  }, [height, width]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = (): void => {
      if (locking.current) return
      const items = virt.getVirtualItems()
      if (!items.length) return
      const mid = el.scrollTop + el.clientHeight * 0.3
      let best = items[0]
      for (const it of items) {
        if (it.start <= mid) best = it
      }
      const next = best.index + 1
      if (next !== pageRef.current) {
        fromScroll.current = true
        onPageChange(next)
      }
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [virt, onPageChange, scrollRef])

  return (
    <div style={{ height: virt.getTotalSize(), position: 'relative', width: '100%' }}>
      {virt.getVirtualItems().map((vi) => {
        const p = vi.index + 1
        const pageBands = bands.filter((b) => b.page === p)
        const pageRects = rects.filter((r) => r.page === p)
        return (
          <div
            key={vi.key}
            data-index={vi.index}
            ref={virt.measureElement}
            className="absolute left-0 flex w-full justify-center px-6"
            style={{ top: vi.start, height: height + gap, paddingBottom: gap }}
          >
            <div className="relative shrink-0 bg-white shadow-md" style={{ width, height }}>
              <img
                src={pageImageUrl(documentId, p, renderScale)}
                alt={`Page ${p}`}
                draggable={false}
                loading="lazy"
                className="absolute inset-0 h-full w-full"
              />
              {pageBands.map((b, i) => (
                <div
                  key={i}
                  className={cn(
                    'pointer-events-none absolute inset-x-0 border-l-4',
                    b.tone === 'procedure' ? 'border-ok bg-ok/[0.07]' : b.tone === 'schedule' ? 'border-accent bg-accent/[0.06]' : 'border-warn bg-warn/[0.07]'
                  )}
                  style={{ top: b.y0 * height, height: Math.max((b.y1 - b.y0) * height, 4) }}
                >
                  {b.label ? (
                    <span className={cn('absolute -left-1 -top-5 rounded px-1.5 py-0.5 text-2xs font-medium text-white', b.tone === 'procedure' ? 'bg-ok' : b.tone === 'schedule' ? 'bg-accent' : 'bg-warn')}>
                      {b.label}
                    </span>
                  ) : null}
                </div>
              ))}
              {pageRects.map((r, i) => (
                <div
                  key={`r${i}`}
                  className={cn('pointer-events-none absolute rounded-[2px]', r.tone === 'search' ? 'bg-[#facc15]/40' : 'outline-2 outline-accent bg-accent/10')}
                  style={{ left: r.rect[0] * width - 2, top: r.rect[1] * height - 2, width: (r.rect[2] - r.rect[0]) * width + 4, height: (r.rect[3] - r.rect[1]) * height + 4 }}
                />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function PdfViewer({
  documentId,
  pageCount,
  page,
  onPageChange,
  bands = [],
  rects = [],
  title,
  toolbarExtra,
  defaultThumbnails = false,
  initialSearch,
  continuous = false
}: {
  documentId: string
  pageCount: number
  page: number
  onPageChange: (p: number) => void
  bands?: HighlightBand[]
  rects?: HighlightRect[]
  title?: ReactNode
  toolbarExtra?: ReactNode
  defaultThumbnails?: boolean
  initialSearch?: string
  /** Stack pages vertically so the document can be scrolled continuously. */
  continuous?: boolean
}): ReactNode {
  const [zoom, setZoom] = useState<number | 'fit'>('fit')
  const [thumbs, setThumbs] = useState(defaultThumbnails)
  const [pageInput, setPageInput] = useState(String(page))
  const [searchOpen, setSearchOpen] = useState(!!initialSearch)
  const [search, setSearch] = useState(initialSearch ?? '')
  const [activeQuery, setActiveQuery] = useState(initialSearch ?? '')
  const [imgLoaded, setImgLoaded] = useState(false)
  const [containerW, setContainerW] = useState(800)
  const scrollRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => setPageInput(String(page)), [page])
  useEffect(() => setImgLoaded(false), [documentId, page])
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setContainerW(el.clientWidth))
    ro.observe(el)
    setContainerW(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const layer = useQuery({ queryKey: ['page-text', documentId, page], queryFn: () => api.pageText(documentId, page), staleTime: Infinity })
  const hits = useQuery({
    queryKey: ['doc-search', documentId, activeQuery],
    queryFn: () => api.searchDocument(documentId, activeQuery),
    enabled: activeQuery.length >= 2
  })
  const pageHits = useQuery({
    queryKey: ['highlights', documentId, page, activeQuery],
    queryFn: () => api.highlights(documentId, page, activeQuery),
    enabled: activeQuery.length >= 2 && !continuous
  })

  const geom = layer.data?.geometry ?? { width: 595, height: 842, rotation: 0 }
  const effectiveZoom = zoom === 'fit' ? Math.max(0.3, Math.min(3, (containerW - 48) / geom.width)) : zoom
  const width = geom.width * effectiveZoom
  const height = geom.height * effectiveZoom
  const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1
  const renderScale = RENDER_SCALES.find((s) => s >= effectiveZoom * dpr) ?? 3

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        setSearchOpen(true)
        setTimeout(() => searchRef.current?.focus(), 0)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const go = (p: number): void => {
    const next = Math.max(1, Math.min(pageCount, p))
    if (next !== page) onPageChange(next)
    if (!continuous) scrollRef.current?.scrollTo({ top: 0 })
  }

  const hitPages = useMemo(() => [...new Set(hits.data?.hits.map((h) => h.page_no) ?? [])], [hits.data])
  const markedPages = useMemo(() => new Set([...bands.map((b) => b.page), ...hitPages]), [bands, hitPages])
  const pageBands = bands.filter((b) => b.page === page)
  const pageRects = [...rects.filter((r) => r.page === page), ...(pageHits.data?.rects ?? []).map((rect) => ({ page, rect, tone: 'search' as const }))]
  const zoomIndex = ZOOMS.findIndex((z) => z >= effectiveZoom - 0.01)
  const allRects = useMemo(
    () => [...rects, ...(pageHits.data?.rects ?? []).map((rect) => ({ page, rect, tone: 'search' as const }))],
    [rects, pageHits.data, page]
  )

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-3">
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-line bg-surface px-2">
        <Tooltip content="Thumbnails">
          <Button variant="ghost" size="icon-sm" onClick={() => setThumbs((t) => !t)} aria-pressed={thumbs} aria-label="Toggle thumbnails">
            <PanelLeft />
          </Button>
        </Tooltip>
        {title ? <div className="mx-1 min-w-0 max-w-[260px] truncate text-xs font-medium text-muted">{title}</div> : null}
        <div className="mx-1 h-5 w-px bg-line" />
        <Button variant="ghost" size="icon-sm" onClick={() => go(page - 1)} disabled={page <= 1} aria-label="Previous page">
          <ChevronLeft />
        </Button>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const n = Number(pageInput)
            if (Number.isFinite(n)) go(n)
          }}
          className="flex items-center gap-1 text-xs text-muted"
        >
          <input
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value.replace(/\D/g, ''))}
            className="tabular h-6 w-12 rounded border border-line bg-surface text-center text-xs text-text focus:border-accent focus:outline-none"
            aria-label="Page number"
          />
          <span className="tabular">/ {pageCount}</span>
        </form>
        <Button variant="ghost" size="icon-sm" onClick={() => go(page + 1)} disabled={page >= pageCount} aria-label="Next page">
          <ChevronRight />
        </Button>
        <div className="mx-1 h-5 w-px bg-line" />
        <Button variant="ghost" size="icon-sm" onClick={() => setZoom(ZOOMS[Math.max(0, zoomIndex - 1)])} aria-label="Zoom out">
          <ZoomOut />
        </Button>
        <span className="tabular w-10 text-center text-xs text-muted">{Math.round(effectiveZoom * 100)}%</span>
        <Button variant="ghost" size="icon-sm" onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, zoomIndex + 1)])} aria-label="Zoom in">
          <ZoomIn />
        </Button>
        <Tooltip content="Fit width">
          <Button variant={zoom === 'fit' ? 'secondary' : 'ghost'} size="icon-sm" onClick={() => setZoom('fit')} aria-label="Fit width">
            <Maximize2 />
          </Button>
        </Tooltip>
        <div className="flex-1" />
        {toolbarExtra}
        <Tooltip content="Search document (Ctrl+F)">
          <Button variant={searchOpen ? 'secondary' : 'ghost'} size="icon-sm" onClick={() => setSearchOpen((s) => !s)} aria-label="Search document">
            <Search />
          </Button>
        </Tooltip>
      </div>

      {searchOpen ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-line bg-surface px-2 py-1.5">
          <form
            className="flex flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              setActiveQuery(search.trim())
            }}
          >
            <input
              ref={searchRef}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search this document…"
              className="h-7 flex-1 rounded border border-line bg-surface px-2 text-xs focus:border-accent focus:outline-none"
            />
          </form>
          {hits.isFetching ? <Loader2 className="size-3.5 animate-spin text-subtle" /> : null}
          {activeQuery && hits.data ? (
            <div className="flex items-center gap-1 text-xs text-muted">
              <span className="tabular">{hitPages.length} pages</span>
              <Button
                variant="ghost"
                size="icon-sm"
                disabled={!hitPages.some((p) => p < page)}
                onClick={() => go([...hitPages].reverse().find((p) => p < page) ?? page)}
                aria-label="Previous match"
              >
                <ChevronLeft />
              </Button>
              <Button variant="ghost" size="icon-sm" disabled={!hitPages.some((p) => p > page)} onClick={() => go(hitPages.find((p) => p > page) ?? page)} aria-label="Next match">
                <ChevronRight />
              </Button>
            </div>
          ) : null}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => {
              setSearchOpen(false)
              setActiveQuery('')
              setSearch('')
            }}
            aria-label="Close search"
          >
            <X />
          </Button>
        </div>
      ) : null}

      {layer.data && (layer.data.status === 'needs_review' || layer.data.status === 'failed' || layer.data.source === 'ocr') ? (
        <div
          className={cn(
            'flex shrink-0 items-center gap-2 border-b px-3 py-1.5 text-xs',
            layer.data.status === 'failed' ? 'border-danger/20 bg-danger-soft text-danger' : layer.data.status === 'needs_review' ? 'border-warn/20 bg-warn-soft text-warn' : 'border-line bg-info-soft text-info'
          )}
        >
          {layer.data.status === 'completed' ? <ScanText className="size-3.5" /> : <AlertTriangle className="size-3.5" />}
          {layer.data.source === 'ocr' ? <span>Text on this page was recognised by OCR.</span> : null}
          {layer.data.status_reason ? <span>{layer.data.status_reason}</span> : null}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        {thumbs ? <Thumbnails documentId={documentId} pageCount={pageCount} current={page} onSelect={go} marked={markedPages} /> : null}
        <div
          ref={scrollRef}
          className="min-h-0 min-w-0 flex-1 overflow-auto"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'PageDown' || (e.key === 'ArrowRight' && !e.ctrlKey)) go(page + 1)
            if (e.key === 'PageUp' || (e.key === 'ArrowLeft' && !e.ctrlKey)) go(page - 1)
          }}
        >
          {continuous ? (
            <div className="py-4">
              <ContinuousPages
                documentId={documentId}
                pageCount={pageCount}
                page={page}
                onPageChange={onPageChange}
                width={width}
                height={height}
                renderScale={renderScale}
                bands={bands}
                rects={allRects}
                scrollRef={scrollRef}
              />
            </div>
          ) : (
            <div className="flex min-h-full justify-center p-6">
              <div className="relative shrink-0 bg-white shadow-md" style={{ width, height }}>
                <img
                  key={`${documentId}-${page}-${renderScale}`}
                  src={pageImageUrl(documentId, page, renderScale)}
                  alt={`Page ${page}`}
                  draggable={false}
                  onLoad={() => setImgLoaded(true)}
                  className="absolute inset-0 h-full w-full"
                />
                {!imgLoaded ? (
                  <div className="absolute inset-0 flex items-center justify-center text-xs text-subtle">
                    <Loader2 className="mr-2 size-4 animate-spin" /> Rendering page {page}…
                  </div>
                ) : null}
                {pageBands.map((b, i) => (
                  <div
                    key={i}
                    className={cn(
                      'pointer-events-none absolute inset-x-0 border-l-4',
                      b.tone === 'procedure' ? 'border-ok bg-ok/[0.07]' : b.tone === 'schedule' ? 'border-accent bg-accent/[0.06]' : 'border-warn bg-warn/[0.07]'
                    )}
                    style={{ top: b.y0 * height, height: Math.max((b.y1 - b.y0) * height, 4) }}
                  >
                    {b.label ? (
                      <span className={cn('absolute -left-1 -top-5 rounded px-1.5 py-0.5 text-2xs font-medium text-white', b.tone === 'procedure' ? 'bg-ok' : b.tone === 'schedule' ? 'bg-accent' : 'bg-warn')}>
                        {b.label}
                      </span>
                    ) : null}
                  </div>
                ))}
                {pageRects.map((r, i) => (
                  <div
                    key={`r${i}`}
                    className={cn('pointer-events-none absolute rounded-[2px]', r.tone === 'search' ? 'bg-[#facc15]/40' : 'outline-2 outline-accent bg-accent/10')}
                    style={{ left: r.rect[0] * width - 2, top: r.rect[1] * height - 2, width: (r.rect[2] - r.rect[0]) * width + 4, height: (r.rect[3] - r.rect[1]) * height + 4 }}
                  />
                ))}
                {layer.data ? <TextLayer words={layer.data.words} width={width} height={height} /> : null}
              </div>
            </div>
          )}
        </div>
      </div>
      {layer.data?.page_kind && layer.data.page_kind !== 'unknown' ? (
        <div className="flex h-7 shrink-0 items-center gap-2 border-t border-line bg-surface px-3 text-2xs text-muted">
          <span>Page {page}</span>
          <Badge tone="neutral">{layer.data.page_kind.replace('_', ' ')}</Badge>
          <span>{layer.data.source === 'ocr' ? 'OCR text' : layer.data.source === 'digital' ? 'Digital text' : 'No text layer'}</span>
        </div>
      ) : null}
    </div>
  )
}
