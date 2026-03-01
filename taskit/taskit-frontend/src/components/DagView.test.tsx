import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DagView } from './DagView'
import type { Task, Member } from '../types'

// ─── Factory helpers ───

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

const defaultMembers: Member[] = []

// ─── DagView component tests ───

describe('DagView', () => {
  it('renders empty state when tasks is empty', () => {
    render(
      <DagView tasks={[]} allTasks={[]} members={defaultMembers} onTaskClick={() => {}} />
    )
    expect(screen.getByText('No tasks to display')).toBeInTheDocument()
  })

  it('renders "no dependencies" message when all tasks are orphans', () => {
    const tasks = [
      makeTask({ id: 'A' }),
      makeTask({ id: 'B' }),
    ]
    render(
      <DagView tasks={tasks} allTasks={tasks} members={defaultMembers} onTaskClick={() => {}} />
    )
    expect(screen.getByText(/No dependency relationships found/)).toBeInTheDocument()
  })

  it('renders SVG nodes for connected tasks', () => {
    const tasks = [
      makeTask({ id: 'A', title: 'Task Alpha', dependsOn: [] }),
      makeTask({ id: 'B', title: 'Task Beta', dependsOn: ['A'] }),
    ]
    const { container } = render(
      <DagView tasks={tasks} allTasks={tasks} members={defaultMembers} onTaskClick={() => {}} />
    )
    // Should render SVG with node texts
    const svgTexts = container.querySelectorAll('svg text')
    const textContents = Array.from(svgTexts).map(t => t.textContent)
    expect(textContents).toContain('Task Alpha')
    expect(textContents).toContain('Task Beta')
  })

  it('renders edges for dependency relationships', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
    ]
    const { container } = render(
      <DagView tasks={tasks} allTasks={tasks} members={defaultMembers} onTaskClick={() => {}} />
    )
    // dagre produces edge paths
    const paths = container.querySelectorAll('svg path[d]')
    // At least one edge path (excluding arrowhead marker paths)
    const edgePaths = Array.from(paths).filter(p => {
      const d = p.getAttribute('d') || ''
      return d.startsWith('M ') && !d.includes('L 10 5') // exclude marker paths
    })
    expect(edgePaths.length).toBeGreaterThanOrEqual(1)
  })

  it('calls onTaskClick when a node is clicked', () => {
    const onClick = vi.fn()
    const tasks = [
      makeTask({ id: 'A', title: 'Click Me', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
    ]
    const { container } = render(
      <DagView tasks={tasks} allTasks={tasks} members={defaultMembers} onTaskClick={onClick} />
    )
    // Find the text element for Task A and click its parent group
    const textElements = container.querySelectorAll('svg text')
    const clickMeText = Array.from(textElements).find(t => t.textContent === 'Click Me')
    expect(clickMeText).toBeTruthy()
    // Click the parent <g> element
    const parentG = clickMeText!.closest('g[class]')
    if (parentG) fireEvent.click(parentG)
    expect(onClick).toHaveBeenCalledWith(expect.objectContaining({ id: 'A' }))
  })

  it('shows orphan section when there are both connected and orphan tasks', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
      makeTask({ id: 'C' }), // orphan
      makeTask({ id: 'D' }), // orphan
    ]
    render(
      <DagView tasks={tasks} allTasks={tasks} members={defaultMembers} onTaskClick={() => {}} />
    )
    expect(screen.getByText('2 unconnected tasks')).toBeInTheDocument()
  })

  it('shows cycle warning when cycles are detected', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: ['B'] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
    ]
    render(
      <DagView tasks={tasks} allTasks={tasks} members={defaultMembers} onTaskClick={() => {}} />
    )
    expect(screen.getByText(/Circular dependencies detected/)).toBeInTheDocument()
  })

  it('renders Fit All button', () => {
    const tasks = [
      makeTask({ id: 'A', dependsOn: [] }),
      makeTask({ id: 'B', dependsOn: ['A'] }),
    ]
    render(
      <DagView tasks={tasks} allTasks={tasks} members={defaultMembers} onTaskClick={() => {}} />
    )
    expect(screen.getByText('Fit All')).toBeInTheDocument()
  })
})
