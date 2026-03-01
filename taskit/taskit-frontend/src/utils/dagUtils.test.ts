import { describe, it, expect } from 'vitest'
import type { Task } from '../types'
import {
  detectCycles,
  separateConnectedAndOrphans,
  classifyEdge,
  computeDagLayout,
} from './dagUtils'

// ─── Factory helper ───

function makeTask(overrides: Partial<Task> & { id: string }): Task {
  return {
    name: overrides.id,
    title: overrides.title ?? overrides.id,
    idShort: parseInt(overrides.id, 36) || 0,
    shortLink: '',
    boardId: 'b1',
    boardName: 'Test',
    currentStatus: 'In Progress',
    assignees: [],
    assigneeIds: [],
    createdAt: new Date().toISOString(),
    createdBy: 'test@test.com',
    mutations: [],
    comments: [],
    timeInStatuses: {},
    totalLifespanMs: 0,
    workTimeMs: 0,
    executingTimeMs: 0,
    dependsOn: [],
    ...overrides,
  }
}

// ─── detectCycles ───

describe('detectCycles', () => {
  it('returns empty array for a linear chain (no cycles)', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
      makeTask({ id: 'C', dependsOn: ['B'] }),
    ]
    expect(detectCycles(tasks)).toEqual([])
  })

  it('detects a simple two-node cycle', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: ['B'] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
    ]
    const cycles = detectCycles(tasks)
    expect(cycles.length).toBeGreaterThan(0)
    // Both A and B should be in a cycle
    const allCycleNodes = cycles.flat()
    expect(allCycleNodes).toContain('A')
    expect(allCycleNodes).toContain('B')
  })

  it('detects a self-referencing task', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: ['A'] }),
    ]
    const cycles = detectCycles(tasks)
    expect(cycles.length).toBeGreaterThan(0)
    expect(cycles.flat()).toContain('A')
  })

  it('returns empty for a diamond pattern (no cycle)', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
      makeTask({ id: 'C', dependsOn: ['A'] }),
      makeTask({ id: 'D', dependsOn: ['B', 'C'] }),
    ]
    expect(detectCycles(tasks)).toEqual([])
  })

  it('detects a complex multi-node cycle while ignoring acyclic nodes', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: ['C'] }), // A→C part of cycle A→C→B→A
      makeTask({ id: 'B', dependsOn: ['A'] }),
      makeTask({ id: 'C', dependsOn: ['B'] }),
      makeTask({ id: 'D', dependsOn: [] }),
      makeTask({ id: 'E', dependsOn: ['D'] }),
    ]
    const cycles = detectCycles(tasks)
    expect(cycles.length).toBeGreaterThan(0)
    const allCycleNodes = cycles.flat()
    expect(allCycleNodes).toContain('A')
    expect(allCycleNodes).toContain('B')
    expect(allCycleNodes).toContain('C')
    expect(allCycleNodes).not.toContain('D')
    expect(allCycleNodes).not.toContain('E')
  })

  it('handles tasks with no dependsOn field', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: undefined }),
      makeTask({ id: 'B', dependsOn: undefined }),
    ]
    expect(detectCycles(tasks)).toEqual([])
  })

  it('handles empty task list', () => {
    expect(detectCycles([])).toEqual([])
  })
})

// ─── separateConnectedAndOrphans ───

describe('separateConnectedAndOrphans', () => {
  it('returns all tasks as orphans when none have dependencies', () => {
    const tasks = [
      makeTask({ id: 'A' }),
      makeTask({ id: 'B' }),
      makeTask({ id: 'C' }),
    ]
    const result = separateConnectedAndOrphans(tasks)
    expect(result.connected).toHaveLength(0)
    expect(result.orphans).toHaveLength(3)
  })

  it('returns all tasks as connected when all are in the graph', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
      makeTask({ id: 'C', dependsOn: ['B'] }),
    ]
    const result = separateConnectedAndOrphans(tasks)
    expect(result.connected).toHaveLength(3)
    expect(result.orphans).toHaveLength(0)
  })

  it('correctly separates mixed connected and orphan tasks', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
      makeTask({ id: 'C' }),
      makeTask({ id: 'D' }),
    ]
    const result = separateConnectedAndOrphans(tasks)
    expect(result.connected.map(t => t.id).sort()).toEqual(['A', 'B'])
    expect(result.orphans.map(t => t.id).sort()).toEqual(['C', 'D'])
  })

  it('includes a task that is depended upon even if it has no dependsOn itself', () => {
    // A has no dependsOn, but B depends on A — both are connected
    const tasks = [
      makeTask({ id: 'A' }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
    ]
    const result = separateConnectedAndOrphans(tasks)
    expect(result.connected.map(t => t.id).sort()).toEqual(['A', 'B'])
    expect(result.orphans).toHaveLength(0)
  })

  it('handles empty task list', () => {
    const result = separateConnectedAndOrphans([])
    expect(result.connected).toHaveLength(0)
    expect(result.orphans).toHaveLength(0)
  })
})

// ─── classifyEdge ───

describe('classifyEdge', () => {
  it('returns satisfied for a done task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'Done' }))).toBe('satisfied')
  })

  it('returns satisfied for a completed task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'Complete' }))).toBe('satisfied')
  })

  it('returns blocked for a failed task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'Failed' }))).toBe('blocked')
  })

  it('returns blocked for an error task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'Error' }))).toBe('blocked')
  })

  it('returns active for an executing task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'EXECUTING' }))).toBe('active')
  })

  it('returns active for an in-progress task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'In Progress' }))).toBe('active')
  })

  it('returns active for a review task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'Review' }))).toBe('active')
  })

  it('returns active for a testing task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'Testing' }))).toBe('active')
  })

  it('returns pending for a todo task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'To Do' }))).toBe('pending')
  })

  it('returns pending for a backlog task', () => {
    expect(classifyEdge(makeTask({ id: 'A', currentStatus: 'Backlog' }))).toBe('pending')
  })
})

// ─── computeDagLayout ───

describe('computeDagLayout', () => {
  it('returns valid positions for a single node', () => {
    const tasks = [makeTask({ id: 'A' })]
    const result = computeDagLayout(tasks)
    expect(result.nodes).toHaveLength(1)
    expect(result.nodes[0].id).toBe('A')
    expect(typeof result.nodes[0].x).toBe('number')
    expect(typeof result.nodes[0].y).toBe('number')
  })

  it('lays out a chain left-to-right', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
      makeTask({ id: 'C', dependsOn: ['B'] }),
    ]
    const result = computeDagLayout(tasks)
    const nodeMap = new Map(result.nodes.map(n => [n.id, n]))
    // In LR layout, A should be leftmost, C rightmost
    expect(nodeMap.get('A')!.x).toBeLessThan(nodeMap.get('B')!.x)
    expect(nodeMap.get('B')!.x).toBeLessThan(nodeMap.get('C')!.x)
  })

  it('produces correct edges for dependencies', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
    ]
    const result = computeDagLayout(tasks)
    expect(result.edges).toHaveLength(1)
    expect(result.edges[0].source).toBe('A')
    expect(result.edges[0].target).toBe('B')
  })

  it('skips edges to nonexistent dependencies (no crash)', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: ['Z'] }), // Z doesn't exist
    ]
    const result = computeDagLayout(tasks)
    expect(result.edges).toHaveLength(0)
    expect(result.nodes).toHaveLength(1)
  })

  it('returns width and height enclosing all nodes', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
    ]
    const result = computeDagLayout(tasks)
    expect(result.width).toBeGreaterThan(0)
    expect(result.height).toBeGreaterThan(0)
  })

  it('handles diamond pattern correctly', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
      makeTask({ id: 'C', dependsOn: ['A'] }),
      makeTask({ id: 'D', dependsOn: ['B', 'C'] }),
    ]
    const result = computeDagLayout(tasks)
    expect(result.nodes).toHaveLength(4)
    expect(result.edges).toHaveLength(4) // A→B, A→C, B→D, C→D
  })

  it('handles empty task list', () => {
    const result = computeDagLayout([])
    expect(result.nodes).toHaveLength(0)
    expect(result.edges).toHaveLength(0)
    expect(result.width).toBe(0)
    expect(result.height).toBe(0)
  })
})
