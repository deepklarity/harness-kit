import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { CapacityBadge } from './CapacityBadge'
import type { ExecutorCapacity } from '@/types'

function createMockService(overrides: Partial<{
    fetchExecutorCapacity: ReturnType<typeof vi.fn>
}> = {}) {
    const fetchExecutorCapacity: ReturnType<typeof vi.fn> = overrides.fetchExecutorCapacity ?? vi.fn().mockResolvedValue({
        running: 2,
        max: 3,
        suggested_max: 4,
    } satisfies ExecutorCapacity)

    return { fetchExecutorCapacity }
}

type MockService = ReturnType<typeof createMockService>
type BadgeProps = React.ComponentProps<typeof CapacityBadge>
type BadgeService = BadgeProps['service']

function asBadgeService(s: MockService): BadgeService {
    return s as unknown as BadgeService
}

describe('CapacityBadge', () => {
    beforeEach(() => {
        vi.clearAllMocks()
    })

    it('renders running/max readout after load', async () => {
        const service = createMockService({
            fetchExecutorCapacity: vi.fn().mockResolvedValue({
                running: 2,
                max: 3,
                suggested_max: 4,
            } satisfies ExecutorCapacity),
        })
        render(<CapacityBadge service={asBadgeService(service)} />)

        await waitFor(() => {
            expect(screen.getByText('2/3')).toBeInTheDocument()
        })
    })

    it('renders placeholder dashes before load completes', () => {
        const service = createMockService({
            fetchExecutorCapacity: vi.fn().mockImplementation(() => new Promise<ExecutorCapacity>(() => {})),
        })
        render(<CapacityBadge service={asBadgeService(service)} />)
        expect(screen.getByText('—/—')).toBeInTheDocument()
    })

    it('shows at-capacity styling when running equals max', async () => {
        const service = createMockService({
            fetchExecutorCapacity: vi.fn().mockResolvedValue({
                running: 3,
                max: 3,
                suggested_max: 4,
            } satisfies ExecutorCapacity),
        })
        const { container } = render(<CapacityBadge service={asBadgeService(service)} />)

        await waitFor(() => {
            expect(screen.getByText('3/3')).toBeInTheDocument()
        })
        expect(container.querySelector('[data-capacity-state="saturated"]')).toBeInTheDocument()
    })

    // Task #353: badge must split executions + reflections so the operator
    // sees the real memory-share picture from the same accounting the
    // dispatcher uses.
    it('renders executing+reflecting/max when the new fields are populated', async () => {
        const service = createMockService({
            fetchExecutorCapacity: vi.fn().mockResolvedValue({
                running: 3,
                max: 4,
                suggested_max: 4,
                executing: 3,
                reflecting: 1,
                shares_in_use: 4,
                memory_max_shares: 4,
                memory_share_holders: [
                    { task_id: '344', task_title: 'Live A', kind: 'execution', mem_mib: 4096 },
                    { task_id: '346', task_title: 'Reflected', kind: 'reflection', mem_mib: 4096, report_id: 12 },
                ],
            } satisfies ExecutorCapacity),
        })
        render(<CapacityBadge service={asBadgeService(service)} />)

        await waitFor(() => {
            expect(screen.getByText('3+1/4')).toBeInTheDocument()
        })
        const node = screen.getByText('3+1/4').closest('[data-capacity-state]')!
        expect(node.getAttribute('data-capacity-state')).toBe('saturated')
        expect(node.getAttribute('title')).toMatch(/3 executing \+ 1 review = 4\/4 memory shares/)
        // Tooltip must name every holder.
        expect(node.getAttribute('title')).toMatch(/task 344/)
        expect(node.getAttribute('title')).toMatch(/reflection on 346/)
    })

    it('falls back to plain running/max when no reflection count is provided', async () => {
        // Backwards-compatible shape: legacy clients still get the old
        // "running/max" readout when the new fields are absent.
        const service = createMockService({
            fetchExecutorCapacity: vi.fn().mockResolvedValue({
                running: 2,
                max: 4,
                suggested_max: 4,
            } satisfies ExecutorCapacity),
        })
        render(<CapacityBadge service={asBadgeService(service)} />)

        await waitFor(() => {
            expect(screen.getByText('2/4')).toBeInTheDocument()
        })
    })
})