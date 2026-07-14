import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SpecStoryView } from './SpecStoryView'
import type { SpecStory, SpecStoryTask } from '../types'

const mockGetSpecStory = vi.fn()
const mockService: { getSpecStory?: typeof mockGetSpecStory } = { getSpecStory: mockGetSpecStory }

vi.mock('../contexts/ServiceContext', () => ({
    useService: () => mockService,
}))

function makeTask(overrides: Partial<SpecStoryTask> = {}): SpecStoryTask {
    return {
        task_id: 1,
        title: 'Build login page',
        status: 'DONE',
        agent: 'claude',
        model: 'claude-sonnet-4-5',
        dispatched_at: '2026-07-01T10:00:00Z',
        duration_ms: 45000,
        tokens: { total: 1500, input: 1000, output: 500 },
        cost_usd: 1.23,
        redo_rounds: { count: 0, verdicts: [] },
        merge: { status: 'merged', mode: 'clean merge', auto_resolved: false, conflicting_files: [], error: '', diff_stat: '' },
        latest_comment: { id: 1, comment_type: 'reflection', author: 'claude', created_at: '2026-07-01T10:05:00Z', headline: 'Reviewer verdict: PASS' },
        depends_on: [],
        gaps: [],
        ...overrides,
    }
}

function makeStory(tasks: SpecStoryTask[]): SpecStory {
    return { spec_id: 57, odin_id: 'sp_057', title: 'Wave 5', task_count: tasks.length, tasks }
}

describe('SpecStoryView', () => {
    afterEach(() => {
        mockService.getSpecStory = mockGetSpecStory
        mockGetSpecStory.mockReset()
    })

    it('shows a loading state before the story resolves', () => {
        mockGetSpecStory.mockReturnValue(new Promise(() => {}))
        render(<SpecStoryView specId="57" onTaskClick={() => {}} />)
        expect(screen.getByText(/Loading story/i)).toBeInTheDocument()
    })

    it('renders a headline row per task with plain-number cost and duration', async () => {
        mockGetSpecStory.mockResolvedValue(makeStory([makeTask()]))
        render(<SpecStoryView specId="57" onTaskClick={() => {}} />)

        await waitFor(() => expect(screen.getByText('Build login page')).toBeInTheDocument())
        expect(screen.getByText('DONE')).toBeInTheDocument()
        expect(screen.getByText('$1.23')).toBeInTheDocument()
        expect(screen.getByText('45s')).toBeInTheDocument()
    })

    it('expands to reveal the reviewer verdict comment and redo rounds', async () => {
        mockGetSpecStory.mockResolvedValue(makeStory([makeTask({
            redo_rounds: {
                count: 1,
                verdicts: [{ id: 9, verdict: 'NEEDS_WORK', reviewer_agent: 'claude', reviewer_model: 'claude-sonnet-4-5', created_at: '2026-07-01T09:00:00Z' }],
            },
        })]))
        render(<SpecStoryView specId="57" onTaskClick={() => {}} />)

        await waitFor(() => expect(screen.getByText('Build login page')).toBeInTheDocument())

        // Collapsed: expanded-only detail not shown yet
        expect(screen.queryByText(/Reviewer verdict: PASS/)).not.toBeInTheDocument()

        // Click the status text — part of the row, not the title button — to expand
        fireEvent.click(screen.getByText('DONE'))

        await waitFor(() => expect(screen.getByText(/Reviewer verdict: PASS/)).toBeInTheDocument())
        expect(screen.getByText('NEEDS_WORK')).toBeInTheDocument()
    })

    it('surfaces gaps instead of hiding a row with missing data', async () => {
        mockGetSpecStory.mockResolvedValue(makeStory([makeTask({
            merge: null,
            cost_usd: null,
            tokens: { total: 0, input: 0, output: 0 },
            latest_comment: null,
            gaps: ['no token/cost capture for this task', 'no merge record (pre-ledger merge or merge step skipped)'],
        })]))
        render(<SpecStoryView specId="57" onTaskClick={() => {}} />)

        await waitFor(() => expect(screen.getByText('Build login page')).toBeInTheDocument())
        fireEvent.click(screen.getByText('DONE'))

        await waitFor(() => expect(screen.getByText(/no token\/cost capture/)).toBeInTheDocument())
        expect(screen.getByText(/no merge record/)).toBeInTheDocument()
    })

    it('calls onTaskClick with the task id when the task title is clicked', async () => {
        const onTaskClick = vi.fn()
        mockGetSpecStory.mockResolvedValue(makeStory([makeTask({ task_id: 7 })]))
        render(<SpecStoryView specId="57" onTaskClick={onTaskClick} />)

        await waitFor(() => expect(screen.getByText('Build login page')).toBeInTheDocument())
        fireEvent.click(screen.getByText('Build login page'))

        expect(onTaskClick).toHaveBeenCalledWith('7')
    })

    it('shows an empty state when the spec has no tasks', async () => {
        mockGetSpecStory.mockResolvedValue(makeStory([]))
        render(<SpecStoryView specId="57" onTaskClick={() => {}} />)
        await waitFor(() => expect(screen.getByText(/No tasks yet/i)).toBeInTheDocument())
    })

    it('shows a fallback message when the service does not support the story endpoint', async () => {
        delete mockService.getSpecStory
        render(<SpecStoryView specId="57" onTaskClick={() => {}} />)
        await waitFor(() => expect(screen.getByText(/not supported/i)).toBeInTheDocument())
    })
})
