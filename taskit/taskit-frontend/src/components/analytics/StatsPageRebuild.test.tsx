import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { StatsPage } from './StatsPage';
import { LeagueTable } from './LeagueTable';
import { ServiceContext } from '@/contexts/ServiceContext';
import type { HarnessTimeService } from '@/services/harness/HarnessTimeService';
import type { AnalyticsCostSummary, Board, LeagueRow } from '../../types';

const emptySummary: AnalyticsCostSummary = {
    summary_kpis: {
        total_spend: 0,
        total_tokens: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        total_cache_read_tokens: 0,
        task_count: 0,
        avg_cost_per_task: 0,
        reflection_cost: 0,
    },
    time_series: [],
    cost_by_model: [],
    cost_by_board: [],
    cost_by_agent: [],
    efficiency_metrics: {
        cache_hit_rate: 0,
        failure_cost: 0,
        avg_cost_per_task: 0,
        reflection_cost: 0,
        avg_tokens_per_task: 0,
        avg_duration_ms: 0,
        failed_task_count: 0,
        total_task_count: 0,
    },
    model_comparison: [],
    top_expensive_tasks: [],
    autonomy: {
        total_done: 0,
        agent_authored: 0,
        autonomy_rate: 0,
        operator_touches_total: 0,
        tasks_with_capture_gaps: 0,
        exec_duration_seconds: { min: 0, max: 0, p50: 0, p90: 0 },
        dispatch_to_done_seconds: { min: 0, max: 0, p50: 0, p90: 0 },
    },
    failure_class_breakdown: { buckets: [], total_failed: 0 },
    rework_breakdown: [],
    per_agent_rollup: [],
    merge_health: { total_attempts: 0, by_mode: [], by_outcome: [], lag_seconds: { min: 0, max: 0, p50: 0, p90: 0 } },
    review_health: { total_reviews: 0, by_verdict: [] },
    throughput_funnel: {
        total: 0,
        buckets: [
            { bucket: 'pass', count: 0, pct: 0 },
            { bucket: 'rework', count: 0, pct: 0 },
            { bucket: 'fail', count: 0, pct: 0 },
            { bucket: 'in_flight', count: 0, pct: 0 },
        ],
    },
    league: { rows: [], meta: { task_count: 0, board_id: null, since_spec: null, aggregate: false } },
    per_spec: [],
    scheduled_tasks: [],
    meta: { task_count: 0, granularity: 'day' },
};

function makeMockService(overrides: Partial<HarnessTimeService> = {}) {
    return {
        fetchQuotaStatus: vi.fn().mockResolvedValue([]),
        fetchAnalytics: vi.fn().mockResolvedValue(emptySummary),
        fetchFactorySnapshot: vi.fn().mockResolvedValue(null),
        fetchInbox: vi.fn().mockResolvedValue(null),
        fetchLeague: vi.fn().mockResolvedValue({ rows: [], meta: { board_id: 0, task_count: 0, since_spec: null } }),
        ...overrides,
    } as unknown as HarnessTimeService;
}

const boards: Board[] = [
    { id: '5', name: 'Board Five', memberIds: [], tasks: [], members: [], lists: [], totalActions: 0 } as unknown as Board,
    { id: '6', name: 'Board Six', memberIds: [], tasks: [], members: [], lists: [], totalActions: 0 } as unknown as Board,
];

describe('StatsPage funnel — sums to 100%', () => {
    it('renders all four funnel buckets with percentages that sum to 100', async () => {
        const summary = {
            ...emptySummary,
            throughput_funnel: {
                total: 100,
                buckets: [
                    { bucket: 'pass', count: 66, pct: 66 },
                    { bucket: 'rework', count: 15, pct: 15 },
                    { bucket: 'fail', count: 10, pct: 10 },
                    { bucket: 'in_flight', count: 9, pct: 9 },
                ],
            },
        };
        const service = makeMockService({
            fetchAnalytics: vi.fn().mockResolvedValue(summary),
        });
        render(
            <MemoryRouter initialEntries={['/stats?board=5']}>
                <ServiceContext.Provider value={service}>
                    <StatsPage boards={boards} onTaskClick={vi.fn()} />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        // Each bucket has a testid; pct% lives inside it.
        expect(await screen.findByTestId('funnel-bucket-pass')).toBeInTheDocument();
        const rework = screen.getByTestId('funnel-bucket-rework');
        const fail = screen.getByTestId('funnel-bucket-fail');
        const inFlight = screen.getByTestId('funnel-bucket-in_flight');
        const pass = screen.getByTestId('funnel-bucket-pass');

        // Bucket counts inside the bucket card.
        expect(within(pass).getByText('66')).toBeInTheDocument();
        expect(within(rework).getByText('15')).toBeInTheDocument();
        expect(within(fail).getByText('10')).toBeInTheDocument();
        expect(within(inFlight).getByText('9')).toBeInTheDocument();

        // Each bucket shows its pct label inside parentheses — the
        // user-visible invariant is that these four whole numbers
        // sum to 100 exactly.
        const pctFromBucket = (bucketEl: HTMLElement) => {
            const pctText = within(bucketEl).getByText(/\d+%/);
            return Number(pctText.textContent!.replace(/[()%]/g, ''));
        };
        const pctSum =
            pctFromBucket(pass) +
            pctFromBucket(rework) +
            pctFromBucket(fail) +
            pctFromBucket(inFlight);
        expect(pctSum).toBe(100);
    });

    it('shows a clear funnel total denominator next to the buckets', async () => {
        const summary = {
            ...emptySummary,
            throughput_funnel: {
                total: 200,
                buckets: [
                    { bucket: 'pass', count: 100, pct: 50 },
                    { bucket: 'rework', count: 60, pct: 30 },
                    { bucket: 'fail', count: 20, pct: 10 },
                    { bucket: 'in_flight', count: 20, pct: 10 },
                ],
            },
        };
        const service = makeMockService({
            fetchAnalytics: vi.fn().mockResolvedValue(summary),
        });
        render(
            <MemoryRouter initialEntries={['/stats?board=5']}>
                <ServiceContext.Provider value={service}>
                    <StatsPage boards={boards} onTaskClick={vi.fn()} />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        // The total denominator "200" appears in the throughput zone
        // so the operator can verify bucket percentages add to 100
        // against a visible number, not against an invisible sum.
        expect(await screen.findByText('200')).toBeInTheDocument();
    });
});

describe('StatsPage — All-boards view shows the agent league', () => {
    it('renders the league table on the All-Boards view (no placeholder)', async () => {
        const leagueRows: LeagueRow[] = [
            {
                agent: 'plan',
                model: 'opus-4-5',
                tasks_landed: 5,
                hands_free_count: 4,
                hands_free_pct: 0.8,
                redo_rounds_avg: 0,
                tokens_median: 15000,
                duration_ms_median: 60000,
                merge_conflicts_caused: 0,
                cost_usd_total: 1.23,
                reflection_count: 2,
                reflection_cost_usd_total: 0.42,
                avg_reflection_cost_usd: 0.21,
            },
        ];
        const summary = {
            ...emptySummary,
            league: { rows: leagueRows, meta: { task_count: 5, board_id: null, since_spec: null, aggregate: true } },
        };
        const service = makeMockService({
            fetchAnalytics: vi.fn().mockResolvedValue(summary),
        });
        render(
            <MemoryRouter initialEntries={['/stats']}>
                <ServiceContext.Provider value={service}>
                    <StatsPage boards={boards} onTaskClick={vi.fn()} />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        // The agent row is visible — no "Select a board" placeholder
        // blocks the All-Boards view anymore.
        expect(await screen.findByText('plan')).toBeInTheDocument();
        expect(screen.getByText('opus-4-5')).toBeInTheDocument();
        expect(screen.getByText('5')).toBeInTheDocument();
        expect(screen.queryByText(/Select a board to see the agent league/i)).not.toBeInTheDocument();
    });

    it('All-Boards fetchAnalytics returns league rows (no separate board fetch needed)', async () => {
        const leagueRows: LeagueRow[] = [
            { agent: 'plan', model: 'opus', tasks_landed: 3, hands_free_count: 3, hands_free_pct: 1, redo_rounds_avg: 0, tokens_median: 0, duration_ms_median: 0, merge_conflicts_caused: 0, cost_usd_total: 0, reflection_count: 0, reflection_cost_usd_total: 0, avg_reflection_cost_usd: 0 },
        ];
        const summary = {
            ...emptySummary,
            league: { rows: leagueRows, meta: { task_count: 3, board_id: null, since_spec: null, aggregate: true } },
        };
        const service = makeMockService({
            fetchAnalytics: vi.fn().mockResolvedValue(summary),
        });
        render(
            <MemoryRouter initialEntries={['/stats']}>
                <ServiceContext.Provider value={service}>
                    <StatsPage boards={boards} onTaskClick={vi.fn()} />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        // All-Boards view calls fetchAnalytics — league comes bundled
        // in the cost-summary response, so no fetchLeague call (which
        // requires a board id) needs to be made.
        expect(await screen.findByText('plan')).toBeInTheDocument();
        expect(service.fetchLeague).not.toHaveBeenCalled();
    });
});

describe('StatsPage — per-spec and scheduled-tasks sections', () => {
    it('renders per_spec rows when the backend returns them', async () => {
        const summary = {
            ...emptySummary,
            per_spec: [
                { odin_id: 'sp_a', title: 'Spec A', task_count: 5, done_count: 4, total_cost_usd: 0.5, created_at: '2026-07-10T00:00:00Z' },
                { odin_id: 'sp_b', title: 'Spec B', task_count: 3, done_count: 3, total_cost_usd: 0.2, created_at: '2026-07-09T00:00:00Z' },
            ],
        };
        const service = makeMockService({
            fetchAnalytics: vi.fn().mockResolvedValue(summary),
        });
        render(
            <MemoryRouter initialEntries={['/stats?board=5']}>
                <ServiceContext.Provider value={service}>
                    <StatsPage boards={boards} onTaskClick={vi.fn()} />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        expect(await screen.findByText('Spec A')).toBeInTheDocument();
        expect(screen.getByText('Spec B')).toBeInTheDocument();
        // Spec heading visible — a separate zone, not buried in the league.
        expect(screen.getByText(/Per spec/i)).toBeInTheDocument();
    });

    it('renders scheduled_tasks rows when the backend returns them', async () => {
        const summary = {
            ...emptySummary,
            scheduled_tasks: [
                { id: 1, template_title: 'Hourly sweep', template_kind: 'ONE_TIME', status: 'ACTIVE', run_count: 2, success_count: 1, failure_count: 1, next_run_at_utc: '2026-07-11T12:00:00Z' },
            ],
        };
        const service = makeMockService({
            fetchAnalytics: vi.fn().mockResolvedValue(summary),
        });
        render(
            <MemoryRouter initialEntries={['/stats?board=5']}>
                <ServiceContext.Provider value={service}>
                    <StatsPage boards={boards} onTaskClick={vi.fn()} />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        expect(await screen.findByText('Hourly sweep')).toBeInTheDocument();
        expect(screen.getByText(/Scheduled/i)).toBeInTheDocument();
    });
});

describe('LeagueTable — clickable agent row', () => {
    function makeRow(overrides: Partial<LeagueRow> = {}): LeagueRow {
        return {
            agent: 'plan',
            model: 'opus-4-5',
            tasks_landed: 5,
            hands_free_count: 4,
            hands_free_pct: 0.8,
            redo_rounds_avg: 0,
            tokens_median: 15000,
            duration_ms_median: 60000,
            merge_conflicts_caused: 0,
            cost_usd_total: 1.23,
            reflection_count: 0,
            reflection_cost_usd_total: 0,
            avg_reflection_cost_usd: 0,
            ...overrides,
        };
    }

    it('renders rows as interactive buttons (cursor-pointer + role=button)', () => {
        const onAgentClick = vi.fn();
        render(<LeagueTable rows={[makeRow()]} onAgentClick={onAgentClick} />);
        const row = screen.getByRole('button', { name: /plan/ });
        expect(row).toBeInTheDocument();
        expect(row).toHaveClass('cursor-pointer');
    });

    it('fires onAgentClick when an agent row is clicked', () => {
        const onAgentClick = vi.fn();
        render(
            <LeagueTable
                rows={[
                    makeRow({ agent: 'plan' }),
                    makeRow({ agent: 'exec', model: 'sonnet-4' }),
                ]}
                onAgentClick={onAgentClick}
            />,
        );
        const planRow = screen.getByRole('button', { name: /plan/ });
        fireEvent.click(planRow);
        expect(onAgentClick).toHaveBeenCalledWith('plan');
    });
});