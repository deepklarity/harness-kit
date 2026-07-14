import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SimilarTasks } from './SimilarTasks'
import type { Task, TaskTwin, TaskEstimate, TaskActual } from '../types'

function makeTwin(overrides: Partial<TaskTwin> = {}): TaskTwin {
    return {
        task_id: 228,
        title: 'Routing: the agent league table',
        outcome: 'FAILED',
        tokens: 15954688,
        duration_ms: 2707000,
        redo_rounds: 0,
        agent: 'minimax',
        model: 'minimax-m1',
        score: 0.26,
        text_score: 0.2,
        structural_score: 0.06,
        proof_path: '.proof/task-228/proof.md',
        ...overrides,
    }
}

function makeTask(overrides: Partial<Task> = {}): Task {
    return {
        id: '233',
        name: 'Expose twins in modal',
        idShort: 233,
        shortLink: '233',
        boardId: 'b1',
        boardName: 'Wave 6',
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
        ...overrides,
    }
}

function renderWithRouter(ui: React.ReactElement) {
    return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('SimilarTasks — twins + quote section', () => {
    it('renders twin rows with id, title, outcome, tokens, duration, score, agent+model', () => {
        const twin = makeTwin()
        const task = makeTask({ twins: [twin] })
        renderWithRouter(<SimilarTasks task={task} />)

        const section = screen.getByTestId('similar-tasks')
        expect(section).toBeInTheDocument()
        expect(screen.getByText(/Routing: the agent league table/)).toBeInTheDocument()
        expect(screen.getByText(/FAILED/)).toBeInTheDocument()
        expect(screen.getByText(/16\.0M/)).toBeInTheDocument()
        expect(screen.getByText(/45\.1 min/)).toBeInTheDocument()
        expect(screen.getByText(/0\.26/)).toBeInTheDocument()
        expect(screen.getByText(/minimax/)).toBeInTheDocument()
        expect(screen.getByText(/minimax-m1/)).toBeInTheDocument()
    })

    it('twin row link opens in a NEW browser tab via ?taskId= URL', () => {
        const twin = makeTwin({ task_id: 228 })
        const task = makeTask({ twins: [twin] })
        renderWithRouter(<SimilarTasks task={task} />)

        const link = screen.getByTestId(`twin-link-${twin.task_id}`)
        expect(link.tagName).toBe('A')
        expect(link.getAttribute('href')).toContain('taskId=228')
        expect(link.getAttribute('target')).toBe('_blank')
        expect(link.getAttribute('rel')).toContain('noopener')
    })

    it('renders the quote line from the estimate', () => {
        const estimate: TaskEstimate = {
            confidence: 'high',
            twin_count: 3,
            tokens_median: 2500000,
            duration_ms_median: 900000,
        }
        const task = makeTask({ twins: [makeTwin()], estimate })
        renderWithRouter(<SimilarTasks task={task} />)

        expect(screen.getByTestId('twin-quote')).toBeInTheDocument()
        expect(screen.getByTestId('twin-quote').textContent).toMatch(/15\.0 min/)
        expect(screen.getByTestId('twin-quote').textContent).toMatch(/2\.5M tokens/)
        expect(screen.getByTestId('twin-quote').textContent).toMatch(/high/i)
    })

    it('shows actual vs estimate once finished', () => {
        const estimate: TaskEstimate = {
            confidence: 'medium',
            twin_count: 2,
            tokens_median: 5000000,
            duration_ms_median: 1800000,
        }
        const actual: TaskActual = {
            tokens: 1800000,
            duration_ms: 700000,
            transition: 'DONE',
        }
        const task = makeTask({ twins: [makeTwin()], estimate, actual })
        renderWithRouter(<SimilarTasks task={task} />)

        const actualLine = screen.getByTestId('twin-actual')
        expect(actualLine).toBeInTheDocument()
        expect(actualLine.textContent).toMatch(/1\.8M/)
        expect(actualLine.textContent).toMatch(/11\.7 min/)
    })

    it('task with no twins shows the section with an em-dash, not a hole', () => {
        const task = makeTask({ twins: [], estimate: null, actual: null })
        renderWithRouter(<SimilarTasks task={task} />)

        const section = screen.getByTestId('similar-tasks')
        expect(section).toBeInTheDocument()
        // No twin rows rendered.
        expect(screen.queryByTestId(/twin-link-/)).not.toBeInTheDocument()
        // Quote degrades to em-dash, never hidden.
        expect(screen.getByTestId('twin-quote').textContent).toMatch(/—/)
    })

    it('absent tokens/duration on a twin render em-dash', () => {
        const twin = makeTwin({ tokens: null, duration_ms: null })
        const task = makeTask({ twins: [twin] })
        renderWithRouter(<SimilarTasks task={task} />)

        const row = screen.getByTestId(`twin-row-${twin.task_id}`)
        expect(row.textContent).toMatch(/Tokens.*—/)
        expect(row.textContent).toMatch(/Duration.*—/)
    })
})
