import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SpecListView } from './SpecListView'
import type { Spec } from '../types'

// Mock the ServiceContext
vi.mock('../contexts/ServiceContext', () => ({
  useService: () => ({
    cloneSpec: vi.fn(),
    deleteSpec: vi.fn(),
  }),
}))

// Mock the toast hook
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}))

function makeSpec(overrides: Partial<Spec> & { id: string }): Spec {
  return {
    title: overrides.id,
    source: 'odin',
    content: '',
    abandoned: false,
    boardId: 'b1',
    metadata: {},
    createdAt: new Date().toISOString(),
    tasks: [],
    taskCount: 0,
    ...overrides,
  }
}

describe('SpecListView', () => {
  const noop = () => {}

  it('renders empty state when no specs', () => {
    render(<SpecListView specs={[]} onSpecClick={noop} onDataChange={noop} />)
    expect(screen.getByText('No specs found')).toBeInTheDocument()
  })

  it('renders spec cards', () => {
    const specs = [
      makeSpec({ id: 'sp_001', title: 'Auth feature' }),
      makeSpec({ id: 'sp_002', title: 'Payment flow' }),
    ]
    render(<SpecListView specs={specs} onSpecClick={noop} onDataChange={noop} />)
    expect(screen.getByText('Auth feature')).toBeInTheDocument()
    expect(screen.getByText('Payment flow')).toBeInTheDocument()
  })

  it('filters specs by search term', () => {
    const specs = [
      makeSpec({ id: 'sp_001', title: 'Auth feature' }),
      makeSpec({ id: 'sp_002', title: 'Payment flow' }),
    ]
    render(<SpecListView specs={specs} onSpecClick={noop} onDataChange={noop} />)

    const input = screen.getByPlaceholderText('Search specs...')
    fireEvent.change(input, { target: { value: 'payment' } })

    expect(screen.queryByText('Auth feature')).not.toBeInTheDocument()
    expect(screen.getByText('Payment flow')).toBeInTheDocument()
  })

  it('hides abandoned specs by default', () => {
    const specs = [
      makeSpec({ id: 'sp_001', title: 'Active spec' }),
      makeSpec({ id: 'sp_002', title: 'Abandoned spec', abandoned: true }),
    ]
    render(<SpecListView specs={specs} onSpecClick={noop} onDataChange={noop} />)

    expect(screen.getByText('Active spec')).toBeInTheDocument()
    expect(screen.queryByText('Abandoned spec')).not.toBeInTheDocument()
  })

  it('shows pagination when more than 50 specs', () => {
    const specs = Array.from({ length: 55 }, (_, i) =>
      makeSpec({ id: `sp_${String(i).padStart(3, '0')}`, title: `Spec ${i}` })
    )
    render(<SpecListView specs={specs} onSpecClick={noop} onDataChange={noop} />)

    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument()
    expect(screen.getByText('Next')).toBeInTheDocument()
  })

  it('does not show pagination for small lists', () => {
    const specs = [makeSpec({ id: 'sp_001', title: 'Only spec' })]
    render(<SpecListView specs={specs} onSpecClick={noop} onDataChange={noop} />)

    expect(screen.queryByText(/Page \d+ of \d+/)).not.toBeInTheDocument()
  })
})
