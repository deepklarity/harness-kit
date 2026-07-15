import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AssignmentReason } from './AssignmentReason'
import type { Task, TaskAssignmentReason } from '../types'

function makeTask(overrides: Partial<Task> = {}): Task {
    return {
        id: '234',
        name: 'Surface routing WHY',
        idShort: 234,
        shortLink: '234',
        boardId: 'b1',
        boardName: 'Wave 6',
        currentStatus: 'TODO',
        assignees: [],
        assigneeIds: [],
        createdAt: new Date().toISOString(),
        createdBy: 'odin@example.com',
        mutations: [],
        comments: [],
        timeInStatuses: {},
        totalLifespanMs: 0,
        workTimeMs: 0,
        executingTimeMs: 0,
        ...overrides,
    }
}

function makeAR(overrides: Partial<TaskAssignmentReason> = {}): TaskAssignmentReason {
    return {
        agent: 'gemini',
        model: 'gemini-2.5-flash',
        rule: 'history',
        reason: 'Routed to gemini/gemini-2.5-flash (LOW tier, history: 90% success).',
        override: false,
        cheaper_alternatives: [
            { agent: 'qwen', model: 'qwen-coder', success_rate: 0.2, reason: 'success_rate 0.20 below 0.50' },
        ],
        twin_consensus: { agent: 'gemini', landed: 2, total: 3 },
        ...overrides,
    }
}

describe('AssignmentReason — WHY line + tooltip', () => {
    it('renders the one-line reason next to the assignee', () => {
        const task = makeTask({ metadata: { assignment_reason: makeAR() } })
        render(<AssignmentReason task={task} />)
        const row = screen.getByTestId('assignment-reason-row')
        expect(row).toBeInTheDocument()
        expect(row.textContent).toMatch(/Routed to gemini\/gemini-2\.5-flash/)
    })

    it('shows the rule label (Auto by default, Override when flagged)', () => {
        const autoTask = makeTask({ metadata: { assignment_reason: makeAR() } })
        const { rerender } = render(<AssignmentReason task={autoTask} />)
        expect(screen.getByTestId('assignment-reason-rule').textContent).toMatch(/Auto/)

        const overrideTask = makeTask({
            metadata: {
                assignment_reason: makeAR({
                    override: true,
                    override_by: 'alice@example.com',
                }),
            },
        })
        rerender(<AssignmentReason task={overrideTask} />)
        expect(screen.getByTestId('assignment-reason-rule').textContent).toMatch(/Override/)
    })

    it('hides the row when assignment_reason is absent (old tasks)', () => {
        const task = makeTask({ metadata: {} })
        render(<AssignmentReason task={task} />)
        // No reason sentence → the WHY line says nothing, so it does not render.
        expect(screen.queryByTestId('assignment-reason-row')).not.toBeInTheDocument()
    })

    it('hides the row when metadata is missing entirely', () => {
        const task = makeTask()
        render(<AssignmentReason task={task} />)
        expect(screen.queryByTestId('assignment-reason-row')).not.toBeInTheDocument()
    })

    it('hides the row when an override carries no reason text', () => {
        const task = makeTask({
            metadata: {
                assignment_reason: makeAR({ override: true, override_by: 'alice@example.com', reason: '' }),
            },
        })
        render(<AssignmentReason task={task} />)
        expect(screen.queryByTestId('assignment-reason-row')).not.toBeInTheDocument()
    })

    it('twin_consensus line appears in the tooltip when present', () => {
        const task = makeTask({
            metadata: {
                assignment_reason: makeAR({
                    twin_consensus: { agent: 'gemini', landed: 2, total: 3 },
                }),
            },
        })
        render(<AssignmentReason task={task} />)
        const tooltip = screen.getByTestId('assignment-reason-tooltip')
        expect(tooltip.textContent).toMatch(/2 of 3 twins landed with gemini/)
    })

    it('cheaper alternatives list appears in the tooltip when present', () => {
        const task = makeTask({
            metadata: {
                assignment_reason: makeAR({
                    cheaper_alternatives: [
                        { agent: 'qwen', model: 'qwen-coder', success_rate: 0.2, reason: 'success_rate 0.20 below 0.50' },
                        { agent: 'glm', model: 'GLM-4.7', success_rate: 0.3, reason: 'success_rate 0.30 below 0.50' },
                    ],
                }),
            },
        })
        render(<AssignmentReason task={task} />)
        const tooltip = screen.getByTestId('assignment-reason-tooltip')
        expect(tooltip.textContent).toMatch(/qwen/)
        expect(tooltip.textContent).toMatch(/glm/)
    })

    it('no tooltip content when cheaper_alternatives and twin_consensus are empty/null', () => {
        const task = makeTask({
            metadata: {
                assignment_reason: makeAR({
                    cheaper_alternatives: [],
                    twin_consensus: null,
                }),
            },
        })
        render(<AssignmentReason task={task} />)
        // Tooltip container exists but has only the reason — no extras.
        const tooltip = screen.getByTestId('assignment-reason-tooltip')
        expect(tooltip.textContent).not.toMatch(/twins landed/)
    })
})