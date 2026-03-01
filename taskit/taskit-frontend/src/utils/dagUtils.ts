import dagre from '@dagrejs/dagre'
import type { Task } from '../types'

// ─── Types ───

export interface LayoutNode {
  id: string
  x: number
  y: number
  width: number
  height: number
}

export interface LayoutEdge {
  source: string
  target: string
  points: { x: number; y: number }[]
}

export interface DagLayout {
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  width: number
  height: number
}

// ─── Constants ───

const NODE_WIDTH = 200
const NODE_HEIGHT = 50

// ─── Status classification (mirrors transformer.ts logic) ───

const TERMINAL_KEYWORDS = ['done', 'complete', 'finished', 'closed', 'archived', 'failed', 'error', 'blocked']
const FAILED_KEYWORDS = ['failed', 'error', 'blocked']
const QUEUE_KEYWORDS = ['backlog', 'todo', 'to do', 'planned']
const ACTIVE_KEYWORDS = ['doing', 'in progress', 'in_progress', 'executing', 'working', 'active', 'review', 'testing', 'qa', 'verify']

function matchesKeywords(status: string, keywords: string[]): boolean {
  const lower = status.toLowerCase()
  return keywords.some(k => lower.includes(k))
}

// ─── Exported functions ───

/**
 * Detect cycles in the task dependency graph using dagre's built-in graphlib.
 * Returns arrays of node IDs that form cycles (empty array if acyclic).
 */
export function detectCycles(tasks: Task[]): string[][] {
  if (tasks.length === 0) return []

  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))

  const taskIds = new Set(tasks.map(t => t.id))
  for (const task of tasks) {
    g.setNode(task.id, {})
  }
  for (const task of tasks) {
    for (const dep of task.dependsOn ?? []) {
      if (taskIds.has(dep)) {
        g.setEdge(dep, task.id)
      }
    }
  }

  const cycles = dagre.graphlib.alg.findCycles(g)
  return cycles
}

/**
 * Separate tasks into connected (part of dependency graph) and orphans (no edges).
 * A task is connected if it appears in any dependsOn relationship (as source or target).
 */
export function separateConnectedAndOrphans(tasks: Task[]): {
  connected: Task[]
  orphans: Task[]
} {
  if (tasks.length === 0) return { connected: [], orphans: [] }

  const taskIds = new Set(tasks.map(t => t.id))
  const connectedIds = new Set<string>()

  for (const task of tasks) {
    for (const dep of task.dependsOn ?? []) {
      if (taskIds.has(dep)) {
        connectedIds.add(task.id)
        connectedIds.add(dep)
      }
    }
  }

  const connected: Task[] = []
  const orphans: Task[] = []
  for (const task of tasks) {
    if (connectedIds.has(task.id)) {
      connected.push(task)
    } else {
      orphans.push(task)
    }
  }

  return { connected, orphans }
}

/**
 * Classify the edge from a source task based on its status.
 * Used to color dependency edges.
 */
export function classifyEdge(sourceTask: Task): 'satisfied' | 'active' | 'blocked' | 'pending' {
  const status = sourceTask.currentStatus

  // Failed/blocked takes priority over terminal
  if (matchesKeywords(status, FAILED_KEYWORDS)) return 'blocked'
  // Terminal (done/complete) but not failed
  if (matchesKeywords(status, TERMINAL_KEYWORDS)) return 'satisfied'
  // Active work
  if (matchesKeywords(status, ACTIVE_KEYWORDS)) return 'active'
  // Queue/waiting
  if (matchesKeywords(status, QUEUE_KEYWORDS)) return 'pending'

  // Default: treat unknown status as pending
  return 'pending'
}

/**
 * Compute DAG layout using dagre's Sugiyama algorithm.
 * Returns positioned nodes and edge paths. Skips edges to nonexistent tasks.
 * If cycles are detected, breaks them by removing back-edges before layout.
 */
export function computeDagLayout(tasks: Task[]): DagLayout {
  if (tasks.length === 0) {
    return { nodes: [], edges: [], width: 0, height: 0 }
  }

  const g = new dagre.graphlib.Graph()
  g.setGraph({
    rankdir: 'LR',
    nodesep: 40,
    ranksep: 80,
    marginx: 20,
    marginy: 20,
  })
  g.setDefaultEdgeLabel(() => ({}))

  const taskIds = new Set(tasks.map(t => t.id))
  for (const task of tasks) {
    g.setNode(task.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  }

  // Collect valid edges, skipping nonexistent targets
  const validEdges: { source: string; target: string }[] = []
  for (const task of tasks) {
    for (const dep of task.dependsOn ?? []) {
      if (taskIds.has(dep)) {
        validEdges.push({ source: dep, target: task.id })
      }
    }
  }

  // Add edges — dagre's acyclicer will handle cycles if present
  for (const edge of validEdges) {
    g.setEdge(edge.source, edge.target)
  }

  dagre.layout(g)

  const nodes: LayoutNode[] = g.nodes().map(id => {
    const node = g.node(id)
    return {
      id,
      x: node.x,
      y: node.y,
      width: node.width,
      height: node.height,
    }
  })

  const edges: LayoutEdge[] = g.edges().map(e => {
    const edge = g.edge(e)
    return {
      source: e.v,
      target: e.w,
      points: edge.points || [],
    }
  })

  const graphLabel = g.graph()
  const width = graphLabel?.width ?? 0
  const height = graphLabel?.height ?? 0

  return { nodes, edges, width, height }
}
