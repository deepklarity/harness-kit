import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppHeader, PRIMARY_VIEWS, INSIGHTS_VIEWS, VIEW_ROUTES } from './AppHeader';
import type { Board, ViewMode } from '../types';

// NotificationBell pulls in NotificationContext — stub it out for isolation.
vi.mock('./NotificationBell', () => ({ NotificationBell: () => <div data-testid="notif-bell" /> }));

let authState = { user: null as unknown, authEnabled: false, logout: vi.fn() };
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => authState,
}));

const boards: Board[] = [
    { id: '5', name: 'Widget Factory', memberIds: [], tasks: [], members: [], lists: [], totalActions: 0 } as unknown as Board,
    { id: '7', name: 'Other Board', memberIds: [], tasks: [], members: [], lists: [], totalActions: 0 } as unknown as Board,
];

function renderHeader(overrides: Partial<Parameters<typeof AppHeader>[0]> = {}) {
    const props = {
        boards,
        selectedBoard: '5',
        currentBoard: boards[0],
        isAllBoards: false,
        viewMode: 'board' as ViewMode,
        onBoardChange: vi.fn(),
        onNavChange: vi.fn(),
        onCreateTask: vi.fn(),
        onCreateSpec: vi.fn(),
        onCreateBoard: vi.fn(),
        onNavigateHome: vi.fn(),
        onOpenProcessMonitor: vi.fn(),
        onOpenCommandPalette: vi.fn(),
        ...overrides,
    };
    render(
        <MemoryRouter>
            <TooltipProvider>
                <AppHeader {...props} />
            </TooltipProvider>
        </MemoryRouter>
    );
    return props;
}

describe('AppHeader navigation', () => {
    it('renders every primary work surface as a reachable link (no orphans)', () => {
        renderHeader();
        // Board, Specs, Scheduling must all be linked.
        for (const id of PRIMARY_VIEWS) {
            const route = VIEW_ROUTES.find(r => r.id === id)!;
            const link = screen.getByRole('link', { name: new RegExp(route.label, 'i') });
            expect(link).toHaveAttribute('href', `${route.path}?board=5`);
        }
    });

    it('links Providers and Settings with accessible labels and board context', () => {
        renderHeader();
        const providers = screen.getByRole('link', { name: 'Providers' });
        expect(providers).toHaveAttribute('href', '/providers?board=5');
        const settings = screen.getByRole('link', { name: 'Settings' });
        expect(settings).toHaveAttribute('href', '/settings?board=5');
    });

    it('marks the active primary tab with aria-current', () => {
        renderHeader({ viewMode: 'specs' });
        const specs = screen.getByRole('link', { name: /Specs/i });
        expect(specs).toHaveAttribute('aria-current', 'page');
        const board = screen.getByRole('link', { name: /Board/i });
        expect(board).not.toHaveAttribute('aria-current');
    });

    it('exposes Stats and Reflections through the Insights dropdown', async () => {
        const user = userEvent.setup();
        const props = renderHeader();
        await user.click(screen.getByRole('button', { name: /Insights/i }));
        for (const id of INSIGHTS_VIEWS) {
            const route = VIEW_ROUTES.find(r => r.id === id)!;
            const item = await screen.findByRole('button', { name: new RegExp(route.label, 'i') });
            await user.click(item);
            expect(props.onNavChange).toHaveBeenCalledWith(id);
            // reopen for the next item (closes on selection)
            await user.click(screen.getByRole('button', { name: /Insights|Stats|Reflections/i }));
        }
    });

    it('surfaces the active insight label on the dropdown trigger', () => {
        renderHeader({ viewMode: 'overview' });
        expect(screen.getByRole('button', { name: /Stats/i })).toBeInTheDocument();
    });

    it('drops the board query when viewing All Boards', () => {
        renderHeader({ selectedBoard: '__ALL__', isAllBoards: true, currentBoard: null });
        const board = screen.getByRole('link', { name: /Board/i });
        expect(board).toHaveAttribute('href', '/board');
    });

    it('shows the logout control only when auth is enabled', () => {
        renderHeader();
        expect(screen.queryByRole('button', { name: /log ?out/i })).not.toBeInTheDocument();
    });
});
