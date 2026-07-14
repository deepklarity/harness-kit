import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TaskCard } from './TaskCard'
import type { Task } from '../types'
import { TooltipProvider } from '@/components/ui/tooltip'

function clearLocalStorage() {
    try {
        localStorage.clear()
    } catch {
        // ignore — jsdom might be unavailable
    }
}

function makeTask(overrides: Partial<Task> = {}): Task {
    return {
        id: 't1',
        name: 'Test task',
        idShort: 1,
        shortLink: 't1',
        boardId: 'b1',
        boardName: 'Test board',
        currentStatus: 'IN_PROGRESS',
        assignees: [],
        assigneeIds: [],
        createdAt: new Date().toISOString(),
        createdBy: 'user@example.com',
        mutations: [],
        comments: [],
        timeInStatuses: {},
        totalLifespanMs: 0,
        workTimeMs: 0,
        executingTimeMs: 0,
        metadata: {},
        ...overrides,
    }
}

function renderWithProviders(ui: React.ReactElement) {
    return render(<TooltipProvider>{ui}</TooltipProvider>)
}

describe('TaskCard — dispatch_blocked_reason banner', () => {
    const noop = () => {}

    it('renders no banner when metadata has no dispatch_blocked_reason', () => {
        clearLocalStorage()
        const task = makeTask({ metadata: {} })
        renderWithProviders(<TaskCard task={task} onClick={noop} />)
        expect(screen.queryByTestId('dispatch-blocked-banner')).not.toBeInTheDocument()
    })

    it('renders banner with "no_assignee" message when dispatch_blocked_reason is set', () => {
        clearLocalStorage()
        const task = makeTask({
            currentStatus: 'TODO',
            metadata: { dispatch_blocked_reason: 'no_assignee' },
        })
        renderWithProviders(<TaskCard task={task} onClick={noop} />)
        const banner = screen.getByTestId('dispatch-blocked-banner')
        expect(banner).toBeInTheDocument()
        expect(banner.textContent).toMatch(/no assignee/i)
    })

    it('renders banner with deps-not-complete message', () => {
        clearLocalStorage()
        const task = makeTask({
            metadata: { dispatch_blocked_reason: 'deps_not_complete' },
        })
        renderWithProviders(<TaskCard task={task} onClick={noop} />)
        const banner = screen.getByTestId('dispatch-blocked-banner')
        expect(banner.textContent).toMatch(/dependencies? (are )?not complete|waiting on (its )?dependencies|dependencies haven't/i)
    })

    it('renders banner with deps-blocked-failed message', () => {
        clearLocalStorage()
        const task = makeTask({
            metadata: { dispatch_blocked_reason: 'deps_blocked_failed' },
        })
        renderWithProviders(<TaskCard task={task} onClick={noop} />)
        const banner = screen.getByTestId('dispatch-blocked-banner')
        expect(banner.textContent).toMatch(/dependency failed|blocked by failed/i)
    })

    it('renders banner with no-worktree message', () => {
        clearLocalStorage()
        const task = makeTask({
            metadata: { dispatch_blocked_reason: 'no_worktree_no_optin' },
        })
        renderWithProviders(<TaskCard task={task} onClick={noop} />)
        const banner = screen.getByTestId('dispatch-blocked-banner')
        expect(banner.textContent).toMatch(/worktree/i)
    })

    it('falls back to raw reason code for unknown values', () => {
        clearLocalStorage()
        const task = makeTask({
            metadata: { dispatch_blocked_reason: 'some_future_reason' },
        })
        renderWithProviders(<TaskCard task={task} onClick={noop} />)
        const banner = screen.getByTestId('dispatch-blocked-banner')
        expect(banner.textContent).toMatch(/some_future_reason/)
    })
})

describe('TaskCard — needs-human indicator', () => {
    const noop = () => {}

    it('always renders the indicator element (inactive) for a normal task', () => {
        clearLocalStorage()
        const task = makeTask({ metadata: {} })
        renderWithProviders(<TaskCard task={task} onClick={noop} compact />)
        const indicator = screen.getByTestId('needs-human-indicator')
        expect(indicator).toBeInTheDocument()
        expect(indicator.getAttribute('data-active')).toBe('false')
        // no reason surfaced for an inactive indicator
        expect(indicator.getAttribute('title') || '').toBe('')
    })

    it('renders an active indicator with the merge reason on hover', () => {
        clearLocalStorage()
        const task = makeTask({ needsHuman: true, needsHumanReason: 'Merge needs human' })
        renderWithProviders(<TaskCard task={task} onClick={noop} compact />)
        const indicator = screen.getByTestId('needs-human-indicator')
        expect(indicator.getAttribute('data-active')).toBe('true')
        expect(indicator.getAttribute('title')).toMatch(/merge needs human/i)
        expect(indicator.getAttribute('aria-label')).toMatch(/merge needs human/i)
    })

    it('renders an active indicator for a pending question', () => {
        clearLocalStorage()
        const task = makeTask({ needsHuman: true, needsHumanReason: 'Question pending' })
        renderWithProviders(<TaskCard task={task} onClick={noop} compact />)
        const indicator = screen.getByTestId('needs-human-indicator')
        expect(indicator.getAttribute('data-active')).toBe('true')
        expect(indicator.getAttribute('title')).toMatch(/question pending/i)
    })

    it('renders an active indicator for review errored', () => {
        clearLocalStorage()
        const task = makeTask({ needsHuman: true, needsHumanReason: 'Review errored' })
        renderWithProviders(<TaskCard task={task} onClick={noop} />)
        const indicator = screen.getByTestId('needs-human-indicator')
        expect(indicator.getAttribute('data-active')).toBe('true')
        expect(indicator.getAttribute('title')).toMatch(/review errored/i)
    })

    it('stays inactive when needsHuman is absent', () => {
        clearLocalStorage()
        const task = makeTask({})
        renderWithProviders(<TaskCard task={task} onClick={noop} compact />)
        expect(screen.getByTestId('needs-human-indicator').getAttribute('data-active')).toBe('false')
    })
})
