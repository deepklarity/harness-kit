import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { StatsPage } from './StatsPage';
import { ServiceContext } from '@/contexts/ServiceContext';
import type { HarnessTimeService } from '@/services/harness/HarnessTimeService';
import type { AnalyticsCostSummary, Board } from '@/types';

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
        plan_cost: 0,
        merge_cost: 0,
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

function makeMockService() {
    return {
        fetchQuotaStatus: vi.fn().mockResolvedValue([]),
        fetchAnalytics: vi.fn().mockResolvedValue(emptySummary),
        fetchFactorySnapshot: vi.fn().mockResolvedValue(null),
        fetchInbox: vi.fn().mockResolvedValue(null),
        fetchLeague: vi.fn().mockResolvedValue({ rows: [], meta: { board_id: 0, task_count: 0, since_spec: null } }),
    } as unknown as HarnessTimeService;
}

const boards: Board[] = [
    { id: '5', name: 'Board Five', memberIds: [], tasks: [], members: [], lists: [], totalActions: 0 } as unknown as Board,
];

describe('StatsPage smoke test', () => {
    it('mounts and renders all four zones without crashing (All Boards, empty data)', async () => {
        const service = makeMockService();
        render(
            <MemoryRouter initialEntries={['/stats']}>
                <ServiceContext.Provider value={service}>
                    <StatsPage boards={boards} onTaskClick={vi.fn()} />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        expect(await screen.findByText('Now')).toBeInTheDocument();
        expect(screen.getByText('Throughput')).toBeInTheDocument();
        expect(screen.getByText('Cost & Agents')).toBeInTheDocument();
        expect(screen.getByText('Health')).toBeInTheDocument();
        // W12.4: NOW still board-scoped (live activity is one-board),
        // but the agent league renders on the All-Boards view too
        // (bundled in fetchAnalytics → data.league.rows).
        expect(screen.getByText('Select a board to view live activity')).toBeInTheDocument();
        // Empty-data league on All-Boards shows the LeagueTable empty
        // state ("No league data for this board"), not the placeholder.
        expect(screen.getByText('Agent league')).toBeInTheDocument();
    });

    it('mounts with a board selected and fetches factory/inbox/analytics data', async () => {
        const service = makeMockService();
        render(
            <MemoryRouter initialEntries={['/stats?board=5']}>
                <ServiceContext.Provider value={service}>
                    <StatsPage boards={boards} onTaskClick={vi.fn()} />
                </ServiceContext.Provider>
            </MemoryRouter>
        );

        expect(await screen.findByText('Now')).toBeInTheDocument();
        expect(service.fetchFactorySnapshot).toHaveBeenCalledWith('5');
        expect(service.fetchInbox).toHaveBeenCalledWith('5');
        // W12.4: league rows are bundled inside fetchAnalytics (single
        // round-trip). fetchLeague is no longer called by StatsPage
        // because the per-(agent, model) ranking is now part of the
        // cost-summary response — both single-board AND All-Boards views
        // share the same data shape.
        expect(service.fetchAnalytics).toHaveBeenCalled();
    });
});
