import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AgentTasksDrilldown } from './AgentTasksDrilldown';
import { ServiceContext } from '@/contexts/ServiceContext';
import type { HarnessTimeService } from '@/services/harness/HarnessTimeService';
import type { Board, Spec } from '../../types';

function makeService(overrides: Partial<HarnessTimeService> = {}): HarnessTimeService {
    return {
        name: 'test',
        baseUrl: 'http://localhost:8000',
        fetchSpecs: vi.fn().mockResolvedValue([] as Spec[]),
        ...overrides,
    } as unknown as HarnessTimeService;
}

const boards: Board[] = [
    { id: '5', name: 'Board Five', memberIds: [], tasks: [], members: [], lists: [], totalActions: 0 } as unknown as Board,
];

function makeTaskRow(opts: Partial<{
    task_id: number;
    title: string;
    status: string;
    board_id: number | null;
    board_name: string | null;
    spec_id: number | null;
    spec_title: string | null;
    agent_name: string;
    model_name: string;
}> = {}) {
    return {
        task_id: 1,
        title: 'task-1',
        status: 'DONE',
        board_id: 5,
        board_name: 'Board Five',
        spec_id: 100,
        spec_title: 'Spec A',
        agent_name: 'plan',
        model_name: 'opus-4-5',
        created_at: '2026-07-10T00:00:00Z',
        last_updated_at: '2026-07-10T01:00:00Z',
        ...opts,
    };
}

function makeSpecsResponse(tasks: ReturnType<typeof makeTaskRow>[]) {
    return { tasks, meta: { agent: 'plan', count: tasks.length } };
}

describe('AgentTasksDrilldown — spec filter selector', () => {
    let fetchMock: ReturnType<typeof vi.fn>;
    let fetchSpecsMock: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        fetchMock = vi.fn();
        globalThis.fetch = fetchMock as unknown as typeof fetch;
        fetchSpecsMock = vi.fn().mockResolvedValue([
            { id: '100', title: 'Spec A', boardId: '5', source: 'inline', content: '', abandoned: false, metadata: {}, createdAt: '2026-07-10T00:00:00Z', tasks: [], taskCount: 0 },
            { id: '200', title: 'Spec B', boardId: '5', source: 'inline', content: '', abandoned: false, metadata: {}, createdAt: '2026-07-10T00:00:00Z', tasks: [], taskCount: 0 },
        ]);
    });

    it('renders a Spec dropdown selector alongside the existing board/status filters', async () => {
        fetchMock.mockResolvedValue({
            ok: true,
            status: 200,
            json: () => Promise.resolve(makeSpecsResponse([makeTaskRow()])),
        });
        const service = makeService({ fetchSpecs: fetchSpecsMock as unknown as () => Promise<Spec[]> });
        render(
            <MemoryRouter initialEntries={['/stats?view=agent-tasks&agent=plan']}>
                <ServiceContext.Provider value={service}>
                    <AgentTasksDrilldown
                        agent="plan"
                        boards={boards}
                        boardFilter=""
                        searchParams={new URLSearchParams('view=agent-tasks&agent=plan')}
                        onTaskClick={vi.fn()}
                        onBack={vi.fn()}
                    />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        // The spec selector exists as a Select trigger. We assert by
        // the trigger label "Any spec" being present — all three
        // triggers default to "Any ..." when no filter is set.
        const anySpecTriggers = await screen.findAllByText('Any spec');
        expect(anySpecTriggers.length).toBeGreaterThanOrEqual(1);
        expect(screen.getByText('Any board')).toBeInTheDocument();
        expect(screen.getByText('Any status')).toBeInTheDocument();
    });

    it('fetches the spec list from service.fetchSpecs() so the selector has options', async () => {
        fetchMock.mockResolvedValue({
            ok: true,
            status: 200,
            json: () => Promise.resolve(makeSpecsResponse([makeTaskRow()])),
        });
        const service = makeService({ fetchSpecs: fetchSpecsMock as unknown as () => Promise<Spec[]> });
        render(
            <MemoryRouter initialEntries={['/stats?view=agent-tasks&agent=plan']}>
                <ServiceContext.Provider value={service}>
                    <AgentTasksDrilldown
                        agent="plan"
                        boards={boards}
                        boardFilter="5"
                        searchParams={new URLSearchParams('view=agent-tasks&agent=plan')}
                        onTaskClick={vi.fn()}
                        onBack={vi.fn()}
                    />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        await waitFor(() => {
            expect(fetchSpecsMock).toHaveBeenCalled();
        });
    });

    it('honors the ?spec=<id> URL parameter on initial load — issues request with spec_id', async () => {
        fetchMock.mockResolvedValue({
            ok: true,
            status: 200,
            json: () => Promise.resolve(makeSpecsResponse([makeTaskRow()])),
        });
        const service = makeService({ fetchSpecs: fetchSpecsMock as unknown as () => Promise<Spec[]> });
        const params = new URLSearchParams('view=agent-tasks&agent=plan&spec=100');
        render(
            <MemoryRouter initialEntries={['/stats?view=agent-tasks&agent=plan&spec=100']}>
                <ServiceContext.Provider value={service}>
                    <AgentTasksDrilldown
                        agent="plan"
                        boards={boards}
                        boardFilter="5"
                        searchParams={params}
                        onTaskClick={vi.fn()}
                        onBack={vi.fn()}
                    />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        await waitFor(() => {
            expect(fetchMock).toHaveBeenCalled();
        });
        // The first URL the component called must include spec_id=100
        const firstCallUrl = String(fetchMock.mock.calls[0][0]);
        expect(firstCallUrl).toContain('spec_id=100');
    });
});
