import { useState, useEffect, useMemo } from 'react'
import { useService } from '../contexts/ServiceContext'
import { ApiError } from '../services/harness/HarnessTimeService'
import type { Task, TaskComment, TaskMutation } from '../types'
import {
  detectFailedChains,
  detectUnsatisfiedDeps,
  computeSpecSummary,
} from '../utils/diagnostics'
import type { Problem } from '../utils/diagnostics'
import { SpecJourney } from './SpecJourney'
import { DagView } from './DagView'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertTriangle, ChevronDown, ChevronRight, Bug } from 'lucide-react'

// ─── Types ───

interface DiagnosticSpec {
  id: number
  odin_id: string
  title: string
  source: string
  content: string
  abandoned: boolean
  board_id: number
  board_name: string
  metadata: Record<string, unknown>
  created_at: string
  tasks: DiagnosticTask[]
}

interface DiagnosticTask {
  id: number
  board_id: number
  title: string
  description: string
  priority: string
  status: string
  assignee?: { id: number; name: string; email: string; color?: string }
  created_at: string
  created_by: string
  last_updated_at: string
  labels?: Array<{ id: number; name: string; color: string }>
  spec_id?: number
  spec_title?: string
  depends_on?: string[]
  complexity?: string
  metadata?: Record<string, unknown>
  model_name?: string
  history?: Array<{
    id: number
    task_id: number
    field_name: string
    old_value: string
    new_value: string
    changed_at: string
    changed_by: string
  }>
  comments?: Array<{
    id: number
    task_id: number
    author_email: string
    author_label: string
    content: string
    attachments: unknown[]
    created_at: string
  }>
}

function transformDiagnosticTask(dt: DiagnosticTask): Task {
  const mutations: TaskMutation[] = (dt.history || []).map(h => {
    let type: TaskMutation['type'] = 'other'
    if (h.field_name === 'status') type = 'status_change'
    else if (h.field_name === 'assignee_id') type = 'assigned'
    else if (h.field_name === 'created') type = 'created'

    return {
      id: String(h.id),
      type,
      date: h.changed_at,
      timestamp: new Date(h.changed_at).getTime(),
      actor: h.changed_by || 'Unknown',
      actorId: h.changed_by || '',
      description: h.field_name === 'status'
        ? `status: ${h.old_value} \u2192 ${h.new_value}`
        : h.field_name === 'created'
          ? 'Task created'
          : `${h.field_name} changed`,
      fromStatus: h.field_name === 'status' ? h.old_value : undefined,
      toStatus: h.field_name === 'status' ? h.new_value : undefined,
      fieldName: h.field_name,
      oldValue: h.old_value,
      newValue: h.new_value,
    }
  })

  const comments: TaskComment[] = (dt.comments || []).map(c => ({
    id: String(c.id),
    taskId: String(c.task_id),
    authorEmail: c.author_email,
    authorLabel: c.author_label,
    content: c.content,
    attachments: c.attachments || [],
    commentType: (c as any).comment_type || 'status_update',
    createdAt: c.created_at,
  }))

  return {
    id: String(dt.id),
    name: dt.title || dt.description.substring(0, 50),
    title: dt.title,
    idShort: dt.id,
    shortLink: String(dt.id),
    boardId: String(dt.board_id),
    boardName: '',
    currentStatus: dt.status,
    assignees: dt.assignee ? [dt.assignee.name] : [],
    assigneeIds: dt.assignee ? [String(dt.assignee.id)] : [],
    createdAt: dt.created_at,
    createdBy: dt.created_by || 'Unknown',
    mutations,
    comments,
    timeInStatuses: {},
    totalLifespanMs: Date.now() - new Date(dt.created_at).getTime(),
    workTimeMs: 0,
    executingTimeMs: 0,
    description: dt.description,
    priority: dt.priority,
    specId: dt.spec_id ? String(dt.spec_id) : undefined,
    specName: dt.spec_title,
    labels: dt.labels || [],
    complexity: dt.complexity || undefined,
    metadata: dt.metadata && Object.keys(dt.metadata).length > 0 ? dt.metadata : undefined,
    dependsOn: dt.depends_on && dt.depends_on.length > 0 ? dt.depends_on : undefined,
    modelName: dt.model_name || undefined,
  }
}

// ─── Severity badge helper ───

function ProblemBadge({ problem }: { problem: Problem }) {
  const variant = problem.severity === 'error' ? 'destructive' : 'secondary'
  return (
    <div className="flex items-start gap-2 py-1.5">
      <Badge variant={variant} className="text-[10px] px-1.5 shrink-0">
        {problem.type === 'stuck' ? 'STUCK' : problem.type === 'failed_chain' ? 'BLOCKED' : problem.type === 'unmet_deps' ? 'UNMET' : 'WARN'}
      </Badge>
      <div className="text-xs">
        <span className="font-medium">#{problem.taskId}</span>{' '}
        <span className="text-muted-foreground">{problem.message}</span>
      </div>
    </div>
  )
}

// ─── Main component ───

interface SpecDebugViewProps {
  specId: string
  onBack: () => void
  onTaskClick: (taskId: string) => void
}

export function SpecDebugView({ specId, onBack, onTaskClick }: SpecDebugViewProps) {
  const service = useService()
  const [spec, setSpec] = useState<DiagnosticSpec | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [problemsExpanded, setProblemsExpanded] = useState(true)

  useEffect(() => {
    setLoading(true)
    setError(null)

    // Fetch from the diagnostic endpoint
    const baseUrl = (service as any).baseUrl || ''
    const fetchDiagnostic = async () => {
      const authHeaders = (service as any).authHeaders
        ? await (service as any).authHeaders()
        : {}
      const res = await fetch(`${baseUrl}/specs/${specId}/diagnostic/`, { headers: authHeaders })
      if (!res.ok) {
        if (res.status === 404) throw new ApiError(404, 'Not Found')
        throw new ApiError(res.status, res.statusText)
      }
      return res.json()
    }

    fetchDiagnostic()
      .then(setSpec)
      .catch(err => {
        setError(err instanceof ApiError && err.isNotFound
          ? 'Spec not found'
          : err instanceof Error ? err.message : 'Failed to load diagnostic')
      })
      .finally(() => setLoading(false))
  }, [specId, service])

  // Transform diagnostic tasks to frontend Task type
  const tasks = useMemo(() => {
    if (!spec) return []
    return spec.tasks.map(transformDiagnosticTask)
  }, [spec])

  const summary = useMemo(() => computeSpecSummary(tasks), [tasks])

  const problems = useMemo(() => {
    if (tasks.length === 0) return []
    return [
      ...detectFailedChains(tasks),
      ...detectUnsatisfiedDeps(tasks),
    ]
  }, [tasks])

  if (loading) {
    return (
      <div className="text-center py-16 text-muted-foreground">
        <div className="size-10 border-3 border-border border-t-primary rounded-full animate-spin mx-auto mb-4" />
        Loading diagnostic data...
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-center py-16 text-muted-foreground">
        <AlertTriangle className="size-12 mx-auto mb-4 opacity-50 text-destructive" />
        <div className="text-lg font-semibold text-foreground mb-2">Failed to load diagnostic</div>
        <p className="text-sm mb-4">{error}</p>
        <Button variant="outline" onClick={onBack}>Back</Button>
      </div>
    )
  }

  if (!spec) return null

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <Button variant="ghost" size="sm" className="gap-1.5" onClick={onBack}>
          <ArrowLeft className="size-3.5" /> Back
        </Button>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <Bug className="size-5 text-muted-foreground" />
        <h2 className="text-lg font-semibold">{spec.title}</h2>
        <div className="flex gap-1.5 text-xs">
          {summary.done > 0 && <Badge variant="secondary" className="bg-green-500/10 text-green-600">{summary.done} done</Badge>}
          {summary.inProgress > 0 && <Badge variant="secondary" className="bg-blue-500/10 text-blue-600">{summary.inProgress} active</Badge>}
          {summary.stuck > 0 && <Badge variant="secondary" className="bg-yellow-500/10 text-yellow-600">{summary.stuck} idle</Badge>}
          {summary.failed > 0 && <Badge variant="destructive">{summary.failed} failed</Badge>}
          {summary.blocked > 0 && <Badge variant="secondary" className="bg-red-500/10 text-red-600">{summary.blocked} blocked</Badge>}
          {summary.todo > 0 && <Badge variant="outline">{summary.todo} todo</Badge>}
        </div>
      </div>

      {/* Two-panel layout */}
      <div className="grid grid-cols-1 lg:grid-cols-[2fr_3fr] gap-4 mb-4">
        {/* Left: Spec Journey */}
        <Card className="border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Spec Journey</CardTitle>
          </CardHeader>
          <CardContent className="max-h-[500px] overflow-y-auto">
            <SpecJourney tasks={tasks} onTaskClick={onTaskClick} />
          </CardContent>
        </Card>

        {/* Right: DAG */}
        <Card className="border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Dependency Graph</CardTitle>
          </CardHeader>
          <CardContent>
            <DagView
              tasks={tasks}
              allTasks={tasks}
              members={[]}
              onTaskClick={(t) => onTaskClick(t.id)}
            />
          </CardContent>
        </Card>
      </div>

      {/* Problems section */}
      <Card className="border-border">
        <CardHeader className="pb-2 cursor-pointer" onClick={() => setProblemsExpanded(!problemsExpanded)}>
          <div className="flex items-center gap-2">
            {problemsExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
            <CardTitle className="text-sm">
              Problems Detected
              {problems.length > 0 && (
                <Badge variant="destructive" className="ml-2 text-[10px]">{problems.length}</Badge>
              )}
            </CardTitle>
          </div>
        </CardHeader>
        {problemsExpanded && (
          <CardContent>
            {problems.length === 0 ? (
              <div className="text-sm text-muted-foreground py-2">No problems detected.</div>
            ) : (
              <div className="space-y-1">
                {problems.map((p, i) => (
                  <ProblemBadge key={`${p.taskId}-${p.type}-${i}`} problem={p} />
                ))}
              </div>
            )}
          </CardContent>
        )}
      </Card>

      {/* Metrics footer */}
      {(summary.totalDurationMs > 0 || summary.totalTokens > 0) && (
        <div className="flex gap-4 mt-3 text-xs text-muted-foreground">
          {summary.totalDurationMs > 0 && (
            <span>Total duration: {(summary.totalDurationMs / 1000).toFixed(1)}s</span>
          )}
          {summary.totalTokens > 0 && (
            <span>Total tokens: {summary.totalTokens.toLocaleString()}</span>
          )}
        </div>
      )}
    </div>
  )
}
