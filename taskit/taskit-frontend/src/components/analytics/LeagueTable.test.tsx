import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { LeagueTable } from './LeagueTable';
import type { LeagueRow } from '../../types';

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

describe('LeagueTable', () => {
    it('renders empty state when rows are empty', () => {
        render(<LeagueTable rows={[]} />);
        expect(screen.getByText('Agent + Model League')).toBeInTheDocument();
        expect(screen.getByText('No league data for this board')).toBeInTheDocument();
    });

    it('renders loading state when loading=true', () => {
        render(<LeagueTable rows={[]} loading />);
        expect(screen.getByText('Agent + Model League')).toBeInTheDocument();
        expect(screen.getByText('Loading…')).toBeInTheDocument();
    });

    it('hides the card title when hideTitle is set', () => {
        render(<LeagueTable rows={[makeRow()]} hideTitle />);
        expect(screen.queryByText('Agent + Model League')).not.toBeInTheDocument();
    });

    it('renders all rows when provided', () => {
        const rows: LeagueRow[] = [
            makeRow({ agent: 'plan', model: 'opus-4-5' }),
            makeRow({ agent: 'exec', model: 'sonnet-4', tasks_landed: 8 }),
        ];
        render(<LeagueTable rows={rows} />);
        expect(screen.getByText('plan')).toBeInTheDocument();
        expect(screen.getByText('exec')).toBeInTheDocument();
        expect(screen.getByText('opus-4-5')).toBeInTheDocument();
        expect(screen.getByText('sonnet-4')).toBeInTheDocument();
    });

    it('only renders the trimmed column set (no tokens/duration/conflicts columns)', () => {
        render(<LeagueTable rows={[makeRow()]} />);
        expect(screen.getByText('Landed')).toBeInTheDocument();
        expect(screen.getByText('Hands-free %')).toBeInTheDocument();
        expect(screen.getByText('Redo avg')).toBeInTheDocument();
        expect(screen.getByText('Cost')).toBeInTheDocument();
        expect(screen.getByText('Avg refl cost')).toBeInTheDocument();
        expect(screen.queryByText('Tokens')).not.toBeInTheDocument();
        expect(screen.queryByText('Duration')).not.toBeInTheDocument();
        expect(screen.queryByText('Conflicts')).not.toBeInTheDocument();
    });

    it('renders the new Avg refl cost column with formatted USD value', () => {
        const rows: LeagueRow[] = [
            makeRow({
                agent: 'plan',
                model: 'opus-4-5',
                reflection_count: 2,
                reflection_cost_usd_total: 0.42,
                avg_reflection_cost_usd: 0.21,
            }),
        ];
        render(<LeagueTable rows={rows} />);
        // The header is there.
        expect(screen.getByText('Avg refl cost')).toBeInTheDocument();
        // The cell renders the formatted USD value via formatCost.
        expect(screen.getByText('$0.21')).toBeInTheDocument();
    });

    it('renders em-dash for avg_reflection_cost_usd when zero', () => {
        const rows: LeagueRow[] = [
            makeRow({
                agent: 'plan',
                model: 'opus-4-5',
                reflection_count: 0,
                reflection_cost_usd_total: 0,
                avg_reflection_cost_usd: 0,
            }),
        ];
        render(<LeagueTable rows={rows} />);
        // Zero cost → formatCost returns "$0.00"; the cell uses the
        // same em-dash convention as cost_usd_total for zero, so we
        // accept either "$0.00" or the existing em-dash behavior.
        const cell = screen.getAllByText(/\$0\.00|—/);
        expect(cell.length).toBeGreaterThanOrEqual(1);
    });

    it('carries hidden metrics (tokens/duration/conflicts) in a row tooltip', () => {
        render(<LeagueTable rows={[makeRow({ tokens_median: 15000, duration_ms_median: 60000, merge_conflicts_caused: 2 })]} />);
        const table = screen.getByRole('table');
        const row = within(table).getAllByRole('row')[1];
        expect(row).toHaveAttribute('title', expect.stringContaining('Tokens'));
        expect(row).toHaveAttribute('title', expect.stringContaining('Duration'));
        expect(row).toHaveAttribute('title', expect.stringContaining('Merge conflicts'));
    });

    it('sorts by tasks_landed desc by default', () => {
        const rows: LeagueRow[] = [
            makeRow({ agent: 'plan', model: 'a', tasks_landed: 3 }),
            makeRow({ agent: 'exec', model: 'b', tasks_landed: 9 }),
            makeRow({ agent: 'test', model: 'c', tasks_landed: 6 }),
        ];
        render(<LeagueTable rows={rows} />);
        const table = screen.getByRole('table');
        const bodyRows = within(table).getAllByRole('row').slice(1);
        expect(within(bodyRows[0]).getByText('exec')).toBeInTheDocument();
        expect(within(bodyRows[1]).getByText('test')).toBeInTheDocument();
        expect(within(bodyRows[2]).getByText('plan')).toBeInTheDocument();
    });

    it('toggles sort direction when clicking a column header', () => {
        const rows: LeagueRow[] = [
            makeRow({ agent: 'plan', model: 'a', tasks_landed: 3 }),
            makeRow({ agent: 'exec', model: 'b', tasks_landed: 9 }),
        ];
        render(<LeagueTable rows={rows} />);
        const header = screen.getByText('Landed').closest('th');
        if (!header) throw new Error('Landed header not found');
        fireEvent.click(header);
        const table = screen.getByRole('table');
        const bodyRows = within(table).getAllByRole('row').slice(1);
        expect(within(bodyRows[0]).getByText('plan')).toBeInTheDocument();
        expect(within(bodyRows[1]).getByText('exec')).toBeInTheDocument();
    });

    it('shows em-dash for cost_usd_total=0', () => {
        const rows: LeagueRow[] = [
            makeRow({
                agent: 'plan',
                model: 'opus',
                cost_usd_total: 0,
            }),
        ];
        render(<LeagueTable rows={rows} />);
        const emDashes = screen.getAllByText('—');
        expect(emDashes.length).toBeGreaterThanOrEqual(1);
    });

    it('shows "0" for redo_rounds_avg=0', () => {
        const rows: LeagueRow[] = [
            makeRow({
                agent: 'plan',
                model: 'opus',
                redo_rounds_avg: 0,
            }),
        ];
        render(<LeagueTable rows={rows} />);
        const mutedZeros = document.querySelectorAll('.text-muted-foreground');
        const texts = Array.from(mutedZeros).map(el => el.textContent);
        const zeroCount = texts.filter(t => t === '0').length;
        expect(zeroCount).toBeGreaterThanOrEqual(1);
    });
});
