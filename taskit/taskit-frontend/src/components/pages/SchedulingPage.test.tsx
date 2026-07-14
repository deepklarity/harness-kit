import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ServiceContext } from '@/contexts/ServiceContext';
import type { HarnessTimeService } from '@/services/harness/HarnessTimeService';
import type { TaskSchedule } from '@/types';
import { SchedulingPage } from './SchedulingPage';

const toastMock = vi.fn();

vi.mock('@/hooks/use-toast', () => ({
    useToast: () => ({ toast: toastMock }),
}));

function makeSchedule(overrides: Partial<TaskSchedule> = {}): TaskSchedule {
    return {
        id: 42,
        board_id: 1,
        kind: 'RECURRING',
        status: 'ACTIVE',
        timezone: 'UTC',
        starts_at_local: '2026-01-01T09:00:00Z',
        starts_at_utc: '2026-01-01T09:00:00Z',
        next_run_at_utc: '2026-01-01T09:00:00Z',
        recurrence_rule: { freq: 'DAILY', interval: 1 },
        materialized_task_id: null,
        last_released_run_id: null,
        paused_at: null,
        canceled_at: null,
        completed_at: null,
        created_by: 'admin@example.com',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        template: {
            title: 'Daily health check',
            description: 'Run health probes',
            priority: 'MEDIUM',
        },
        runs: [],
        ...overrides,
    };
}

function renderPage(service: Partial<HarnessTimeService>, schedule: TaskSchedule) {
    const fetchSchedules = vi.fn().mockResolvedValue({ count: 1, next: null, previous: null, results: [schedule] });
    const fullService = { fetchSchedules, ...service } as unknown as HarnessTimeService;
    return render(
        <ServiceContext.Provider value={fullService}>
            <MemoryRouter>
                <SchedulingPage selectedBoard="1" onTaskClick={vi.fn()} />
            </MemoryRouter>
        </ServiceContext.Provider>
    );
}

describe('SchedulingPage — Run now', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('renders a Run now button on each active schedule row', async () => {
        const runScheduleNow = vi.fn();
        renderPage({ runScheduleNow }, makeSchedule({ id: 1, status: 'ACTIVE' }));

        await waitFor(() => expect(screen.getByTestId('run-now-button')).toBeInTheDocument());
    });

    it('does not render Run now on canceled schedules', async () => {
        renderPage({ runScheduleNow: vi.fn() }, makeSchedule({ id: 2, status: 'CANCELED' }));
        await waitFor(() => expect(screen.getAllByText(/canceled/i).length).toBeGreaterThan(0));
        expect(screen.queryByTestId('run-now-button')).not.toBeInTheDocument();
    });

    it('opens a confirm dialog when Run now is clicked (no POST until confirmed)', async () => {
        const runScheduleNow = vi.fn().mockResolvedValue({ task_id: 99, run_id: 1 });
        renderPage({ runScheduleNow }, makeSchedule());

        const runButton = await screen.findByTestId('run-now-button');
        fireEvent.click(runButton);

        expect(runScheduleNow).not.toHaveBeenCalled();
        await waitFor(() => expect(screen.getByText(/run this schedule now/i)).toBeInTheDocument());
    });

    it('confirm triggers service call, refreshes the list, and shows success toast with action link', async () => {
        const runScheduleNow = vi.fn().mockResolvedValue({ task_id: 99, run_id: 7 });
        const fetchSchedules = vi.fn().mockResolvedValue({ count: 1, next: null, previous: null, results: [makeSchedule()] });
        const onTaskClick = vi.fn();

        render(
            <ServiceContext.Provider value={{ runScheduleNow, fetchSchedules } as unknown as HarnessTimeService}>
                <MemoryRouter>
                    <SchedulingPage selectedBoard="1" onTaskClick={onTaskClick} />
                </MemoryRouter>
            </ServiceContext.Provider>
        );

        const runButton = await screen.findByTestId('run-now-button');
        fireEvent.click(runButton);

        const confirm = await screen.findByRole('button', { name: /^run now$/i });
        fireEvent.click(confirm);

        await waitFor(() => expect(runScheduleNow).toHaveBeenCalledWith('42'));
        await waitFor(() => expect(fetchSchedules).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(toastMock).toHaveBeenCalled());
        const lastToast = toastMock.mock.calls[toastMock.mock.calls.length - 1][0];
        expect(String(lastToast.title ?? '').toLowerCase()).toContain('run');
        expect(lastToast.action).toBeDefined();
        expect(lastToast.description).toBeDefined();
    });

    it('disables the Run now button while its own request is in flight (double-click guard)', async () => {
        let resolveRun!: (value: unknown) => void;
        const runScheduleNow = vi.fn().mockImplementation(
            () => new Promise((resolve) => { resolveRun = resolve; })
        );
        renderPage({ runScheduleNow }, makeSchedule());

        const runButton = await screen.findByTestId('run-now-button');
        fireEvent.click(runButton);

        const confirm = await screen.findByRole('button', { name: /^run now$/i });
        fireEvent.click(confirm);

        // After confirm, the source button should be disabled while the request is in flight.
        await waitFor(() => expect(screen.getByTestId('run-now-button')).toBeDisabled());

        // Re-clicking the (disabled) button must NOT trigger another POST.
        fireEvent.click(screen.getByTestId('run-now-button'));
        expect(runScheduleNow).toHaveBeenCalledTimes(1);

        // Resolve so the test cleans up.
        resolveRun({ task_id: 1, run_id: 1 });
        await waitFor(() => expect(screen.getByTestId('run-now-button')).not.toBeDisabled());
    });

    it('surfaces backend 4xx as a non-crashing error toast', async () => {
        const runScheduleNow = vi.fn().mockRejectedValue(
            new Error('Schedule 42 already has an active run (task 5, status IN_PROGRESS). Wait for it to finish before running again.')
        );
        renderPage({ runScheduleNow }, makeSchedule());

        const runButton = await screen.findByTestId('run-now-button');
        fireEvent.click(runButton);
        const confirm = await screen.findByRole('button', { name: /^run now$/i });
        fireEvent.click(confirm);

        await waitFor(() => expect(runScheduleNow).toHaveBeenCalled());
        await waitFor(() =>
            expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ variant: 'destructive' }))
        );
    });
});