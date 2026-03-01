import { describe, it, expect } from 'vitest'
import type { Task } from '../types'
import {
  detectStuckTasks,
  detectFailedChains,
  detectUnsatisfiedDeps,
  computeSpecSummary,
  parseMetricsFromComment,
  classifyEdgeByExecution,
} from './diagnostics'

// ─── Helpers ───

function makeTask(overrides: Partial<Task> & { id: string; currentStatus: string }): Task {
  return {
    name: overrides.title || `Task ${overrides.id}`,
    title: `Task ${overrides.id}`,
    idShort: Number(overrides.id),
    shortLink: overrides.id,
    boardId: '1',
    boardName: 'Test Board',
    assignees: [],
    assigneeIds: [],
    createdAt: new Date(Date.now() - 3600_000).toISOString(),
    createdBy: 'test@test.com',
    mutations: [],
    comments: [],
    timeInStatuses: {},
    totalLifespanMs: 3600_000,
    workTimeMs: 0,
    executingTimeMs: 0,
    ...overrides,
  }
}

// ─── detectStuckTasks ───

describe('detectStuckTasks', () => {
  it('detects tasks stuck in IN_PROGRESS with no recent activity', () => {
    const tasks = [
      makeTask({
        id: '1',
        currentStatus: 'IN_PROGRESS',
        mutations: [{
          id: '1', type: 'status_change', date: new Date(Date.now() - 900_000).toISOString(),
          timestamp: Date.now() - 900_000, actor: 'alice', actorId: 'alice',
          description: 'status changed', fromStatus: 'TODO', toStatus: 'IN_PROGRESS',
        }],
      }),
    ]

    const problems = detectStuckTasks(tasks, 600_000)
    expect(problems).toHaveLength(1)
    expect(problems[0].type).toBe('stuck')
    expect(problems[0].taskId).toBe('1')
  })

  it('does not flag recently active tasks', () => {
    const tasks = [
      makeTask({
        id: '1',
        currentStatus: 'IN_PROGRESS',
        mutations: [{
          id: '1', type: 'status_change', date: new Date().toISOString(),
          timestamp: Date.now(), actor: 'alice', actorId: 'alice',
          description: 'status changed', fromStatus: 'TODO', toStatus: 'IN_PROGRESS',
        }],
      }),
    ]

    const problems = detectStuckTasks(tasks, 600_000)
    expect(problems).toHaveLength(0)
  })

  it('ignores tasks not in active status', () => {
    const tasks = [
      makeTask({ id: '1', currentStatus: 'TODO' }),
      makeTask({ id: '2', currentStatus: 'DONE' }),
    ]

    const problems = detectStuckTasks(tasks, 600_000)
    expect(problems).toHaveLength(0)
  })
})

// ─── detectFailedChains ───

describe('detectFailedChains', () => {
  it('detects children blocked by failed parents', () => {
    const tasks = [
      makeTask({ id: '1', currentStatus: 'FAILED' }),
      makeTask({ id: '2', currentStatus: 'TODO', dependsOn: ['1'] }),
    ]

    const problems = detectFailedChains(tasks)
    expect(problems).toHaveLength(1)
    expect(problems[0].type).toBe('failed_chain')
    expect(problems[0].taskId).toBe('2')
  })

  it('does not flag if no failed tasks', () => {
    const tasks = [
      makeTask({ id: '1', currentStatus: 'DONE' }),
      makeTask({ id: '2', currentStatus: 'TODO', dependsOn: ['1'] }),
    ]

    const problems = detectFailedChains(tasks)
    expect(problems).toHaveLength(0)
  })

  it('does not flag already-failed children', () => {
    const tasks = [
      makeTask({ id: '1', currentStatus: 'FAILED' }),
      makeTask({ id: '2', currentStatus: 'FAILED', dependsOn: ['1'] }),
    ]

    const problems = detectFailedChains(tasks)
    expect(problems).toHaveLength(0)
  })
})

// ─── detectUnsatisfiedDeps ───

describe('detectUnsatisfiedDeps', () => {
  it('detects tasks executing with unmet deps', () => {
    const tasks = [
      makeTask({ id: '1', currentStatus: 'TODO' }),
      makeTask({ id: '2', currentStatus: 'EXECUTING', dependsOn: ['1'] }),
    ]

    const problems = detectUnsatisfiedDeps(tasks)
    expect(problems).toHaveLength(1)
    expect(problems[0].type).toBe('unmet_deps')
    expect(problems[0].taskId).toBe('2')
  })

  it('no problem when deps are satisfied', () => {
    const tasks = [
      makeTask({ id: '1', currentStatus: 'DONE' }),
      makeTask({ id: '2', currentStatus: 'IN_PROGRESS', dependsOn: ['1'] }),
    ]

    const problems = detectUnsatisfiedDeps(tasks)
    expect(problems).toHaveLength(0)
  })
})

// ─── computeSpecSummary ───

describe('computeSpecSummary', () => {
  it('counts tasks by status category', () => {
    const tasks = [
      makeTask({ id: '1', currentStatus: 'DONE' }),
      makeTask({ id: '2', currentStatus: 'FAILED' }),
      makeTask({ id: '3', currentStatus: 'TODO' }),
      makeTask({ id: '4', currentStatus: 'REVIEW' }),
    ]

    const summary = computeSpecSummary(tasks)
    expect(summary.total).toBe(4)
    expect(summary.done).toBe(2) // DONE + REVIEW
    expect(summary.failed).toBe(1)
    expect(summary.todo).toBe(1)
  })

  it('aggregates metrics from metadata', () => {
    const tasks = [
      makeTask({
        id: '1',
        currentStatus: 'DONE',
        metadata: { last_duration_ms: 5000, last_usage: { total_tokens: 1000 } },
      }),
      makeTask({
        id: '2',
        currentStatus: 'DONE',
        metadata: { last_duration_ms: 3000, last_usage: { input_tokens: 200, output_tokens: 300 } },
      }),
    ]

    const summary = computeSpecSummary(tasks)
    expect(summary.totalDurationMs).toBe(8000)
    expect(summary.totalTokens).toBe(1500)
  })
})

// ─── parseMetricsFromComment ───

describe('parseMetricsFromComment', () => {
  it('parses "Completed in Xs · N tokens"', () => {
    const m = parseMetricsFromComment('Completed in 45.2s · 12,345 tokens')
    expect(m.durationMs).toBe(45_200)
    expect(m.tokens).toBe(12345)
  })

  it('parses "in Nms"', () => {
    const m = parseMetricsFromComment('Failed in 250ms')
    expect(m.durationMs).toBe(250)
  })

  it('returns empty for unrecognized content', () => {
    const m = parseMetricsFromComment('Just a regular comment')
    expect(m.durationMs).toBeUndefined()
    expect(m.tokens).toBeUndefined()
  })
})

// ─── classifyEdgeByExecution ───

describe('classifyEdgeByExecution', () => {
  it('returns satisfied for DONE tasks', () => {
    expect(classifyEdgeByExecution(makeTask({ id: '1', currentStatus: 'DONE' }))).toBe('satisfied')
  })

  it('returns blocked for FAILED tasks', () => {
    expect(classifyEdgeByExecution(makeTask({ id: '1', currentStatus: 'FAILED' }))).toBe('blocked')
  })

  it('returns active for IN_PROGRESS tasks', () => {
    expect(classifyEdgeByExecution(makeTask({ id: '1', currentStatus: 'IN_PROGRESS' }))).toBe('active')
  })

  it('returns pending for TODO tasks', () => {
    expect(classifyEdgeByExecution(makeTask({ id: '1', currentStatus: 'TODO' }))).toBe('pending')
  })
})
