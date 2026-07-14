import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SpecDetailView } from './SpecDetailView'
import type { Spec, SpecComment, SpecStory } from '../types'
import { MemoryRouter } from 'react-router-dom'

const mockGetSpecStory = vi.fn()

// Mock the ServiceContext
vi.mock('../contexts/ServiceContext', () => ({
    useService: () => ({
        fetchSpecDetail: vi.fn(),
        getSpecStory: mockGetSpecStory,
    }),
}))

function makeComment(overrides: Partial<SpecComment> = {}): SpecComment {
    return {
        id: '1',
        specId: '42',
        authorEmail: 'claude+claude-opus-4-6@odin.agent',
        authorLabel: 'claude (claude-opus-4-6)',
        content: 'Completed in 45.0s\n\nExploring codebase...\nAnalyzed 12 files.\nPlan: 3 tasks.',
        attachments: [],
        commentType: 'planning',
        createdAt: new Date().toISOString(),
        ...overrides,
    }
}

function makeSpec(overrides: Partial<Spec> = {}): Spec {
    return {
        id: '42',
        title: 'Build login page',
        source: 'odin',
        content: 'Build a login page with email/password auth.',
        abandoned: false,
        boardId: 'b1',
        metadata: {},
        createdAt: new Date().toISOString(),
        tasks: [],
        taskCount: 0,
        comments: [],
        ...overrides,
    }
}

function renderWithRouter(ui: React.ReactElement) {
    return render(<MemoryRouter>{ui}</MemoryRouter>)
}

function renderAtPath(ui: React.ReactElement, path: string) {
    return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>)
}

function makeStory(): SpecStory {
    return {
        spec_id: 42,
        odin_id: 'sp_042',
        title: 'Build login page',
        task_count: 1,
        tasks: [{
            task_id: 1,
            title: 'Wire up login form',
            status: 'DONE',
            agent: 'claude',
            model: 'claude-sonnet-4-5',
            dispatched_at: '2026-07-01T10:00:00Z',
            duration_ms: 12000,
            tokens: { total: 500, input: 300, output: 200 },
            cost_usd: 0.5,
            redo_rounds: { count: 0, verdicts: [] },
            merge: null,
            latest_comment: null,
            depends_on: [],
            gaps: [],
        }],
    }
}

describe('SpecDetailView - Story tab', () => {
    const noop = () => {}

    it('renders the overview by default and the story timeline when ?view=story', async () => {
        mockGetSpecStory.mockResolvedValue(makeStory())
        const spec = makeSpec()
        renderAtPath(
            <SpecDetailView specId="42" spec={spec} onBack={noop} onTaskClick={noop} />,
            '/specs/42?view=story'
        )

        await waitFor(() => expect(screen.getByText('Wire up login form')).toBeInTheDocument())
        // Overview-only content (Cost Breakdown card) is not rendered in story view
        expect(screen.queryByText('Cost Breakdown')).not.toBeInTheDocument()
    })

    it('defaults to overview when no ?view param is present', () => {
        const spec = makeSpec()
        renderAtPath(
            <SpecDetailView specId="42" spec={spec} onBack={noop} onTaskClick={noop} />,
            '/specs/42'
        )
        expect(screen.getByText('Cost Breakdown')).toBeInTheDocument()
    })

    it('switching to the Story toggle renders the story timeline', async () => {
        mockGetSpecStory.mockResolvedValue(makeStory())
        const spec = makeSpec()
        renderAtPath(
            <SpecDetailView specId="42" spec={spec} onBack={noop} onTaskClick={noop} />,
            '/specs/42'
        )

        fireEvent.click(screen.getByRole('radio', { name: 'Story' }))

        await waitFor(() => expect(screen.getByText('Wire up login form')).toBeInTheDocument())
    })
})

describe('SpecDetailView - Planning Trace', () => {
    const noop = () => {}

    it('renders planning trace section when comments exist', () => {
        const spec = makeSpec({
            comments: [makeComment()],
        })
        renderWithRouter(
            <SpecDetailView
                specId="42"
                spec={spec}
                onBack={noop}
                onTaskClick={noop}
            />
        )

        // Header is always visible
        const header = screen.getByText(/Planning Trace/i)
        expect(header).toBeInTheDocument()

        // Expand the section
        fireEvent.click(header)

        // Now the comment content should be visible
        expect(screen.getByText(/Completed in 45.0s/)).toBeInTheDocument()
    })

    it('shows placeholder when no planning trace comments', () => {
        const spec = makeSpec({ comments: [] })
        renderWithRouter(
            <SpecDetailView
                specId="42"
                spec={spec}
                onBack={noop}
                onTaskClick={noop}
            />
        )

        // Expand the section
        const header = screen.getByText(/Planning Trace/i)
        fireEvent.click(header)

        expect(screen.getByText(/No planning trace/i)).toBeInTheDocument()
    })

    it('planning trace section is collapsible', () => {
        const spec = makeSpec({
            comments: [makeComment({
                content: 'Completed in 30s\n\nDetailed trace output.',
            })],
        })
        renderWithRouter(
            <SpecDetailView
                specId="42"
                spec={spec}
                onBack={noop}
                onTaskClick={noop}
            />
        )

        const header = screen.getByText(/Planning Trace/i)

        // Initially collapsed — content not visible
        expect(screen.queryByText(/Completed in 30s/)).not.toBeInTheDocument()

        // Expand
        fireEvent.click(header)
        expect(screen.getByText(/Completed in 30s/)).toBeInTheDocument()

        // Collapse again
        fireEvent.click(header)
        expect(screen.queryByText(/Completed in 30s/)).not.toBeInTheDocument()
    })
})
