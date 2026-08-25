import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CreateTaskModal } from './CreateTaskModal';
import { ServiceContext } from '../contexts/ServiceContext';

if (typeof Element !== 'undefined') {
    if (!Element.prototype.hasPointerCapture) Element.prototype.hasPointerCapture = () => false;
    if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => {};
    if (!Element.prototype.releasePointerCapture) Element.prototype.releasePointerCapture = () => {};
    if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
}

const mockService = {
    fetchPresets: vi.fn().mockResolvedValue({ presets: [], categories: [] }),
    fetchBoardAgents: vi.fn().mockResolvedValue([]),
    getLabels: vi.fn().mockResolvedValue([]),
    createUser: vi.fn(),
    uploadScreenshots: vi.fn(),
};

const mockBoards = [
    {
        id: '1',
        name: 'Test Board',
        memberIds: ['101'],
        odinInitialized: false,
        isTrial: false,
        workingDir: '/tmp',
        timezone: 'UTC',
        claudeTokenConfigured: false,
        skipReflection: false,
        skipProof: false,
        allowProjectRootExecution: true,
    },
];

const mockUsers = [
    { id: 101, name: 'Alice', email: 'alice@example.com' },
];

describe('CreateTaskModal Schedule flow', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('renders Date button and inline Time input in Schedule mode', () => {
        render(
            <ServiceContext.Provider value={mockService as any}>
                <CreateTaskModal
                    boards={mockBoards as any}
                    users={mockUsers as any}
                    defaultCreateMode="SCHEDULE"
                    onClose={vi.fn()}
                    onCreate={vi.fn()}
                    onCreateSchedule={vi.fn()}
                />
            </ServiceContext.Provider>
        );

        // Verify Date label and Time label exist
        expect(screen.getByText('Date')).toBeInTheDocument();
        expect(screen.getByText('Time')).toBeInTheDocument();

        // Check Date button is rendered with calendar text
        const buttons = screen.getAllByRole('button');
        const dateBtn = buttons.find(b => /(Mon|Tue|Wed|Thu|Fri|Sat|Sun)/.test(b.textContent || ''));
        expect(dateBtn).toBeDefined();

        // Check inline time input exists
        const timeInput = document.querySelector('input[type="time"]');
        expect(timeInput).toBeInTheDocument();
    });

    it('opens Date popover when Date is clicked', () => {
        render(
            <ServiceContext.Provider value={mockService as any}>
                <CreateTaskModal
                    boards={mockBoards as any}
                    users={mockUsers as any}
                    defaultCreateMode="SCHEDULE"
                    onClose={vi.fn()}
                    onCreate={vi.fn()}
                    onCreateSchedule={vi.fn()}
                />
            </ServiceContext.Provider>
        );

        // Click Date button
        const buttons = screen.getAllByRole('button');
        const dateBtn = buttons.find(b => /(Mon|Tue|Wed|Thu|Fri|Sat|Sun)/.test(b.textContent || ''))!;
        fireEvent.click(dateBtn);

        // Calendar should render in popover
        const calendarElement = document.querySelector('table') || document.querySelector('[role="grid"]');
        expect(calendarElement).toBeInTheDocument();
    });

    it('scrolls to extra settings section smoothly when Extra settings is clicked', async () => {
        const scrollIntoViewSpy = vi.fn();
        Element.prototype.scrollIntoView = scrollIntoViewSpy;

        render(
            <ServiceContext.Provider value={mockService as any}>
                <CreateTaskModal
                    boards={mockBoards as any}
                    users={mockUsers as any}
                    onClose={vi.fn()}
                    onCreate={vi.fn()}
                />
            </ServiceContext.Provider>
        );

        const extraSettingsBtn = screen.getByText(/Extra settings/i);
        fireEvent.click(extraSettingsBtn);

        expect(screen.getByText('Skip reflection')).toBeInTheDocument();

        await vi.waitFor(() => {
            expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'nearest' });
        });
    });

    it('submits task with IN_PROGRESS status when allowProjectRootExecution is true and no dependencies selected', async () => {
        const onCreate = vi.fn().mockResolvedValue('101');
        render(
            <ServiceContext.Provider value={mockService as any}>
                <CreateTaskModal
                    boards={mockBoards as any}
                    users={mockUsers as any}
                    onClose={vi.fn()}
                    onCreate={onCreate}
                />
            </ServiceContext.Provider>
        );

        fireEvent.change(screen.getByPlaceholderText('Brief task title'), { target: { value: 'Task title' } });
        fireEvent.change(screen.getByPlaceholderText('What needs to be done?'), { target: { value: 'Task description' } });

        const submitBtn = screen.getByRole('button', { name: /^Create Task$/i });
        fireEvent.click(submitBtn);

        await vi.waitFor(() => {
            expect(onCreate).toHaveBeenCalledWith(
                '1', 'Task title', 'Task description', 'MEDIUM', 101,
                undefined, undefined, undefined, undefined, undefined, undefined, 'IN_PROGRESS'
            );
        });
    });

    it('submits task with IN_PROGRESS status when dependencies are selected', async () => {
        const onCreate = vi.fn().mockResolvedValue('102');
        const mockDependencyTasks = {
            '1': [
                { id: '201', idShort: 201, title: 'Parent task', currentStatus: 'IN_PROGRESS' } as any,
            ],
        };

        render(
            <ServiceContext.Provider value={mockService as any}>
                <CreateTaskModal
                    boards={mockBoards as any}
                    users={mockUsers as any}
                    dependencyTasksByBoard={mockDependencyTasks}
                    onClose={vi.fn()}
                    onCreate={onCreate}
                />
            </ServiceContext.Provider>
        );

        // Open dependency dialog and select dependency #201
        fireEvent.click(screen.getByRole('button', { name: /Add Dependencies/i }));
        const depCheckbox = screen.getByRole('checkbox');
        fireEvent.click(depCheckbox);
        fireEvent.click(screen.getByRole('button', { name: /Done/i }));

        fireEvent.change(screen.getByPlaceholderText('Brief task title'), { target: { value: 'Dependent task' } });
        fireEvent.change(screen.getByPlaceholderText('What needs to be done?'), { target: { value: 'Needs parent task' } });

        const submitBtn = screen.getByRole('button', { name: /^Create Task$/i });
        fireEvent.click(submitBtn);

        await vi.waitFor(() => {
            expect(onCreate).toHaveBeenCalledWith(
                '1', 'Dependent task', 'Needs parent task', 'MEDIUM', 101,
                undefined, undefined, undefined, ['201'], undefined, undefined, 'IN_PROGRESS'
            );
        });
    });
});
