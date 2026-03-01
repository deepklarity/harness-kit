/**
 * Diagnostic utilities for spec execution debugging.
 *
 * Pure functions that analyze task data to detect problems
 * and compute summaries.
 */
import type { Task, TaskComment, TaskMutation } from '../types'

// ─── Types ───

export interface Problem {
  type: 'stuck' | 'failed_chain' | 'unmet_deps' | 'no_assignee'
  severity: 'error' | 'warning'
  taskId: string
  taskTitle: string
  message: string
}

export interface SpecSummary {
  total: number
  done: number
  failed: number
  stuck: number
  blocked: number
  inProgress: number
  todo: number
  totalTokens: number
  totalDurationMs: number
}

export interface ParsedMetrics {
  durationMs?: number
  tokens?: number
}

// ─── Status classification helpers ───

const DONE_STATUSES = new Set(['DONE', 'REVIEW', 'TESTING'])
const ACTIVE_STATUSES = new Set(['IN_PROGRESS', 'EXECUTING'])
const FAILED_STATUSES = new Set(['FAILED'])
// ─── Exported functions ───

/**
 * Detect tasks stuck in active states with stale history.
 */
export function detectStuckTasks(tasks: Task[], thresholdMs: number = 600_000): Problem[] {
  const now = Date.now()
  const problems: Problem[] = []

  for (const task of tasks) {
    if (!ACTIVE_STATUSES.has(task.currentStatus)) continue

    // Find last activity timestamp
    const lastMutation = task.mutations.length > 0
      ? Math.max(...task.mutations.map(m => m.timestamp))
      : new Date(task.createdAt).getTime()

    const elapsed = now - lastMutation
    if (elapsed > thresholdMs) {
      const minutes = Math.round(elapsed / 60_000)
      problems.push({
        type: 'stuck',
        severity: 'warning',
        taskId: task.id,
        taskTitle: task.title || task.name,
        message: `Stuck in ${task.currentStatus} for ${minutes}m (no activity since ${new Date(lastMutation).toLocaleTimeString()})`,
      })
    }
  }

  return problems
}

/**
 * Detect failed tasks that block downstream children.
 */
export function detectFailedChains(tasks: Task[]): Problem[] {
  const problems: Problem[] = []
  const failedIds = new Set(
    tasks.filter(t => FAILED_STATUSES.has(t.currentStatus)).map(t => t.id)
  )

  if (failedIds.size === 0) return problems

  for (const task of tasks) {
    if (!task.dependsOn || FAILED_STATUSES.has(task.currentStatus) || DONE_STATUSES.has(task.currentStatus)) continue

    const failedDeps = task.dependsOn.filter(dep => failedIds.has(dep))
    if (failedDeps.length > 0) {
      problems.push({
        type: 'failed_chain',
        severity: 'error',
        taskId: task.id,
        taskTitle: task.title || task.name,
        message: `Blocked by failed dep(s): ${failedDeps.map(d => `#${d}`).join(', ')}`,
      })
    }
  }

  return problems
}

/**
 * Detect tasks running despite unmet dependencies.
 */
export function detectUnsatisfiedDeps(tasks: Task[]): Problem[] {
  const problems: Problem[] = []
  const taskMap = new Map(tasks.map(t => [t.id, t]))

  for (const task of tasks) {
    if (!ACTIVE_STATUSES.has(task.currentStatus) || !task.dependsOn) continue

    const unmetDeps = task.dependsOn.filter(depId => {
      const dep = taskMap.get(depId)
      return dep && !DONE_STATUSES.has(dep.currentStatus)
    })

    if (unmetDeps.length > 0) {
      const depDetails = unmetDeps.map(d => {
        const dep = taskMap.get(d)
        return `#${d} (${dep?.currentStatus || 'unknown'})`
      })
      problems.push({
        type: 'unmet_deps',
        severity: 'error',
        taskId: task.id,
        taskTitle: task.title || task.name,
        message: `${task.currentStatus} but deps not satisfied: ${depDetails.join(', ')}`,
      })
    }
  }

  return problems
}

/**
 * Compute summary statistics for a spec's tasks.
 */
export function computeSpecSummary(tasks: Task[]): SpecSummary {
  let done = 0, failed = 0, stuck = 0, blocked = 0, inProgress = 0, todo = 0
  let totalTokens = 0, totalDurationMs = 0

  const stuckProblems = detectStuckTasks(tasks)
  const stuckIds = new Set(stuckProblems.map(p => p.taskId))

  const failedIds = new Set(
    tasks.filter(t => FAILED_STATUSES.has(t.currentStatus)).map(t => t.id)
  )

  for (const task of tasks) {
    if (DONE_STATUSES.has(task.currentStatus)) done++
    else if (FAILED_STATUSES.has(task.currentStatus)) failed++
    else if (stuckIds.has(task.id)) stuck++
    else if (ACTIVE_STATUSES.has(task.currentStatus)) inProgress++
    else if (task.dependsOn?.some(d => failedIds.has(d))) blocked++
    else todo++

    // Extract metrics from metadata
    if (task.metadata) {
      const durationMs = task.metadata.last_duration_ms as number | undefined
      if (durationMs) totalDurationMs += durationMs

      const usage = (task.usage || task.metadata.last_usage) as Record<string, number> | undefined
      if (usage) {
        totalTokens += usage.total_tokens || (usage.input_tokens || 0) + (usage.output_tokens || 0)
      }
    }
  }

  return { total: tasks.length, done, failed, stuck, blocked, inProgress, todo, totalTokens, totalDurationMs }
}

/**
 * Parse metrics from comment text like "Completed in 45.2s · 12,345 tokens".
 */
export function parseMetricsFromComment(content: string): ParsedMetrics {
  const metrics: ParsedMetrics = {}

  // Match "in Xs" or "in X.Xs" or "in Xms"
  const durationMatch = content.match(/in\s+([\d.]+)(s|ms)/i)
  if (durationMatch) {
    const value = parseFloat(durationMatch[1])
    metrics.durationMs = durationMatch[2] === 'ms' ? value : value * 1000
  }

  // Match "N tokens" or "N,NNN tokens"
  const tokenMatch = content.match(/([\d,]+)\s+tokens/i)
  if (tokenMatch) {
    metrics.tokens = parseInt(tokenMatch[1].replace(/,/g, ''), 10)
  }

  return metrics
}

/**
 * Classify an edge by the execution state of its source task.
 * Used for debug mode in DagView.
 */
export function classifyEdgeByExecution(sourceTask: Task): 'satisfied' | 'active' | 'blocked' | 'pending' {
  if (FAILED_STATUSES.has(sourceTask.currentStatus)) return 'blocked'
  if (DONE_STATUSES.has(sourceTask.currentStatus)) return 'satisfied'
  if (ACTIVE_STATUSES.has(sourceTask.currentStatus)) return 'active'
  return 'pending'
}

// ─── Journey Types ───

export interface TaskAttemptMetrics {
  durationMs?: number
  tokens?: number
  costUsd?: number
  model?: string
}

export interface TaskAttempt {
  attemptNumber: number
  outcome: 'pass' | 'fail' | 'pending'
  mutations: TaskMutation[]
  comments: TaskComment[]
  reflectionVerdict?: string
  reflectionSummary?: string
  metrics: TaskAttemptMetrics
  startTimestamp?: number
  endTimestamp?: number
}

export interface JourneyTask {
  task: Task
  wave: number
  attempts: TaskAttempt[]
  outcome: 'pass' | 'fail' | 'pending'
}

export type JourneyChapter =
  | { type: 'planning'; taskCount: number; waveCount: number; agents: string[] }
  | { type: 'wave'; wave: number; isRetry: boolean; tasks: JourneyTask[]; durationMs: number; totalTokens: number; totalCostUsd: number }
  | { type: 'pivot'; failedTask: JourneyTask; blockedTasks: JourneyTask[] }
  | { type: 'summary'; completed: number; failed: number; retries: number; totalDurationMs: number; totalTokens: number; totalCostUsd: number }

export interface SpecJourneyData {
  chapters: JourneyChapter[]
  tasks: JourneyTask[]
}

// ─── Journey Builder ───

/**
 * Infer wave numbers from dependency DAG.
 * No deps → wave 1, all deps in waves ≤ N → wave N+1.
 */
function inferWaves(tasks: Task[]): Map<string, number> {
  const waves = new Map<string, number>()
  const taskIds = new Set(tasks.map(t => t.id))

  // Iterative topological depth assignment
  let changed = true
  // Initialize: tasks with no deps (or deps outside this set) = wave 1
  for (const task of tasks) {
    const deps = task.dependsOn?.filter(d => taskIds.has(d)) || []
    if (deps.length === 0) waves.set(task.id, 1)
  }

  while (changed) {
    changed = false
    for (const task of tasks) {
      if (waves.has(task.id)) continue
      const deps = task.dependsOn?.filter(d => taskIds.has(d)) || []
      if (deps.length === 0) {
        waves.set(task.id, 1)
        changed = true
        continue
      }
      const depWaves = deps.map(d => waves.get(d))
      if (depWaves.every(w => w !== undefined)) {
        waves.set(task.id, Math.max(...(depWaves as number[])) + 1)
        changed = true
      }
    }
  }

  // Fallback for cycles or missing refs
  for (const task of tasks) {
    if (!waves.has(task.id)) waves.set(task.id, 1)
  }

  return waves
}

/**
 * Extract attempts from a task's mutation/comment history.
 * Each FAILED→TODO/IN_PROGRESS cycle starts a new attempt.
 */
function extractAttempts(task: Task): TaskAttempt[] {
  const sortedMutations = [...task.mutations].sort((a, b) => a.timestamp - b.timestamp)
  const sortedComments = [...task.comments].sort(
    (a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
  )

  if (sortedMutations.length === 0 && sortedComments.length === 0) {
    return [{
      attemptNumber: 1,
      outcome: DONE_STATUSES.has(task.currentStatus) ? 'pass'
        : FAILED_STATUSES.has(task.currentStatus) ? 'fail' : 'pending',
      mutations: [],
      comments: sortedComments,
      metrics: buildAttemptMetrics(task, [], sortedComments),
    }]
  }

  // Find attempt boundaries: each FAILED status followed by TODO/IN_PROGRESS starts a new attempt
  const attempts: TaskAttempt[] = []
  let currentMutations: TaskMutation[] = []
  let attemptStart = sortedMutations[0]?.timestamp

  for (const m of sortedMutations) {
    currentMutations.push(m)

    // If this mutation transitions TO a failed state, and the next one restarts, split
    if (m.toStatus && FAILED_STATUSES.has(m.toStatus)) {
      const idx = sortedMutations.indexOf(m)
      const next = sortedMutations[idx + 1]
      if (next && (next.toStatus === 'TODO' || next.toStatus === 'IN_PROGRESS')) {
        // Close this attempt as failed
        const attemptComments = sortedComments.filter(c => {
          const ct = new Date(c.createdAt).getTime()
          return ct >= (attemptStart || 0) && ct <= m.timestamp
        })
        attempts.push({
          attemptNumber: attempts.length + 1,
          outcome: 'fail',
          mutations: currentMutations,
          comments: attemptComments,
          ...extractReflection(attemptComments),
          metrics: buildAttemptMetrics(task, currentMutations, attemptComments),
          startTimestamp: attemptStart,
          endTimestamp: m.timestamp,
        })
        currentMutations = []
        attemptStart = next.timestamp
      }
    }
  }

  // Close final attempt
  const lastMutationTs = currentMutations.length > 0
    ? currentMutations[currentMutations.length - 1].timestamp
    : undefined
  const attemptComments = sortedComments.filter(c => {
    const ct = new Date(c.createdAt).getTime()
    return ct >= (attemptStart || 0) && (lastMutationTs === undefined || ct <= lastMutationTs + 60_000)
  })
  // Include any remaining comments not captured by earlier attempts
  const usedCommentIds = new Set(attempts.flatMap(a => a.comments.map(c => c.id)))
  const remainingComments = sortedComments.filter(c => !usedCommentIds.has(c.id))
  const finalComments = [...attemptComments, ...remainingComments.filter(c => !attemptComments.includes(c))]

  const finalOutcome = DONE_STATUSES.has(task.currentStatus) ? 'pass' as const
    : FAILED_STATUSES.has(task.currentStatus) ? 'fail' as const : 'pending' as const

  attempts.push({
    attemptNumber: attempts.length + 1,
    outcome: finalOutcome,
    mutations: currentMutations,
    comments: finalComments,
    ...extractReflection(finalComments),
    metrics: buildAttemptMetrics(task, currentMutations, finalComments),
    startTimestamp: attemptStart,
    endTimestamp: lastMutationTs,
  })

  return attempts
}

function extractReflection(comments: TaskComment[]): { reflectionVerdict?: string; reflectionSummary?: string } {
  const reflection = comments.find(c => c.commentType === 'reflection')
  if (!reflection) return {}

  const verdictMatch = reflection.content.match(/verdict[:\s]*(\w+)/i)
  return {
    reflectionVerdict: verdictMatch ? verdictMatch[1].toUpperCase() : undefined,
    reflectionSummary: reflection.content.split('\n')[0].slice(0, 120),
  }
}

function buildAttemptMetrics(task: Task, mutations: TaskMutation[], comments: TaskComment[]): TaskAttemptMetrics {
  const metrics: TaskAttemptMetrics = { model: task.modelName }

  // Try metadata first
  if (task.metadata?.last_duration_ms) {
    metrics.durationMs = task.metadata.last_duration_ms as number
  }

  // Token usage from task.usage
  const usage = task.usage || (task.metadata?.last_usage as Record<string, number> | undefined)
  if (usage) {
    metrics.tokens = (usage as any).total_tokens || ((usage as any).input_tokens || 0) + ((usage as any).output_tokens || 0)
  }

  // Cost from task
  if (task.estimatedCostUsd) {
    metrics.costUsd = task.estimatedCostUsd
  }

  // Try parsing from comments if no metadata
  if (!metrics.durationMs || !metrics.tokens) {
    for (const c of comments) {
      const parsed = parseMetricsFromComment(c.content)
      if (!metrics.durationMs && parsed.durationMs) metrics.durationMs = parsed.durationMs
      if (!metrics.tokens && parsed.tokens) metrics.tokens = parsed.tokens
    }
  }

  // Infer duration from mutation timestamps if still missing
  if (!metrics.durationMs && mutations.length >= 2) {
    const start = mutations[0].timestamp
    const end = mutations[mutations.length - 1].timestamp
    if (end > start) metrics.durationMs = end - start
  }

  return metrics
}

/**
 * Build a spec journey: wave-grouped, retry-aware, pivot-detecting view.
 */
export function buildSpecJourney(tasks: Task[]): SpecJourneyData {
  if (tasks.length === 0) return { chapters: [], tasks: [] }

  const waves = inferWaves(tasks)
  const maxWave = Math.max(...waves.values())

  // Build JourneyTasks
  const journeyTasks: JourneyTask[] = tasks.map(task => {
    const attempts = extractAttempts(task)
    const lastAttempt = attempts[attempts.length - 1]
    return {
      task,
      wave: waves.get(task.id) || 1,
      attempts,
      outcome: lastAttempt.outcome,
    }
  })

  // Collect unique agents
  const agents = [...new Set(tasks.flatMap(t => t.assignees).filter(Boolean))]

  // Build chapters
  const chapters: JourneyChapter[] = []

  // Planning chapter
  chapters.push({
    type: 'planning',
    taskCount: tasks.length,
    waveCount: maxWave,
    agents,
  })

  // Detect pivots: task failed AND has downstream dependents not yet done
  const pivotTasks = new Set<string>()

  for (const jt of journeyTasks) {
    if (jt.outcome !== 'fail' && jt.attempts.some(a => a.outcome === 'fail')) {
      // Had at least one failure — check for blocked downstream
      const blockedDownstream = journeyTasks.filter(other =>
        other.task.dependsOn?.includes(jt.task.id) && other.outcome !== 'pass'
      )
      if (blockedDownstream.length > 0) {
        pivotTasks.add(jt.task.id)
      }
    }
  }

  // Also detect tasks that are still failed and have downstream
  for (const jt of journeyTasks) {
    if (jt.outcome === 'fail') {
      const blockedDownstream = journeyTasks.filter(other =>
        other.task.dependsOn?.includes(jt.task.id)
      )
      if (blockedDownstream.length > 0) {
        pivotTasks.add(jt.task.id)
      }
    }
  }

  // Wave chapters with retry splitting
  for (let w = 1; w <= maxWave; w++) {
    const waveTasks = journeyTasks.filter(jt => jt.wave === w)
    if (waveTasks.length === 0) continue

    // First attempts
    const firstAttemptTasks = waveTasks
    const firstDuration = firstAttemptTasks.reduce((sum, jt) => {
      const a = jt.attempts[0]
      return sum + (a?.metrics.durationMs || 0)
    }, 0)
    const firstTokens = firstAttemptTasks.reduce((sum, jt) => {
      const a = jt.attempts[0]
      return sum + (a?.metrics.tokens || 0)
    }, 0)
    const firstCost = firstAttemptTasks.reduce((sum, jt) => {
      const a = jt.attempts[0]
      return sum + (a?.metrics.costUsd || 0)
    }, 0)

    chapters.push({
      type: 'wave',
      wave: w,
      isRetry: false,
      tasks: waveTasks,
      durationMs: firstDuration,
      totalTokens: firstTokens,
      totalCostUsd: firstCost,
    })

    // Insert pivot chapters for any wave tasks that triggered pivots
    const waveTasksWithPivots = waveTasks.filter(jt => pivotTasks.has(jt.task.id))
    for (const pivotJt of waveTasksWithPivots) {
      const blocked = journeyTasks.filter(other =>
        other.task.dependsOn?.includes(pivotJt.task.id)
      )
      chapters.push({
        type: 'pivot',
        failedTask: pivotJt,
        blockedTasks: blocked,
      })
    }

    // Retry wave if any tasks had multiple attempts
    const retryTasks = waveTasks.filter(jt => jt.attempts.length > 1)
    if (retryTasks.length > 0) {
      const retryDuration = retryTasks.reduce((sum, jt) => {
        return sum + jt.attempts.slice(1).reduce((s, a) => s + (a.metrics.durationMs || 0), 0)
      }, 0)
      const retryTokens = retryTasks.reduce((sum, jt) => {
        return sum + jt.attempts.slice(1).reduce((s, a) => s + (a.metrics.tokens || 0), 0)
      }, 0)
      const retryCost = retryTasks.reduce((sum, jt) => {
        return sum + jt.attempts.slice(1).reduce((s, a) => s + (a.metrics.costUsd || 0), 0)
      }, 0)

      chapters.push({
        type: 'wave',
        wave: w,
        isRetry: true,
        tasks: retryTasks,
        durationMs: retryDuration,
        totalTokens: retryTokens,
        totalCostUsd: retryCost,
      })
    }
  }

  // Summary chapter
  const totalDurationMs = journeyTasks.reduce((sum, jt) =>
    sum + jt.attempts.reduce((s, a) => s + (a.metrics.durationMs || 0), 0), 0)
  const totalTokens = journeyTasks.reduce((sum, jt) =>
    sum + jt.attempts.reduce((s, a) => s + (a.metrics.tokens || 0), 0), 0)
  const totalCostUsd = journeyTasks.reduce((sum, jt) =>
    sum + jt.attempts.reduce((s, a) => s + (a.metrics.costUsd || 0), 0), 0)
  const totalRetries = journeyTasks.reduce((sum, jt) => sum + Math.max(0, jt.attempts.length - 1), 0)

  chapters.push({
    type: 'summary',
    completed: journeyTasks.filter(jt => jt.outcome === 'pass').length,
    failed: journeyTasks.filter(jt => jt.outcome === 'fail').length,
    retries: totalRetries,
    totalDurationMs,
    totalTokens,
    totalCostUsd,
  })

  return { chapters, tasks: journeyTasks }
}
