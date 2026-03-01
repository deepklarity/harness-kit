import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SpecDetailView } from './SpecDetailView'
import type { Spec, SpecComment } from '../types'
import { MemoryRouter } from 'react-router-dom'

// Mock the ServiceContext
vi.mock('../contexts/ServiceContext', () => ({
    useService: () => ({
        fetchSpecDetail: vi.fn(),
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
