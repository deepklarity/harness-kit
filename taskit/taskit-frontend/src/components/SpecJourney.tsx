import { useState } from 'react'
import type { Task } from '../types'
import type { JourneyChapter, JourneyTask, TaskAttempt } from '../utils/diagnostics'
import { buildSpecJourney } from '../utils/diagnostics'
import { formatDuration, formatTokens, shortModelName } from '../utils/transformer'
import { formatCost } from '../utils/costEstimation'
import { Badge } from '@/components/ui/badge'
import {
  CheckCircle2, XCircle, Clock, ChevronDown, ChevronRight,
  AlertTriangle, Layers, BarChart3, Users,
} from 'lucide-react'

// ─── Sub-components ───

function TaskRow({ jt, onTaskClick }: { jt: JourneyTask; onTaskClick?: (taskId: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const title = jt.task.title || jt.task.name
  const lastAttempt = jt.attempts[jt.attempts.length - 1]
  const model = shortModelName(jt.task.modelName)
  const duration = lastAttempt.metrics.durationMs
  const tokens = lastAttempt.metrics.tokens

  const outcomeIcon = jt.outcome === 'pass'
    ? <CheckCircle2 className="size-3.5 text-green-500 shrink-0" />
    : jt.outcome === 'fail'
      ? <XCircle className="size-3.5 text-red-500 shrink-0" />
      : <Clock className="size-3.5 text-muted-foreground shrink-0" />

  return (
    <div className="border-l-2 border-border pl-3 py-1">
      {/* Collapsed row */}
      <div
        className="flex items-center gap-2 cursor-pointer hover:bg-muted/30 rounded px-1 py-0.5 -ml-1"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded
          ? <ChevronDown className="size-3 text-muted-foreground shrink-0" />
          : <ChevronRight className="size-3 text-muted-foreground shrink-0" />}
        {outcomeIcon}
        <Badge
          variant="outline"
          className="text-[10px] px-1.5 py-0 h-4 cursor-pointer hover:bg-muted/50 shrink-0"
          onClick={(e) => { e.stopPropagation(); onTaskClick?.(jt.task.id) }}
        >
          #{jt.task.idShort}
        </Badge>
        <span className="text-xs truncate flex-1">{title}</span>
        <span className="text-[10px] text-muted-foreground font-mono shrink-0">{model}</span>
        <span className="text-[10px] text-muted-foreground font-mono shrink-0">
          {duration ? formatDuration(duration) : '—'}
        </span>
        <span className="text-[10px] text-muted-foreground font-mono shrink-0">
          {formatTokens(tokens)}
        </span>
      </div>

      {/* Reflection line (always visible when present) */}
      {lastAttempt.reflectionVerdict && (
        <div className="ml-6 text-[10px] text-muted-foreground">
          └─ Reflection: <span className={
            lastAttempt.reflectionVerdict === 'PASS' ? 'text-green-500' :
              lastAttempt.reflectionVerdict === 'FAIL' ? 'text-red-500' : 'text-yellow-500'
          }>{lastAttempt.reflectionVerdict}</span>
        </div>
      )}

      {/* Retry indicator */}
      {jt.attempts.length > 1 && (
        <div className="ml-6 text-[10px] text-muted-foreground">
          └─ {jt.attempts.length} attempts
        </div>
      )}

      {/* Expanded detail */}
      {expanded && (
        <div className="ml-6 mt-1 space-y-1.5 text-xs">
          {jt.attempts.map((attempt, i) => (
            <AttemptDetail key={i} attempt={attempt} isOnly={jt.attempts.length === 1} />
          ))}
        </div>
      )}
    </div>
  )
}

function AttemptDetail({ attempt, isOnly }: { attempt: TaskAttempt; isOnly: boolean }) {
  const outcomeColor = attempt.outcome === 'pass' ? 'text-green-500'
    : attempt.outcome === 'fail' ? 'text-red-500' : 'text-muted-foreground'

  return (
    <div className="border border-border/50 rounded p-2 bg-muted/10">
      {!isOnly && (
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] font-medium">Attempt {attempt.attemptNumber}</span>
          <span className={`text-[10px] font-medium ${outcomeColor}`}>
            {attempt.outcome.toUpperCase()}
          </span>
          {attempt.metrics.durationMs && (
            <span className="text-[10px] text-muted-foreground">{formatDuration(attempt.metrics.durationMs)}</span>
          )}
          {attempt.metrics.tokens && (
            <span className="text-[10px] text-muted-foreground">{formatTokens(attempt.metrics.tokens)}</span>
          )}
        </div>
      )}

      {/* Mutations */}
      {attempt.mutations.length > 0 && (
        <div className="space-y-0.5">
          {attempt.mutations.map((m, i) => (
            <div key={i} className="text-[10px] text-muted-foreground font-mono">
              {new Date(m.date).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              {' '}{m.description}
            </div>
          ))}
        </div>
      )}

      {/* Comments */}
      {attempt.comments.length > 0 && (
        <div className="mt-1 space-y-0.5">
          {attempt.comments.map((c, i) => (
            <div key={i} className="text-[10px] text-muted-foreground">
              <Badge variant="outline" className="text-[8px] px-1 py-0 h-3 mr-1">
                {c.commentType}
              </Badge>
              {c.content.split('\n')[0].slice(0, 100)}
            </div>
          ))}
        </div>
      )}

      {/* Reflection detail */}
      {attempt.reflectionVerdict && (
        <div className="mt-1 text-[10px]">
          <span className="font-medium">Reflection:</span>{' '}
          <span className={
            attempt.reflectionVerdict === 'PASS' ? 'text-green-500' :
              attempt.reflectionVerdict === 'FAIL' ? 'text-red-500' : 'text-yellow-500'
          }>{attempt.reflectionVerdict}</span>
          {attempt.reflectionSummary && (
            <span className="text-muted-foreground ml-1">— {attempt.reflectionSummary}</span>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Chapter renderers ───

function PlanningChapterView({ chapter }: { chapter: Extract<JourneyChapter, { type: 'planning' }> }) {
  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
        <Layers className="size-3.5" />
        <span className="font-medium text-foreground">Planning</span>
      </div>
      <div className="flex gap-3 text-xs text-muted-foreground ml-5">
        <span>{chapter.taskCount} tasks</span>
        <span>{chapter.waveCount} wave{chapter.waveCount !== 1 ? 's' : ''}</span>
        {chapter.agents.length > 0 && (
          <span className="flex items-center gap-1">
            <Users className="size-3" />
            {chapter.agents.map(a => shortModelName(a) || a).join(', ')}
          </span>
        )}
      </div>
    </div>
  )
}

function WaveChapterView({ chapter, onTaskClick }: {
  chapter: Extract<JourneyChapter, { type: 'wave' }>
  onTaskClick?: (taskId: string) => void
}) {
  const label = chapter.isRetry
    ? `Wave ${chapter.wave} (retry)`
    : `Wave ${chapter.wave}`

  return (
    <div className="mb-4">
      {/* Wave header */}
      <div className="flex items-center gap-2 text-xs mb-2">
        <div className="h-px flex-1 bg-border" />
        <span className={`font-medium ${chapter.isRetry ? 'text-yellow-500' : 'text-foreground'}`}>
          {label}
        </span>
        <div className="flex gap-2 text-[10px] text-muted-foreground font-mono">
          {chapter.durationMs > 0 && <span>{formatDuration(chapter.durationMs)}</span>}
          {chapter.totalTokens > 0 && <span>{formatTokens(chapter.totalTokens)}</span>}
          {chapter.totalCostUsd > 0 && <span>{formatCost(chapter.totalCostUsd)}</span>}
        </div>
        <div className="h-px flex-1 bg-border" />
      </div>

      {/* Task rows */}
      <div className="space-y-0.5">
        {chapter.tasks.map(jt => (
          <TaskRow key={jt.task.id} jt={jt} onTaskClick={onTaskClick} />
        ))}
      </div>
    </div>
  )
}

function PivotChapterView({ chapter }: { chapter: Extract<JourneyChapter, { type: 'pivot' }> }) {
  const failedTitle = chapter.failedTask.task.title || chapter.failedTask.task.name

  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 text-xs mb-1">
        <div className="h-px flex-1 bg-red-500/30" />
        <AlertTriangle className="size-3.5 text-red-500" />
        <span className="font-medium text-red-500">Pivot</span>
        <div className="h-px flex-1 bg-red-500/30" />
      </div>
      <div className="text-xs text-muted-foreground ml-5">
        <span className="text-red-500 font-medium">#{chapter.failedTask.task.idShort}</span>{' '}
        <span className="truncate">{failedTitle}</span>
        {' failed → '}
        {chapter.blockedTasks.map((bt, i) => (
          <span key={bt.task.id}>
            {i > 0 && ', '}
            <span className="text-yellow-500">#{bt.task.idShort}</span>
          </span>
        ))}
        {' blocked'}
      </div>
    </div>
  )
}

function SummaryChapterView({ chapter }: { chapter: Extract<JourneyChapter, { type: 'summary' }> }) {
  return (
    <div className="mt-4 pt-3 border-t border-border">
      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
        <BarChart3 className="size-3.5" />
        <span className="font-medium text-foreground">Summary</span>
      </div>
      <div className="grid grid-cols-3 gap-2 ml-5">
        <MetricBadge label="Completed" value={String(chapter.completed)} color="text-green-500" />
        <MetricBadge label="Failed" value={String(chapter.failed)} color={chapter.failed > 0 ? 'text-red-500' : 'text-muted-foreground'} />
        <MetricBadge label="Retries" value={String(chapter.retries)} color={chapter.retries > 0 ? 'text-yellow-500' : 'text-muted-foreground'} />
        <MetricBadge label="Duration" value={chapter.totalDurationMs > 0 ? formatDuration(chapter.totalDurationMs) : '—'} />
        <MetricBadge label="Tokens" value={formatTokens(chapter.totalTokens)} />
        <MetricBadge label="Cost" value={formatCost(chapter.totalCostUsd)} />
      </div>
    </div>
  )
}

function MetricBadge({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center">
      <div className={`text-sm font-medium font-mono ${color || 'text-foreground'}`}>{value}</div>
      <div className="text-[10px] text-muted-foreground">{label}</div>
    </div>
  )
}

// ─── Main component ───

interface SpecJourneyProps {
  tasks: Task[]
  onTaskClick?: (taskId: string) => void
}

export function SpecJourney({ tasks, onTaskClick }: SpecJourneyProps) {
  const journey = buildSpecJourney(tasks)

  if (journey.chapters.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground text-sm">
        No execution data available.
      </div>
    )
  }

  return (
    <div>
      {journey.chapters.map((chapter, i) => {
        switch (chapter.type) {
          case 'planning':
            return <PlanningChapterView key={`planning-${i}`} chapter={chapter} />
          case 'wave':
            return <WaveChapterView key={`wave-${chapter.wave}-${chapter.isRetry}-${i}`} chapter={chapter} onTaskClick={onTaskClick} />
          case 'pivot':
            return <PivotChapterView key={`pivot-${chapter.failedTask.task.id}-${i}`} chapter={chapter} />
          case 'summary':
            return <SummaryChapterView key={`summary-${i}`} chapter={chapter} />
        }
      })}
    </div>
  )
}
