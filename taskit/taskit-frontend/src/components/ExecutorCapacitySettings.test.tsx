import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ExecutorCapacitySettings } from './ExecutorCapacitySettings'
import type { ExecutorMaxConcurrency } from '@/types'

function createMockService(overrides: Partial<{
    fetchExecutorMaxConcurrency: ReturnType<typeof vi.fn>
    setExecutorMaxConcurrency: ReturnType<typeof vi.fn>
}> = {}) {
    const fetchExecutorMaxConcurrency: ReturnType<typeof vi.fn> = overrides.fetchExecutorMaxConcurrency
        ?? vi.fn().mockResolvedValue({ value: 3, suggested_max: 4 } satisfies ExecutorMaxConcurrency)
    const setExecutorMaxConcurrency: ReturnType<typeof vi.fn> = overrides.setExecutorMaxConcurrency
        ?? vi.fn().mockResolvedValue({ value: 3, suggested_max: 4 } satisfies ExecutorMaxConcurrency)

    return { fetchExecutorMaxConcurrency, setExecutorMaxConcurrency }
}

type MockService = ReturnType<typeof createMockService>
type ComponentProps = React.ComponentProps<typeof ExecutorCapacitySettings>
type ComponentService = ComponentProps['service']

function asComponentService(s: MockService): ComponentService {
    return s as unknown as ComponentService
}

function renderComponent(service: MockService) {
    return render(<ExecutorCapacitySettings service={asComponentService(service)} />)
}

describe('ExecutorCapacitySettings', () => {
    beforeEach(() => {
        vi.clearAllMocks()
    })

    it('renders current value and suggestion after loading', async () => {
        const service = createMockService({
            fetchExecutorMaxConcurrency: vi.fn().mockResolvedValue({ value: 3, suggested_max: 4 } satisfies ExecutorMaxConcurrency),
        })
        renderComponent(service)

        await waitFor(() => {
            expect(screen.getByDisplayValue('3')).toBeInTheDocument()
        })
        expect(screen.getByText(/4 GB RAM per sandbox/i)).toBeInTheDocument()
    })

    it('rejects 0 by clamping to 1 and showing an error', async () => {
        const service = createMockService({
            fetchExecutorMaxConcurrency: vi.fn().mockResolvedValue({ value: 3, suggested_max: 4 } satisfies ExecutorMaxConcurrency),
            setExecutorMaxConcurrency: vi.fn().mockResolvedValue({ value: 1, suggested_max: 4 } satisfies ExecutorMaxConcurrency),
        })
        renderComponent(service)

        await waitFor(() => {
            expect(screen.getByDisplayValue('3')).toBeInTheDocument()
        })

        const input = screen.getByDisplayValue('3')
        fireEvent.change(input, { target: { value: '0' } })
        const saveBtn = screen.getByRole('button', { name: /save/i })
        fireEvent.click(saveBtn)

        await waitFor(() => {
            expect(screen.getByText(/must be at least 1/i)).toBeInTheDocument()
        })
        expect(service.setExecutorMaxConcurrency).not.toHaveBeenCalled()
    })

    it('rejects negative numbers', async () => {
        const service = createMockService({
            fetchExecutorMaxConcurrency: vi.fn().mockResolvedValue({ value: 3, suggested_max: 4 } satisfies ExecutorMaxConcurrency),
        })
        renderComponent(service)

        await waitFor(() => {
            expect(screen.getByDisplayValue('3')).toBeInTheDocument()
        })

        const input = screen.getByDisplayValue('3')
        fireEvent.change(input, { target: { value: '-5' } })
        const saveBtn = screen.getByRole('button', { name: /save/i })
        fireEvent.click(saveBtn)

        await waitFor(() => {
            expect(screen.getByText(/must be at least 1/i)).toBeInTheDocument()
        })
        expect(service.setExecutorMaxConcurrency).not.toHaveBeenCalled()
    })

    it('shows above-suggestion warning when value exceeds suggested_max but still saves', async () => {
        const service = createMockService({
            fetchExecutorMaxConcurrency: vi.fn().mockResolvedValue({ value: 3, suggested_max: 4 } satisfies ExecutorMaxConcurrency),
            setExecutorMaxConcurrency: vi.fn().mockResolvedValue({ value: 8, suggested_max: 4 } satisfies ExecutorMaxConcurrency),
        })
        renderComponent(service)

        await waitFor(() => {
            expect(screen.getByDisplayValue('3')).toBeInTheDocument()
        })

        const input = screen.getByDisplayValue('3')
        fireEvent.change(input, { target: { value: '8' } })

        await waitFor(() => {
            expect(screen.getByText(/above the suggested/i)).toBeInTheDocument()
        })

        const saveBtn = screen.getByRole('button', { name: /save/i })
        fireEvent.click(saveBtn)

        await waitFor(() => {
            expect(service.setExecutorMaxConcurrency).toHaveBeenCalledWith(8)
        })
    })

    it('saves a valid value below the suggestion without warning', async () => {
        const service = createMockService({
            fetchExecutorMaxConcurrency: vi.fn().mockResolvedValue({ value: 3, suggested_max: 4 } satisfies ExecutorMaxConcurrency),
            setExecutorMaxConcurrency: vi.fn().mockResolvedValue({ value: 2, suggested_max: 4 } satisfies ExecutorMaxConcurrency),
        })
        renderComponent(service)

        await waitFor(() => {
            expect(screen.getByDisplayValue('3')).toBeInTheDocument()
        })

        const input = screen.getByDisplayValue('3')
        fireEvent.change(input, { target: { value: '2' } })
        const saveBtn = screen.getByRole('button', { name: /save/i })
        fireEvent.click(saveBtn)

        await waitFor(() => {
            expect(service.setExecutorMaxConcurrency).toHaveBeenCalledWith(2)
        })
        expect(screen.queryByText(/above the suggested/i)).not.toBeInTheDocument()
    })
})