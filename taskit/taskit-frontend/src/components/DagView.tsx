import { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import type { Task, Member } from '../types'
import { getStatusColor, formatDuration } from '../utils/transformer'
import {
  detectCycles,
  separateConnectedAndOrphans,
  computeDagLayout,
  classifyEdge,
} from '../utils/dagUtils'
import { classifyEdgeByExecution } from '../utils/diagnostics'
import type { LayoutNode, LayoutEdge } from '../utils/dagUtils'
import { AlertTriangle, ChevronDown, ChevronRight, Maximize } from 'lucide-react'
import { Button } from '@/components/ui/button'

// ─── Constants ───

const NODE_WIDTH = 200
const NODE_HEIGHT = 50
const ORPHAN_NODE_WIDTH = 160
const ORPHAN_NODE_HEIGHT = 36
const ORPHAN_COLS = 4
const ORPHAN_GAP_X = 16
const ORPHAN_GAP_Y = 12
const MIN_ZOOM = 0.1
const MAX_ZOOM = 4
const ZOOM_STEP = 1.15

// ─── Edge colors by dependency status ───

const EDGE_STYLES: Record<string, { stroke: string; dash: string; opacity: number }> = {
  satisfied: { stroke: 'var(--chart-4)', dash: '', opacity: 0.7 },
  active: { stroke: 'var(--chart-1)', dash: '6 3', opacity: 0.7 },
  blocked: { stroke: 'var(--destructive)', dash: '3 3', opacity: 0.8 },
  pending: { stroke: 'var(--muted-foreground)', dash: '4 4', opacity: 0.4 },
}

// ─── Props ───

export interface DagViewProps {
  tasks: Task[]
  allTasks: Task[]
  members: Member[]
  onTaskClick: (task: Task) => void
  onFitAll?: () => void
  debugMode?: boolean
}

export function DagView({ tasks, allTasks, members, onTaskClick, debugMode }: DagViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [zoom, setZoom] = useState(1)
  const [offsetX, setOffsetX] = useState(0)
  const [offsetY, setOffsetY] = useState(0)
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null)
  const [orphansExpanded, setOrphansExpanded] = useState(false)
  const dragRef = useRef({ isDragging: false, startX: 0, startY: 0, startOffsetX: 0, startOffsetY: 0 })

  // Build member lookup
  const memberMap = useMemo(() => {
    const map = new Map<string, Member>()
    for (const m of members) {
      map.set(m.id, m)
      map.set(m.fullName, m)
      map.set(m.email, m)
    }
    return map
  }, [members])

  // Build allTasks lookup for resolving dependency source status
  const taskMap = useMemo(() => {
    const map = new Map<string, Task>()
    for (const t of allTasks) map.set(t.id, t)
    for (const t of tasks) map.set(t.id, t) // prefer filtered tasks
    return map
  }, [allTasks, tasks])

  // Cycle detection
  const cycles = useMemo(() => detectCycles(tasks), [tasks])
  const hasCycles = cycles.length > 0
  const cycleNodeIds = useMemo(() => new Set(cycles.flat()), [cycles])

  // Separate connected vs orphan
  const { connected, orphans } = useMemo(() => separateConnectedAndOrphans(tasks), [tasks])

  // Compute DAG layout for connected tasks
  const layout = useMemo(() => computeDagLayout(connected), [connected])

  // Fit-all: compute initial zoom/offset to frame the graph
  const fitAll = useCallback(() => {
    if (!containerRef.current || layout.width === 0) return
    const rect = containerRef.current.getBoundingClientRect()
    const padding = 40
    const scaleX = (rect.width - padding * 2) / layout.width
    const scaleY = (rect.height - padding * 2) / layout.height
    const newZoom = Math.min(Math.max(Math.min(scaleX, scaleY), MIN_ZOOM), MAX_ZOOM)
    setZoom(newZoom)
    setOffsetX((rect.width - layout.width * newZoom) / 2)
    setOffsetY((rect.height - layout.height * newZoom) / 2)
  }, [layout])

  // Auto fit-all on first render and when layout changes
  useEffect(() => {
    const timeout = setTimeout(fitAll, 50)
    return () => clearTimeout(timeout)
  }, [fitAll])

  // Expose fitAll to parent (imperatively via ref is possible but simpler to just use the button)
  // The parent passes onFitAll — we call fitAll when the button is clicked

  // Wheel zoom
  const stateRef = useRef({ zoom, offsetX, offsetY })
  useEffect(() => { stateRef.current = { zoom, offsetX, offsetY } }, [zoom, offsetX, offsetY])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) {
        // Plain scroll: pan
        e.preventDefault()
        const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : 0
        const dy = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : 0
        setOffsetX(prev => prev - dx)
        setOffsetY(prev => prev - dy)
        return
      }
      e.preventDefault()
      const { zoom, offsetX, offsetY } = stateRef.current
      const scaleFactor = Math.exp(-e.deltaY * 0.005)
      const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom * scaleFactor))
      if (newZoom === zoom) return
      const rect = container.getBoundingClientRect()
      const mouseX = e.clientX - rect.left
      const mouseY = e.clientY - rect.top
      // Zoom toward cursor
      const graphX = (mouseX - offsetX) / zoom
      const graphY = (mouseY - offsetY) / zoom
      setZoom(newZoom)
      setOffsetX(mouseX - graphX * newZoom)
      setOffsetY(mouseY - graphY * newZoom)
    }
    container.addEventListener('wheel', onWheel, { passive: false })
    return () => container.removeEventListener('wheel', onWheel)
  }, [])

  // Drag to pan
  const handleMouseDown = (e: React.MouseEvent) => {
    dragRef.current = { isDragging: true, startX: e.clientX, startY: e.clientY, startOffsetX: offsetX, startOffsetY: offsetY }
  }
  const handleMouseMove = (e: React.MouseEvent) => {
    if (!dragRef.current.isDragging) return
    setOffsetX(dragRef.current.startOffsetX + (e.clientX - dragRef.current.startX))
    setOffsetY(dragRef.current.startOffsetY + (e.clientY - dragRef.current.startY))
  }
  const handleMouseUp = () => { dragRef.current.isDragging = false }

  // ─── Empty state ───
  if (connected.length === 0 && orphans.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        <AlertTriangle className="size-10 mb-3 opacity-50" />
        <div className="text-base font-medium mb-1">No tasks to display</div>
        <p className="text-sm">Try adjusting your filters.</p>
      </div>
    )
  }

  if (connected.length === 0 && orphans.length > 0) {
    return (
      <div>
        <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
          <div className="text-sm mb-1">No dependency relationships found among {orphans.length} tasks.</div>
          <p className="text-xs opacity-70">Switch to Timeline view for a temporal perspective.</p>
        </div>
        <OrphanGrid tasks={orphans} onTaskClick={onTaskClick} memberMap={memberMap} expanded={true} onToggle={() => {}} alwaysExpanded />
      </div>
    )
  }

  // Build edge path from dagre control points
  function edgePath(edge: LayoutEdge): string {
    const pts = edge.points
    if (pts.length === 0) return ''
    if (pts.length === 1) return `M ${pts[0].x},${pts[0].y}`
    if (pts.length === 2) return `M ${pts[0].x},${pts[0].y} L ${pts[1].x},${pts[1].y}`
    // Cubic bezier through control points
    let d = `M ${pts[0].x},${pts[0].y}`
    for (let i = 1; i < pts.length - 1; i += 2) {
      const cp1 = pts[i]
      const cp2 = pts[Math.min(i + 1, pts.length - 1)]
      const end = pts[Math.min(i + 2, pts.length - 1)]
      if (i + 2 < pts.length) {
        d += ` C ${cp1.x},${cp1.y} ${cp2.x},${cp2.y} ${end.x},${end.y}`
      } else {
        d += ` L ${cp2.x},${cp2.y}`
      }
    }
    return d
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Cycle warning */}
      {hasCycles && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-destructive/10 text-destructive text-xs">
          <AlertTriangle className="size-4 shrink-0" />
          <span>Circular dependencies detected — {cycles.length} cycle(s) found. Affected tasks are highlighted.</span>
        </div>
      )}

      {/* DAG viewport */}
      <div
        ref={containerRef}
        className="relative overflow-hidden rounded-lg border border-border bg-card"
        style={{ height: 500, cursor: dragRef.current.isDragging ? 'grabbing' : 'grab' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {/* Fit All button */}
        <div className="absolute top-2 right-2 z-10">
          <Button variant="outline" size="sm" className="h-7 text-xs gap-1" onClick={fitAll}>
            <Maximize className="size-3" /> Fit All
          </Button>
        </div>

        <svg
          width="100%"
          height="100%"
          className="block"
        >
          <defs>
            <marker id="dag-arrow-satisfied" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--chart-4)" opacity="0.7" />
            </marker>
            <marker id="dag-arrow-active" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--chart-1)" opacity="0.7" />
            </marker>
            <marker id="dag-arrow-blocked" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--destructive)" opacity="0.8" />
            </marker>
            <marker id="dag-arrow-pending" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted-foreground)" opacity="0.4" />
            </marker>
          </defs>

          <g transform={`translate(${offsetX}, ${offsetY}) scale(${zoom})`}>
            {/* Edges (rendered first, behind nodes) */}
            {layout.edges.map((edge) => {
              const sourceTask = taskMap.get(edge.source)
              const classification = sourceTask
                ? (debugMode ? classifyEdgeByExecution(sourceTask) : classifyEdge(sourceTask))
                : 'pending'
              const style = EDGE_STYLES[classification]
              return (
                <path
                  key={`${edge.source}->${edge.target}`}
                  d={edgePath(edge)}
                  fill="none"
                  stroke={style.stroke}
                  strokeWidth={1.5}
                  strokeDasharray={style.dash}
                  opacity={style.opacity}
                  markerEnd={`url(#dag-arrow-${classification})`}
                />
              )
            })}

            {/* Nodes */}
            {layout.nodes.map((node) => {
              const task = taskMap.get(node.id)
              if (!task) return null
              const statusColor = getStatusColor(task.currentStatus)
              const isHovered = hoveredNode === node.id
              const isCycleNode = cycleNodeIds.has(node.id)
              const isFailed = task.currentStatus === 'FAILED'
              const isExecuting = task.currentStatus.toLowerCase().includes('executing')
              const assignee = task.assignees[0] || ''
              const member = memberMap.get(assignee)
              const title = task.title || task.name
              const displayTitle = title.length > 25 ? title.slice(0, 24) + '\u2026' : title
              // dagre positions are center-based
              const x = node.x - NODE_WIDTH / 2
              const y = node.y - NODE_HEIGHT / 2

              return (
                <g
                  key={node.id}
                  className="cursor-pointer"
                  onClick={(e) => { e.stopPropagation(); onTaskClick(task) }}
                  onMouseEnter={(e) => {
                    setHoveredNode(node.id)
                    const rect = containerRef.current?.getBoundingClientRect()
                    if (rect) {
                      setTooltipPos({ x: e.clientX - rect.left + 12, y: e.clientY - rect.top - 8 })
                    }
                  }}
                  onMouseLeave={() => { setHoveredNode(null); setTooltipPos(null) }}
                >
                  {/* Node background */}
                  <rect
                    x={x}
                    y={y}
                    width={NODE_WIDTH}
                    height={NODE_HEIGHT}
                    rx={8}
                    fill={isHovered ? 'color-mix(in srgb, var(--card), var(--foreground) 8%)' : 'var(--card)'}
                    stroke={isCycleNode ? 'var(--destructive)' : (debugMode && isFailed) ? 'var(--destructive)' : isExecuting ? '#22c55e' : (isHovered ? 'var(--foreground)' : 'var(--border)')}
                    strokeWidth={isCycleNode ? 2 : isExecuting ? 2 : 1}
                    className={isExecuting ? 'animate-pulse-subtle' : undefined}
                    opacity={0.95}
                  />
                  {/* Status left border */}
                  <rect
                    x={x}
                    y={y}
                    width={4}
                    height={NODE_HEIGHT}
                    rx={2}
                    fill={statusColor}
                  />
                  {/* Task ID */}
                  <text
                    x={x + 12}
                    y={y + 16}
                    fontSize={10}
                    fill="var(--muted-foreground)"
                    fontFamily="'Inter', sans-serif"
                  >
                    #{task.idShort}
                  </text>
                  {/* Title */}
                  <text
                    x={x + 12}
                    y={y + 34}
                    fontSize={12}
                    fontWeight={500}
                    fill="var(--foreground)"
                    fontFamily="'Inter', sans-serif"
                  >
                    {displayTitle}
                  </text>
                  {/* Assignee initials */}
                  {member && (
                    <g>
                      <circle
                        cx={x + NODE_WIDTH - 20}
                        cy={y + NODE_HEIGHT / 2}
                        r={12}
                        fill={member.color || 'var(--muted)'}
                        opacity={0.2}
                      />
                      <text
                        x={x + NODE_WIDTH - 20}
                        y={y + NODE_HEIGHT / 2 + 4}
                        fontSize={9}
                        fontWeight={600}
                        fill={member.color || 'var(--muted-foreground)'}
                        textAnchor="middle"
                        fontFamily="'Inter', sans-serif"
                      >
                        {member.initials}
                      </text>
                    </g>
                  )}
                </g>
              )
            })}
          </g>
        </svg>

        {/* Tooltip */}
        {hoveredNode && tooltipPos && (() => {
          const task = taskMap.get(hoveredNode)
          if (!task) return null
          return (
            <div
              className="gantt-tooltip"
              style={{ left: tooltipPos.x, top: tooltipPos.y }}
            >
              <div className="text-sm font-semibold mb-1">{task.title || task.name}</div>
              <div className="text-xs text-muted-foreground mb-1">
                <span className="inline-block size-2 rounded-full mr-1" style={{ background: getStatusColor(task.currentStatus) }} />
                {task.currentStatus}
              </div>
              {task.assignees[0] && (
                <div className="text-xs text-muted-foreground">Assignee: <strong>{task.assignees[0]}</strong></div>
              )}
              {task.workTimeMs > 0 && (
                <div className="text-xs text-muted-foreground">Work time: {formatDuration(task.workTimeMs)}</div>
              )}
              {task.dependsOn && task.dependsOn.length > 0 && (
                <div className="text-xs text-muted-foreground">Dependencies: {task.dependsOn.length}</div>
              )}
            </div>
          )
        })()}
      </div>

      {/* Orphan section */}
      {orphans.length > 0 && (
        <OrphanGrid
          tasks={orphans}
          onTaskClick={onTaskClick}
          memberMap={memberMap}
          expanded={orphansExpanded}
          onToggle={() => setOrphansExpanded(!orphansExpanded)}
        />
      )}
    </div>
  )
}

// ─── Orphan Grid ───

interface OrphanGridProps {
  tasks: Task[]
  onTaskClick: (task: Task) => void
  memberMap: Map<string, Member>
  expanded: boolean
  onToggle: () => void
  alwaysExpanded?: boolean
}

function OrphanGrid({ tasks, onTaskClick, memberMap, expanded, onToggle, alwaysExpanded }: OrphanGridProps) {
  const isExpanded = alwaysExpanded || expanded
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      {!alwaysExpanded && (
        <button
          className="w-full flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
          onClick={onToggle}
        >
          {isExpanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
          <span>{tasks.length} unconnected task{tasks.length !== 1 ? 's' : ''}</span>
        </button>
      )}
      {isExpanded && (
        <div
          className="grid gap-2 p-3"
          style={{ gridTemplateColumns: `repeat(${ORPHAN_COLS}, 1fr)` }}
        >
          {tasks.map(task => {
            const statusColor = getStatusColor(task.currentStatus)
            const title = task.title || task.name
            const displayTitle = title.length > 20 ? title.slice(0, 19) + '\u2026' : title
            return (
              <div
                key={task.id}
                className="flex items-center gap-2 px-2 py-1.5 rounded border border-border cursor-pointer hover:bg-muted/50 transition-colors"
                style={{ borderLeftWidth: 3, borderLeftColor: statusColor }}
                onClick={() => onTaskClick(task)}
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[10px] text-muted-foreground">#{task.idShort}</div>
                  <div className="text-xs font-medium truncate">{displayTitle}</div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
