import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { SettingsView } from './SettingsView';
import type { Board, Member } from '../types';

const mockUpdateBoard = vi.fn().mockResolvedValue({});
const mockFetchRoutingConfig = vi.fn().mockResolvedValue({
    preference_order: ['glm', 'minimax', 'agy'],
    default_preference_order: ['glm', 'minimax', 'agy', 'codex', 'claude'],
    capability_escalate_after: 2,
    capability_escalation_enabled: true,
    capability_max_escalations: 3,
    escalation_enabled: true,
    escalation_tiers: [{ agent_name: 'glm', model_name: 'glm-4.6' }],
    failure_actions: {
        transport_error: { action: 'auto_requeue', max_retries: 2, backoff_seconds: 30, peer_fallback: true, description: 'Network/TLS/stream error — retry with same assignee after backoff.' },
        quota_exhaustion: { action: 'reassign', max_retries: 0, backoff_seconds: 0, peer_fallback: false, description: 'Quota exhausted — reassign to fallback provider on reflection.' },
        crash: { action: 'human', max_retries: 0, backoff_seconds: 0, peer_fallback: false, description: 'Subprocess crash / unhandled exception — operator must investigate.' },
    },
});
const mockService = {
    updateBoard: mockUpdateBoard,
    fetchForcedProviderStatus: vi.fn().mockResolvedValue({ enabled: false, provider: null, model: null }),
    fetchIdeOptions: vi.fn().mockResolvedValue({ detected_ides: [], preferred_ide_id: null }),
    fetchBoardsPage: vi.fn().mockResolvedValue({ results: [], count: 0 }),
    fetchBoardMembers: vi.fn().mockResolvedValue([]),
    saveIdeSettings: vi.fn().mockResolvedValue({}),
    setClaudeToken: vi.fn().mockResolvedValue({}),
    clearBoard: vi.fn().mockResolvedValue({}),
    initOdin: vi.fn().mockResolvedValue({}),
    fetchRoutingConfig: mockFetchRoutingConfig,
};
vi.mock('../contexts/ServiceContext', () => ({ useService: () => mockService }));

const changePassword = vi.fn().mockResolvedValue(undefined);
const logout = vi.fn();
let authState: { user: unknown; authEnabled: boolean; logout: typeof logout; changePassword: typeof changePassword };
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => authState }));

// NotificationSettings depends on NotificationContext — stub it.
vi.mock('./NotificationSettings', () => ({ NotificationSettings: () => <div data-testid="notif-settings" /> }));

const models = [
    { name: 'claude-haiku-4-5', output_price_per_1m_tokens: 5 },
    { name: 'claude-sonnet-4-6', output_price_per_1m_tokens: 15 },
];
const members: Member[] = [
    { id: '1', name: 'agent', availableModels: models } as unknown as Member,
];

function makeBoard(overrides: Partial<Board> = {}): Board {
    return {
        id: '5', name: 'Widget Factory', memberIds: [], tasks: [], members: [], lists: [],
        totalActions: 0, agents: [], skipReflection: false, reflectionModel: 'claude-sonnet-4-6',
        reflectionReviewStrategy: null, ...overrides,
    } as unknown as Board;
}

function renderSettings(board: Board | null, initialEntries = ['/settings']) {
    render(
        <MemoryRouter initialEntries={initialEntries}>
            <SettingsView
                members={members}
                currentBoard={board}
                onDataChange={vi.fn()}
                onCreateBoard={vi.fn()}
                onDeleteBoard={vi.fn().mockResolvedValue(undefined)}
                dark={false}
                onToggleDark={vi.fn()}
            />
        </MemoryRouter>
    );
}

beforeEach(() => {
    vi.clearAllMocks();
    authState = { user: null, authEnabled: false, logout, changePassword };
    // Radix Select relies on pointer-capture / scrollIntoView APIs jsdom lacks.
    Element.prototype.hasPointerCapture = vi.fn(() => false);
    Element.prototype.setPointerCapture = vi.fn();
    Element.prototype.releasePointerCapture = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
});

describe('SettingsView — reflection reviewer', () => {
    it('shows the current reviewer model for the board', () => {
        renderSettings(makeBoard());
        expect(screen.getByLabelText('Reviewer model')).toHaveTextContent('claude-sonnet-4-6');
    });

    it('reveals per-size reviewer selects and persists reflection_review_strategy when scaled by size', async () => {
        const user = userEvent.setup();
        renderSettings(makeBoard());

        const toggle = screen.getByLabelText('Scale reviewer by review size');
        expect(screen.queryByLabelText('small review model')).not.toBeInTheDocument();

        await user.click(toggle);
        // Buckets appear once enabled.
        expect(screen.getByLabelText('small review model')).toBeInTheDocument();
        expect(screen.getByLabelText('medium review model')).toBeInTheDocument();
        expect(screen.getByLabelText('large review model')).toBeInTheDocument();

        // Pick a model for the "small" bucket → writes the size-bucketed dict.
        await user.click(screen.getByLabelText('small review model'));
        await user.click(await screen.findByRole('option', { name: 'claude-haiku-4-5' }));

        await waitFor(() => {
            expect(mockUpdateBoard).toHaveBeenCalledWith('5', {
                reflection_review_strategy: { small: 'claude-haiku-4-5' },
            });
        });
    });

    it('starts enabled and pre-filled when the board already has a strategy', () => {
        renderSettings(makeBoard({ reflectionReviewStrategy: { small: 'claude-haiku-4-5', large: 'claude-sonnet-4-6' } } as Partial<Board>));
        expect(screen.getByLabelText('small review model')).toHaveTextContent('claude-haiku-4-5');
    });

    it('clears the strategy (empty dict) when scaling is turned off', async () => {
        const user = userEvent.setup();
        renderSettings(makeBoard({ reflectionReviewStrategy: { small: 'claude-haiku-4-5' } } as Partial<Board>));
        await user.click(screen.getByLabelText('Scale reviewer by review size'));
        await waitFor(() => {
            expect(mockUpdateBoard).toHaveBeenCalledWith('5', { reflection_review_strategy: {} });
        });
    });
});

describe('SettingsView — Routing', () => {
    it('renders the effective policy fetched from routing-config', async () => {
        renderSettings(makeBoard());

        expect(await screen.findByText('Standing preference order')).toBeInTheDocument();
        const orderList = screen.getByLabelText('Routing preference order');
        expect(within(orderList).getByText('glm')).toBeInTheDocument();
        expect(within(orderList).getByText('minimax')).toBeInTheDocument();
        expect(within(orderList).getByText('agy')).toBeInTheDocument();

        // Per-failure-class actions, grouped/labeled by action.
        expect(screen.getByText('transport_error')).toBeInTheDocument();
        expect(screen.getByText('reassign to fallback')).toBeInTheDocument();
        expect(screen.getByText('hold for human review')).toBeInTheDocument();

        // Escalation tiers (model_escalation_priority ladder).
        expect(screen.getByText('glm-4.6')).toBeInTheDocument();
    });

    it('reorders the standing preference order and persists routing_policy on the board', async () => {
        const user = userEvent.setup();
        renderSettings(makeBoard({ routingPolicy: { preference_order: ['glm', 'minimax', 'agy'] } } as Partial<Board>));

        await screen.findByText('Standing preference order');
        // Move "minimax" (index 1) up a slot → ['minimax', 'glm', 'agy'].
        await user.click(screen.getByLabelText('Move minimax up'));

        await waitFor(() => {
            expect(mockUpdateBoard).toHaveBeenCalledWith('5', {
                routing_policy: expect.objectContaining({ preference_order: ['minimax', 'glm', 'agy'] }),
            });
        });
    });

    it('toggles capability escalation and persists routing_policy', async () => {
        const user = userEvent.setup();
        renderSettings(makeBoard());

        const toggle = await screen.findByLabelText('Capability escalation enabled');
        expect(toggle).toBeChecked();
        await user.click(toggle);

        await waitFor(() => {
            expect(mockUpdateBoard).toHaveBeenCalledWith('5', {
                routing_policy: expect.objectContaining({ capability_escalation_enabled: false }),
            });
        });
    });

    it('edits the rejections-before-escalating threshold and persists it', async () => {
        renderSettings(makeBoard());

        const input = await screen.findByLabelText('Rejections before escalating');
        fireEvent.change(input, { target: { value: '3' } });

        await waitFor(() => {
            expect(mockUpdateBoard).toHaveBeenCalledWith('5', {
                routing_policy: expect.objectContaining({ capability_escalate_after: 3 }),
            });
        });
    });

    it('edits a failure class action and persists it onto failure_actions', async () => {
        const user = userEvent.setup();
        renderSettings(makeBoard());

        // The crash class defaults to "hold for human" — switch its action to
        // auto_requeue via the per-class action Select. This is the full
        // per-failure-class policy edit the reviewer required.
        const actionSelect = await screen.findByLabelText('crash action');
        await user.click(actionSelect);
        await user.click(await screen.findByRole('option', { name: /retry same agent/i }));

        await waitFor(() => {
            expect(mockUpdateBoard).toHaveBeenCalledWith('5', {
                routing_policy: expect.objectContaining({
                    failure_actions: expect.objectContaining({
                        crash: expect.objectContaining({ action: 'auto_requeue' }),
                    }),
                }),
            });
        });
    });

    it('edits a retry class max_retries and merges onto failure_actions', async () => {
        renderSettings(makeBoard());

        const input = await screen.findByLabelText('transport_error max retries');
        fireEvent.change(input, { target: { value: '5' } });

        await waitFor(() => {
            expect(mockUpdateBoard).toHaveBeenCalledWith('5', {
                routing_policy: expect.objectContaining({
                    failure_actions: expect.objectContaining({
                        transport_error: expect.objectContaining({ max_retries: 5 }),
                    }),
                }),
            });
        });
    });
});

describe('SettingsView — Account tab', () => {
    it('hides the Account tab when auth is disabled', () => {
        renderSettings(makeBoard(), ['/settings?tab=account']);
        expect(screen.queryByRole('tab', { name: 'Account' })).not.toBeInTheDocument();
        // falls back to the board tab, not a blank screen
        expect(screen.getByText('Reflection & Review')).toBeInTheDocument();
    });

    it('shows the Account tab with identity, password form and logout when auth is enabled', async () => {
        authState = { user: { email: 'op@example.com', displayName: 'Operator' }, authEnabled: true, logout, changePassword };
        renderSettings(makeBoard(), ['/settings?tab=account']);
        expect(screen.getByText('op@example.com')).toBeInTheDocument();
        expect(screen.getByLabelText('Current password')).toBeInTheDocument();
        await userEvent.setup().click(screen.getByRole('button', { name: /log out/i }));
        expect(logout).toHaveBeenCalled();
    });
});

// W10.4 — per-agent inline toggle in the boards table. PATCHes the
// roster endpoint, optimistically updates UI state, and (on failure)
// rolls back so the dialog never lies about an agent's real status.
describe('SettingsView — board agent roster toggle (W10.4)', () => {
    it('renders a switch per agent and PATCHes the board on toggle', async () => {
        const mockToggle = vi.fn().mockResolvedValue({ name: 'glm', enabled: false });
        mockService.toggleBoardAgent = mockToggle;

        mockService.fetchBoardsPage = vi.fn().mockResolvedValue({
            results: [makeBoard({
                agents: [
                    { name: 'claude', enabled: true, models: [] } as unknown as Board['agents'] extends infer A ? A : never,
                    { name: 'glm', enabled: true, models: [] } as unknown as Board['agents'] extends infer A ? A : never,
                ],
            })],
            count: 1,
        });

        const user = userEvent.setup();
        // Drive the Board tab so the toggle is in the rendered tree.
        renderSettings(null, ['/settings?tab=boards']);

        // Expand the single board row.
        const expandBtn = await screen.findByRole('button', { name: /view/i });
        await user.click(expandBtn);

        const glmSwitch = await screen.findByLabelText(/Disable glm on this board/);
        await user.click(glmSwitch);

        await waitFor(() => {
            expect(mockToggle).toHaveBeenCalledWith('5', 'glm', false);
        });
    });
});
