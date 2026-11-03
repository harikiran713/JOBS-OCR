import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProvenanceTag, StatusBadge } from '@/components/ui/display'
import { ApiError, configureApi, pageImageUrl } from '@/lib/api'
import { formatBytes, pageRange, pct } from '@/lib/utils'

describe('utils', () => {
  it('formats sizes, ranges and percentages', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(pageRange(40, 41)).toBe('40–41')
    expect(pageRange(7, 7)).toBe('7')
    expect(pageRange(null, null)).toBe('—')
    expect(pct(1, 3)).toBe(33)
    expect(pct(5, 0)).toBe(0)
  })
})

describe('status presentation', () => {
  it('labels procedure states distinctly', () => {
    render(
      <>
        <StatusBadge status="verified" kind="proc" />
        <StatusBadge status="needs_confirmation" kind="proc" />
        <StatusBadge status="confirmed" kind="proc" />
      </>
    )
    expect(screen.getByText('Verified match')).toBeTruthy()
    expect(screen.getByText('Needs confirmation')).toBeTruthy()
    expect(screen.getByText('Confirmed by reviewer')).toBeTruthy()
  })

  it('distinguishes human, AI-generated and extracted text', () => {
    render(
      <>
        <ProvenanceTag human method="extractive" />
        <ProvenanceTag human={false} method="llm:qwen3:8b" />
        <ProvenanceTag human={false} method="extractive" />
      </>
    )
    expect(screen.getByText('Reviewer edit')).toBeTruthy()
    expect(screen.getByText('AI-generated')).toBeTruthy()
    expect(screen.getByText('Extracted from source')).toBeTruthy()
  })
})

describe('api client', () => {
  it('builds authenticated image URLs only for the configured local backend', () => {
    configureApi('http://127.0.0.1:5000', 'secret')
    expect(pageImageUrl('doc-1', 3, 1.5)).toBe('http://127.0.0.1:5000/api/documents/doc-1/pages/3/image?scale=1.5&token=secret')
    expect(new ApiError('x', 401).status).toBe(401)
  })
})
