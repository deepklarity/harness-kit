import { describe, it, expect } from 'vitest'
import { buildSpecHtml } from './specHtmlExport'
import type { Spec, Task } from '../types'

function makeTask(over: Partial<Task> = {}): Task {
  return {
    id: 't1', name: 'Task', title: 'Task', idShort: 1, shortLink: '',
    boardId: 'b1', boardName: '', currentStatus: 'DONE',
    assignees: ['codex'], assigneeIds: ['u1'],
    createdAt: '2026-06-05T00:00:00Z', createdBy: 'codex+gpt-5.4@odin.agent',
    mutations: [], comments: [],
    timeInStatuses: { EXECUTING: 228000 },
    totalLifespanMs: 252000, workTimeMs: 0, executingTimeMs: 228000,
    modelName: 'gpt-5.4',
    estimatedCostUsd: 0.38,
    usage: { input_tokens: 13100, output_tokens: 5320, total_tokens: 18420 },
    metadata: {},
    ...over,
  } as Task
}

function makeSpec(over: Partial<Spec> = {}): Spec {
  return {
    id: '142', title: 'Add CSV export', source: 'odin', content: '',
    abandoned: false, boardId: 'b1', metadata: { branch: 'feat/analytics-csv' },
    createdAt: '2026-06-05T00:00:00Z',
    tasks: [makeTask()], taskCount: 1,
    costSummary: {
      total_cost_usd: 1.71, reflection_cost_usd: 0, cost_by_model: {},
      total_tokens: 52140, total_input_tokens: 38300, total_output_tokens: 13840,
      tokens_by_model: {}, total_duration_ms: 1802000, tasks_with_unknown_cost: 0,
    },
    status: 'planning_complete',
    ...over,
  } as Spec
}

const FIXED = new Date('2026-06-08T14:14:07Z')

describe('buildSpecHtml', () => {
  it('produces a self-contained HTML document (doctype + inline style, no external refs)', () => {
    const html = buildSpecHtml(makeSpec(), { exportedAt: FIXED })
    expect(html).toContain('<!DOCTYPE html>')
    expect(html).toContain('<style>')
    expect(html).not.toMatch(/https?:\/\//)
    expect(html).not.toMatch(/<link\b/)
    expect(html).not.toMatch(/\bsrc=/)
    expect(html).not.toMatch(/<script/i)
  })

  it('renders spec header: title, status label, board, branch, totals (cost + in/out tokens)', () => {
    const html = buildSpecHtml(makeSpec(), { boardName: 'Analytics Revamp', exportedAt: FIXED })
    expect(html).toContain('Add CSV export')
    expect(html).toContain('Planning complete')
    expect(html).toContain('Analytics Revamp')
    expect(html).toContain('feat/analytics-csv')
    expect(html).toContain('$1.71')
    expect(html).toContain('38,300 in')
    expect(html).toContain('13,840 out')
  })

  it('renders a completed task with essentials + a clear outcome', () => {
    const html = buildSpecHtml(makeSpec({ tasks: [makeTask({ title: 'Write CSV serializer' })] }), { exportedAt: FIXED })
    expect(html).toContain('Write CSV serializer')
    expect(html).toContain('codex')
    expect(html).toContain('gpt-5.4')
    expect(html).toContain('13,100 in / 5,320 out')
    expect(html).toContain('$0.38')
    expect(html).toMatch(/Complete/)
    expect(html).toContain('Task 1:')
    expect(html).toContain('Execution time</dt><dd>3m 48s')
  })

  it('renders a failed task with reason/origin/type and expands it by default', () => {
    const failed = makeTask({
      title: 'Wire endpoint', currentStatus: 'FAILED',
      metadata: {
        last_failure_reason: 'pytest failed: ImportError compute_cost',
        last_failure_origin: 'execution',
        last_failure_type: 'test_failure',
      },
    })
    const html = buildSpecHtml(makeSpec({ tasks: [failed] }), { exportedAt: FIXED })
    expect(html).toMatch(/Failed/)
    expect(html).toContain('pytest failed: ImportError compute_cost')
    expect(html).toContain('execution')
    expect(html).toContain('test_failure')
    expect(html).toMatch(/<details[^>]*\bopen\b/)
  })

  it('handles a FAILED task with no failure metadata without crashing', () => {
    const html = buildSpecHtml(makeSpec({ tasks: [makeTask({ currentStatus: 'FAILED', metadata: {} })] }), { exportedAt: FIXED })
    expect(html).toMatch(/Failed/)
    expect(html).toMatch(/not recorded/i)
  })

  it('shows placeholders for missing data and "In progress" for non-terminal tasks', () => {
    const bare = makeTask({
      currentStatus: 'IN_PROGRESS', assignees: [], modelName: undefined,
      estimatedCostUsd: null, usage: null, timeInStatuses: {},
    })
    const html = buildSpecHtml(makeSpec({ tasks: [bare] }), { exportedAt: FIXED })
    expect(html).toContain('—')
    expect(html).toMatch(/In progress/i)
  })

  it('treats TESTING as complete, not "In progress"', () => {
    const html = buildSpecHtml(makeSpec({ tasks: [makeTask({ currentStatus: 'TESTING' })] }), { exportedAt: FIXED })
    expect(html).toMatch(/Complete/i)
    expect(html).toMatch(/final testing is underway/i)
    expect(html).not.toMatch(/In progress/i)
  })

  it('labels REVIEW as "Under review" (not work-complete, not "In progress")', () => {
    const html = buildSpecHtml(makeSpec({ tasks: [makeTask({ currentStatus: 'REVIEW' })] }), { exportedAt: FIXED })
    expect(html).toMatch(/Under review/i)
    expect(html).not.toMatch(/Work complete/i)
    expect(html).not.toMatch(/In progress/i)
  })

  it('labels not-started states distinctly (To do / Backlog), not "In progress"', () => {
    const todo = buildSpecHtml(makeSpec({ tasks: [makeTask({ currentStatus: 'TODO' })] }), { exportedAt: FIXED })
    expect(todo).toMatch(/To do/i)
    expect(todo).not.toMatch(/In progress/i)
    const backlog = buildSpecHtml(makeSpec({ tasks: [makeTask({ currentStatus: 'BACKLOG' })] }), { exportedAt: FIXED })
    expect(backlog).toMatch(/Backlog/i)
    expect(backlog).not.toMatch(/In progress/i)
  })

  it('adds a Read more/Show less toggle only for long descriptions', () => {
    const longHtml = buildSpecHtml(makeSpec({ tasks: [makeTask({ description: 'x'.repeat(300) })] }), { exportedAt: FIXED })
    expect(longHtml).toContain('type="checkbox"')
    expect(longHtml).toMatch(/Read more/)
    expect(longHtml).toMatch(/Show less/)

    const shortHtml = buildSpecHtml(makeSpec({ tasks: [makeTask({ description: 'a short description' })] }), { exportedAt: FIXED })
    expect(shortHtml).toContain('a short description')
    expect(shortHtml).not.toContain('type="checkbox"')
    expect(shortHtml).not.toMatch(/Read more/)
  })

  it('renders a zero-task spec without crashing', () => {
    const html = buildSpecHtml(makeSpec({ tasks: [], taskCount: 0 }), { exportedAt: FIXED })
    expect(html).toContain('Tasks (0)')
  })

  it('escapes HTML in user-supplied text (no injection)', () => {
    const evil = makeTask({
      title: '<script>alert(1)</script>',
      description: 'a & b < c > d',
      currentStatus: 'FAILED',
      metadata: { last_failure_reason: '<img src=x onerror=alert(1)>' },
    })
    const html = buildSpecHtml(makeSpec({ tasks: [evil] }), { exportedAt: FIXED })
    expect(html).not.toContain('<script>alert(1)</script>')
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<img src=x onerror=alert(1)>')
    expect(html).toContain('&lt;img')
  })

  it('includes a per-status time breakdown in a hover tooltip', () => {
    const t = makeTask({ timeInStatuses: { EXECUTING: 228000, TESTING: 18000, REVIEW: 6000 } })
    const html = buildSpecHtml(makeSpec({ tasks: [t] }), { exportedAt: FIXED })
    expect(html).toMatch(/title="[^"]*Executing[^"]*Testing[^"]*Review[^"]*"/)
  })

  it('summarizes the current outcome and flags a failed dependency path', () => {
    const failed = makeTask({ id: 't1', idShort: 1, title: 'Prepare API', currentStatus: 'FAILED' })
    const blocked = makeTask({ id: 't2', idShort: 2, title: 'Export report', currentStatus: 'TODO', dependsOn: ['t1'] })
    const html = buildSpecHtml(makeSpec({ tasks: [failed, blocked], taskCount: 2 }), { exportedAt: FIXED })
    expect(html).toContain('At a glance')
    expect(html).toContain('0 completed · 1 need attention · 1 still open')
    expect(html).toContain('Depends on')
    expect(html).toContain('Failed — blocks this task')
    expect(html).toContain('Prepare API')
  })
})
