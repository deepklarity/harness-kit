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
})