import { useState, useRef, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { Task, TaskMutation, Member } from '../types';
import { getStatusColor, formatDuration } from '../utils/transformer';
import { CountdownTimer } from './CountdownTimer';
import { DagView } from './DagView';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
    Search, ZoomIn, ZoomOut, Eye, EyeOff, Users, BarChart3, Clock, Timer,
    CheckCircle2, CircleAlert, ListFilter, GitBranch, GanttChart,
} from 'lucide-react';

type TimelineMode = 'timeline' | 'dag';

interface TimelineViewProps {
    tasks: Task[];
    allTasks?: Task[];
    members?: Member[];
    onTaskClick?: (task: Task) => void;
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 200000;
const ZOOM_STEP = 1.2;

const TRACK_COLORS = [
    'var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)',
    'var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)',
];

// ─── Status classification ───

/** Queue/waiting states — rendered as thin lines, not bars */
function isQueueStatus(status: string): boolean {
    const lower = status.toLowerCase();
    return ['backlog', 'todo', 'to do', 'planned'].some(s => lower.includes(s));
}

/** Terminal states — task is finished, clip segment at last mutation */
function isTerminalStatus(status: string): boolean {
    const lower = status.toLowerCase();
    return ['done', 'complete', 'finished', 'closed', 'archived', 'archive',
            'failed', 'error', 'blocked'].some(s => lower.includes(s));
}

function isFailedStatus(status: string): boolean {
    const lower = status.toLowerCase();
    return ['failed', 'error', 'blocked'].some(s => lower.includes(s));
}

/** Completed = terminal and not failed */
function isCompletedStatus(status: string): boolean {
    return isTerminalStatus(status) && !isFailedStatus(status);
}

type CompletionFilter = 'all' | 'complete' | 'active';

function formatFullDate(dateStr: string): string {
    return new Date(dateStr).toLocaleDateString('en-US', {
        weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
    });
}

export function TimelineView({ tasks, allTasks, members, onTaskClick }: TimelineViewProps) {
    // ─── ALL hooks must be called unconditionally, before any returns ───
    const [searchParams, setSearchParams] = useSearchParams();
    const viewMode: TimelineMode = searchParams.get('mode') === 'dag' ? 'dag' : 'timeline';
    const setViewMode = useCallback((mode: TimelineMode) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (mode === 'dag') next.set('mode', 'dag');
            else next.delete('mode');
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    const [hoveredMutation, setHoveredMutation] = useState<{
        mutation: TaskMutation; taskName: string; trackColor: string; x: number; y: number;
    } | null>(null);

    const [searchTerm, setSearchTerm] = useState('');
    const [selectedMembers, setSelectedMembers] = useState<Set<string>>(new Set());
    const [selectedStatuses, setSelectedStatuses] = useState<Set<string>>(new Set());
    const [completionFilter, setCompletionFilter] = useState<CompletionFilter>('all');
    const [hideTrivial, setHideTrivial] = useState(false);
    const [zoom, setZoom] = useState(1);
    const [containerWidth, setContainerWidth] = useState(1000);
    const [viewOffsetMs, setViewOffsetMs] = useState(0);
    const [now, setNow] = useState(Date.now());

    const containerRef = useRef<HTMLDivElement>(null);
    const dragRef = useRef({ isDragging: false, startX: 0, startOffset: 0 });
    const viewportRef = useRef<HTMLDivElement>(null);

    // Refresh "now" every 30 seconds
    useEffect(() => {
        const interval = setInterval(() => setNow(Date.now()), 30000);
        return () => clearInterval(interval);
    }, []);

    // ResizeObserver for container width
    useEffect(() => {
        if (!containerRef.current) return;
        const updateWidth = () => setContainerWidth(containerRef.current?.getBoundingClientRect().width || 1000);
        const observer = new ResizeObserver(updateWidth);
        observer.observe(containerRef.current);
        updateWidth();
        return () => observer.disconnect();
    }, []);

    const allMemberNames = Array.from(new Set(tasks.flatMap(t => t.assignees))).sort();
    const allStatuses = Array.from(new Set(tasks.map(t => t.currentStatus))).sort();

    const toggleMember = (member: string) => {
        const next = new Set(selectedMembers);
        if (next.has(member)) next.delete(member); else next.add(member);
        setSelectedMembers(next);
    };

    const toggleStatus = (status: string) => {
        const next = new Set(selectedStatuses);
        if (next.has(status)) next.delete(status); else next.add(status);
        setSelectedStatuses(next);
    };

    const filteredTasks = tasks.filter(task => {
        if (searchTerm) {
            const term = searchTerm.toLowerCase();
            if (!task.name.toLowerCase().includes(term) && !task.idShort.toString().includes(term)) return false;
        }
        if (selectedMembers.size > 0 && !task.assignees.some(m => selectedMembers.has(m))) return false;
        if (selectedStatuses.size > 0 && !selectedStatuses.has(task.currentStatus)) return false;
        if (completionFilter === 'complete' && !isTerminalStatus(task.currentStatus)) return false;
        if (completionFilter === 'active' && isTerminalStatus(task.currentStatus)) return false;
        if (hideTrivial) {
            const isCompleted = task.currentStatus.toLowerCase().includes('done') || task.currentStatus.toLowerCase().includes('archive');
            if (isCompleted && task.mutations.length < 2 && task.totalLifespanMs < 3600000) return false;
        }
        return true;
    });

    const sortedTasks = [...filteredTasks].sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());

    const allTimestamps: number[] = [];
    for (const task of sortedTasks) {
        const created = new Date(task.createdAt).getTime();
        if (!isNaN(created)) allTimestamps.push(created);
        for (const m of task.mutations) {
            if (typeof m.timestamp === 'number' && !isNaN(m.timestamp)) {
                allTimestamps.push(m.timestamp);
            }
        }
    }

    // Compacted constants
    const TRACK_HEIGHT = 40;
    const TRACK_GAP = 2;
    const LABEL_WIDTH = 200;
    const HEADER_HEIGHT = 32;
    const DOT_RADIUS = 5;

    const minTime = allTimestamps.length > 0 ? Math.min(...allTimestamps) : 0;
    const maxTime = allTimestamps.length > 0 ? Math.max(...allTimestamps) : 0;
    const contentRange = (maxTime - minTime) || 3600000;
    const basePixelsPerMs = allTimestamps.length > 0 ? (containerWidth - LABEL_WIDTH) / (contentRange * 1.1) : 1;

    // Auto-zoom to fit filtered tasks when completion filter changes
    const autoZoomToFit = useCallback((tasksToFit: Task[]) => {
        const timestamps: number[] = [];
        for (const task of tasksToFit) {
            const created = new Date(task.createdAt).getTime();
            if (!isNaN(created)) timestamps.push(created);
            for (const m of task.mutations) {
                if (typeof m.timestamp === 'number' && !isNaN(m.timestamp)) timestamps.push(m.timestamp);
            }
        }
        if (timestamps.length === 0) return;
        const fitMin = Math.min(...timestamps);
        const fitMax = Math.max(...timestamps);
        const fitRange = (fitMax - fitMin) || 3600000;
        // Add 10% padding
        const paddedRange = fitRange * 1.2;
        const chartWidth = containerWidth - LABEL_WIDTH;
        const neededZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, (chartWidth / paddedRange) / basePixelsPerMs));
        const paddingOffset = fitRange * 0.1;
        setZoom(neededZoom);
        setViewOffsetMs(fitMin - paddingOffset - minTime);
    }, [containerWidth, basePixelsPerMs, minTime]);

    const handleCompletionFilter = useCallback((filter: CompletionFilter) => {
        setCompletionFilter(prev => {
            const next = prev === filter ? 'all' : filter;
            // Auto-zoom after filter applies (use tasks, not sortedTasks which hasn't updated yet)
            if (next !== 'all') {
                const filtered = tasks.filter(t => {
                    if (next === 'complete') return isTerminalStatus(t.currentStatus);
                    return !isTerminalStatus(t.currentStatus);
                });
                // Defer so state updates first
                setTimeout(() => autoZoomToFit(filtered), 0);
            } else {
                setTimeout(() => { setZoom(1); setViewOffsetMs(0); }, 0);
            }
            return next;
        });
    }, [tasks, autoZoomToFit]);

    // Zoom preset handler
    const handleZoomPreset = useCallback((preset: 'hours' | 'days' | 'all') => {
        if (allTimestamps.length === 0) return;

        if (preset === 'all') {
            setZoom(1);
            setViewOffsetMs(0);
            return;
        }

        const span = preset === 'hours' ? 6 * 3600000 : 3 * 86400000;
        const center = now;
        const desiredStart = center - span / 2;
        const chartWidth = containerWidth - LABEL_WIDTH;
        const neededZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, (chartWidth / span) / basePixelsPerMs));

        setZoom(neededZoom);
        setViewOffsetMs(desiredStart - minTime);
    }, [allTimestamps.length, now, containerWidth, basePixelsPerMs, minTime]);

    const pixelsPerMs = basePixelsPerMs * zoom;

    const handleMouseDown = (e: React.MouseEvent) => {
        dragRef.current = { isDragging: true, startX: e.clientX, startOffset: viewOffsetMs };
        setHoveredMutation(null);
    };
    const handleMouseMove = (e: React.MouseEvent) => {
        if (!dragRef.current.isDragging) return;
        const deltaMs = (e.clientX - dragRef.current.startX) / pixelsPerMs;
        setViewOffsetMs(dragRef.current.startOffset - deltaMs);
    };
    const handleMouseUp = () => { dragRef.current.isDragging = false; };

    // Wheel handler for Gantt zoom/pan
    const stateRef = useRef({ zoom, minTime, viewOffsetMs, basePixelsPerMs, MAX_ZOOM, MIN_ZOOM });
    useEffect(() => { stateRef.current = { zoom, minTime, viewOffsetMs, basePixelsPerMs, MAX_ZOOM, MIN_ZOOM }; }, [zoom, minTime, viewOffsetMs, basePixelsPerMs]);

    useEffect(() => {
        const viewport = viewportRef.current;
        if (!viewport) return;
        const onWheel = (e: WheelEvent) => {
            const { zoom, minTime, viewOffsetMs, basePixelsPerMs, MAX_ZOOM, MIN_ZOOM } = stateRef.current;
            const pixelsPerMs = basePixelsPerMs * zoom;
            if (e.ctrlKey) {
                e.preventDefault();
                const scaleFactor = Math.exp(-e.deltaY * 0.005);
                const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom * scaleFactor));
                if (newZoom !== zoom) {
                    const viewportRect = viewport.getBoundingClientRect();
                    const mouseX = e.clientX - viewportRect.left;
                    const timeAtCursor = (minTime + viewOffsetMs) + (mouseX / pixelsPerMs);
                    const newPixelsPerMs = basePixelsPerMs * newZoom;
                    setZoom(newZoom);
                    setViewOffsetMs(timeAtCursor - (mouseX / newPixelsPerMs) - minTime);
                }
            } else {
                // Plain scroll (no ctrl): horizontal pan
                e.preventDefault();
                const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
                setViewOffsetMs(prev => prev + delta / pixelsPerMs);
            }
        };
        viewport.addEventListener('wheel', onWheel, { passive: false });
        return () => viewport.removeEventListener('wheel', onWheel);
    }, []);

    // ─── Shared filter bar props ───
    const filterBarProps = {
        searchTerm, onSearchChange: setSearchTerm,
        members: allMemberNames, statuses: allStatuses,
        selectedMembers, selectedStatuses,
        completionFilter, onCompletionFilter: handleCompletionFilter,
        hideTrivial, zoom,
        onToggleMember: toggleMember, onToggleStatus: toggleStatus,
        onToggleTrivial: () => setHideTrivial(!hideTrivial),
        onZoomChange: setZoom, onZoomPreset: handleZoomPreset,
        viewMode, onViewModeChange: setViewMode,
    };

    // ─── DAG mode ───
    if (viewMode === 'dag') {
        return (
            <div>
                <TimelineFilterBar {...filterBarProps} />
                <DagView
                    tasks={sortedTasks}
                    allTasks={allTasks || tasks}
                    members={members || []}
                    onTaskClick={(task) => onTaskClick?.(task)}
                />
            </div>
        );
    }

    // ─── Timeline mode: empty state ───
    if (allTimestamps.length === 0) {
        return (
            <div>
                <TimelineFilterBar {...filterBarProps} />
                <div className="text-center py-10 text-muted-foreground">
                    <Search className="size-10 mx-auto mb-3 opacity-50" />
                    <div className="text-base font-medium mb-1">No tasks found</div>
                    <p className="text-sm">Try adjusting your filters.</p>
                </div>
            </div>
        );
    }

    // ─── Timeline mode: Gantt chart ───
    const svgWidth = Math.max(containerWidth - LABEL_WIDTH, 1);
    const viewStartTime = minTime + viewOffsetMs;
    const viewEndTime = viewStartTime + (svgWidth / pixelsPerMs);
    const timeToX = (timestamp: number) => (timestamp - viewStartTime) * pixelsPerMs;

    const msPerTickTarget = 150 / pixelsPerMs;
    const TICK_INTERVALS = [
        { ms: 60000, format: (d: Date) => d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) },
        { ms: 300000, format: (d: Date) => d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) },
        { ms: 900000, format: (d: Date) => d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) },
        { ms: 3600000, format: (d: Date) => d.toLocaleTimeString('en-US', { hour: 'numeric' }) },
        { ms: 14400000, format: (d: Date) => d.toLocaleTimeString('en-US', { hour: 'numeric' }) },
        { ms: 86400000, format: (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) },
        { ms: 604800000, format: (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) },
        { ms: 2592000000, format: (d: Date) => d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) },
        { ms: 31536000000, format: (d: Date) => d.toLocaleDateString('en-US', { year: 'numeric' }) },
    ];

    let selectedInterval = TICK_INTERVALS[TICK_INTERVALS.length - 1];
    for (const interval of TICK_INTERVALS) {
        if (interval.ms >= msPerTickTarget) { selectedInterval = interval; break; }
    }

    const ticks: { time: number; label: string; x: number }[] = [];
    let t = Math.floor(viewStartTime / selectedInterval.ms) * selectedInterval.ms;
    let count = 0;
    while (t <= viewEndTime + selectedInterval.ms && count < 100) {
        ticks.push({ time: t, label: selectedInterval.format(new Date(t)), x: timeToX(t) });
        t += selectedInterval.ms;
        count++;
    }

    // Build task row index map for dependency arrows
    const taskRowIndex = new Map<string, number>();
    sortedTasks.forEach((task, idx) => taskRowIndex.set(task.id, idx));

    // Compute active start/end times for dependency arrows
    const taskActiveStartTimes = new Map<string, number>();
    const taskEndTimes = new Map<string, number>();
    sortedTasks.forEach(task => {
        const statusMuts = task.mutations.filter(m => m.type === 'status_change').sort((a, b) => a.timestamp - b.timestamp);
        const firstActive = statusMuts.find(m => !isQueueStatus(m.toStatus || ''));
        taskActiveStartTimes.set(task.id, firstActive ? firstActive.timestamp : new Date(task.createdAt).getTime());
        const lastActive = [...statusMuts].reverse().find(m => !isQueueStatus(m.toStatus || ''));
        taskEndTimes.set(task.id, lastActive ? lastActive.timestamp : new Date(task.createdAt).getTime());
    });

    const totalHeight = HEADER_HEIGHT + sortedTasks.length * (TRACK_HEIGHT + TRACK_GAP);

    // Summary stats computation
    const totalTasks = sortedTasks.length;
    const doneTasks = sortedTasks.filter(t => isCompletedStatus(t.currentStatus)).length;
    const failedTasks = sortedTasks.filter(t => isFailedStatus(t.currentStatus)).length;
    const totalExecMs = sortedTasks.reduce((sum, t) => sum + (t.executingTimeMs || t.workTimeMs), 0);
    const completedWithTime = sortedTasks.filter(t => isTerminalStatus(t.currentStatus) && (t.executingTimeMs || t.workTimeMs) > 0);
    const slowestTask = completedWithTime.length > 0
        ? completedWithTime.reduce((max, t) => (t.executingTimeMs || t.workTimeMs) > (max.executingTimeMs || max.workTimeMs) ? t : max)
        : null;
    const progressPct = totalTasks > 0 ? Math.round(((doneTasks + failedTasks) / totalTasks) * 100) : 0;

    // "Now" marker X position
    const nowX = timeToX(now);

    return (
        <div>
            <TimelineFilterBar {...filterBarProps} />

            <Card className="flex flex-row overflow-hidden border-border p-0" ref={containerRef}>
                {/* Task labels column */}
                <div className="shrink-0 border-r border-border bg-card z-[2]" style={{ width: LABEL_WIDTH }}>
                    <div className="flex items-center justify-center text-[11px] font-semibold text-muted-foreground uppercase tracking-wider border-b border-border" style={{ height: HEADER_HEIGHT }}>
                        Tasks
                    </div>
                    {sortedTasks.map((task, idx) => {
                        const trackColor = TRACK_COLORS[idx % TRACK_COLORS.length];
                        const assignee = task.assignees[0] || '';
                        const labelTooltip = task.labels?.map(l => l.name).join(', ') || '';
                        return (
                            <div key={task.id}
                                className="flex flex-col justify-center gap-0 px-2 border-l-[3px] cursor-pointer hover:bg-white/5 transition-colors overflow-hidden"
                                style={{ height: TRACK_HEIGHT, marginBottom: TRACK_GAP, borderLeftColor: trackColor }}
                                title={`${task.title || task.name}${labelTooltip ? `\nLabels: ${labelTooltip}` : ''}`}
                                onClick={() => onTaskClick?.(task)}>
                                <div className="flex items-center gap-1.5 min-w-0">
                                    <span className="size-1.5 rounded-full shrink-0" style={{ background: getStatusColor(task.currentStatus) }} />
                                    <span className="text-xs font-medium leading-tight truncate">{task.title || task.name}</span>
                                    {task.devEta !== undefined && task.remainingTimeMs !== undefined && (
                                        <span className="shrink-0 ml-auto">
                                            <CountdownTimer remainingMs={task.remainingTimeMs} isRunning={!!task.isTimerRunning} />
                                        </span>
                                    )}
                                </div>
                                <div className="flex items-center gap-1 pl-3 min-w-0">
                                    {assignee && (
                                        <span className="text-[10px] text-muted-foreground truncate">{assignee}</span>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>

                {/* SVG timeline area */}
                <div ref={viewportRef} className="flex-1 overflow-hidden relative select-none"
                    style={{ cursor: dragRef.current.isDragging ? 'grabbing' : 'grab' }}
                    onMouseDown={handleMouseDown} onMouseLeave={handleMouseUp} onMouseUp={handleMouseUp} onMouseMove={handleMouseMove}>
                    <svg width={svgWidth} height={totalHeight} className="block">
                        <defs>
                            {/* Hatch pattern for failed/blocked segments */}
                            <pattern id="hatch-failed" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
                                <line x1="0" y1="0" x2="0" y2="6" stroke="var(--destructive)" strokeWidth="1.5" opacity="0.4" />
                            </pattern>
                            {/* Arrowhead marker for dependency arrows */}
                            <marker id="dep-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted-foreground)" opacity="0.6" />
                            </marker>
                        </defs>

                        {/* Time axis ticks */}
                        <g>
                            {ticks.map((tick, i) => (
                                <g key={i}>
                                    <line x1={tick.x} y1={HEADER_HEIGHT} x2={tick.x} y2={totalHeight} stroke="var(--border)" strokeDasharray="4 4" />
                                    <text x={tick.x} y={HEADER_HEIGHT - 8} fill="var(--muted-foreground)" fontSize={11} fontWeight={500} textAnchor="middle" fontFamily="'Inter', sans-serif">{tick.label}</text>
                                </g>
                            ))}
                        </g>

                        {/* Task tracks */}
                        {sortedTasks.map((task, idx) => {
                            const trackColor = TRACK_COLORS[idx % TRACK_COLORS.length];
                            const trackY = HEADER_HEIGHT + idx * (TRACK_HEIGHT + TRACK_GAP);
                            const mutations = [...task.mutations].sort((a, b) => a.timestamp - b.timestamp);

                            // Build status segments
                            const segments: { startTime: number; endTime: number; status: string }[] = [];
                            let currentStatus = task.currentStatus;
                            const createdMutation = mutations.find(m => m.type === 'created');
                            if (createdMutation?.toStatus) currentStatus = createdMutation.toStatus;
                            else if (mutations.length > 0) {
                                const firstSC = mutations.find(m => m.type === 'status_change' && m.fromStatus);
                                if (firstSC?.fromStatus) currentStatus = firstSC.fromStatus;
                            }
                            let segStart = new Date(task.createdAt).getTime();
                            let segStatus = currentStatus;
                            for (const m of mutations) {
                                if (m.type === 'status_change' && m.toStatus) {
                                    segments.push({ startTime: segStart, endTime: m.timestamp, status: segStatus });
                                    segStatus = m.toStatus; segStart = m.timestamp;
                                }
                            }
                            // Clip last segment: terminal tasks end at last mutation, active tasks extend to now
                            const isTerminal = isTerminalStatus(segStatus);
                            const lastSegEnd = isTerminal
                                ? (mutations.length > 0 ? mutations[mutations.length - 1].timestamp : segStart + 3600000)
                                : now;
                            segments.push({ startTime: segStart, endTime: lastSegEnd, status: segStatus });

                            // Only status-change mutations that transition INTO active states get dots
                            const statusMutations = mutations.filter(m => m.type === 'status_change');
                            const activeDots = statusMutations.filter(m => !isQueueStatus(m.toStatus || ''));

                            // Connecting line spans from first active transition to last
                            const firstActiveMut = statusMutations.find(m => !isQueueStatus(m.toStatus || ''));
                            const lastActiveMut = [...statusMutations].reverse().find(m => !isQueueStatus(m.toStatus || ''));
                            const lineStart = firstActiveMut ? firstActiveMut.timestamp : new Date(task.createdAt).getTime();
                            const lineEnd = lastActiveMut ? lastActiveMut.timestamp : lineStart;

                            // Elapsed time for in-progress tasks
                            const isInProgress = !isTerminal;
                            const elapsed = isInProgress ? now - new Date(task.createdAt).getTime() : 0;

                            return (
                                <g key={task.id}>
                                    <rect x={0} y={trackY} width={svgWidth} height={TRACK_HEIGHT} rx={4}
                                        fill={idx % 2 === 0 ? 'transparent' : 'color-mix(in srgb, var(--muted), transparent 70%)'} />

                                    {/* Two-tier status segments */}
                                    {segments.map((seg, si) => {
                                        const x1 = timeToX(seg.startTime), x2 = timeToX(seg.endTime);
                                        if (x2 < 0 || x1 > svgWidth) return null;
                                        const clampedX1 = Math.max(x1, 0);
                                        const clampedX2 = Math.min(x2, svgWidth);
                                        const w = Math.max(clampedX2 - clampedX1, 2);
                                        const queue = isQueueStatus(seg.status);
                                        const failed = isFailedStatus(seg.status);

                                        if (queue) {
                                            // Queue tier: thin line, low opacity
                                            const cy = trackY + TRACK_HEIGHT / 2;
                                            return (
                                                <line key={si} x1={clampedX1} y1={cy} x2={clampedX1 + w} y2={cy}
                                                    stroke={getStatusColor(seg.status)} strokeWidth={2} opacity={0.25}
                                                    strokeDasharray="4 3" />
                                            );
                                        }

                                        // Active tier: full bar
                                        const bp = 8, bh = TRACK_HEIGHT - bp * 2;
                                        return (
                                            <g key={si}>
                                                <rect x={clampedX1} y={trackY + bp} width={w} height={bh}
                                                    rx={bh / 2} fill={getStatusColor(seg.status)} opacity={0.55} />
                                                {failed && (
                                                    <rect x={clampedX1} y={trackY + bp} width={w} height={bh}
                                                        rx={bh / 2} fill="url(#hatch-failed)" />
                                                )}
                                            </g>
                                        );
                                    })}

                                    {/* Connecting line (active span only) */}
                                    {lineEnd > lineStart && (
                                        <line x1={timeToX(lineStart)} y1={trackY + TRACK_HEIGHT / 2} x2={timeToX(lineEnd)} y2={trackY + TRACK_HEIGHT / 2}
                                            stroke={trackColor} strokeWidth={2} strokeLinecap="round" opacity={0.5} />
                                    )}

                                    {/* Elapsed duration label for in-progress tasks */}
                                    {isInProgress && elapsed > 0 && (() => {
                                        const barEndX = timeToX(now);
                                        if (barEndX < 0 || barEndX > svgWidth + 100) return null;
                                        const displayElapsed = task.executingTimeMs > 0 ? task.executingTimeMs : elapsed;
                                        return (
                                            <text x={Math.min(barEndX + 4, svgWidth - 40)} y={trackY + TRACK_HEIGHT / 2 + 3}
                                                fill="var(--muted-foreground)" fontSize={9} fontFamily="'Inter', sans-serif"
                                                opacity={0.8}>{formatDuration(displayElapsed)}</text>
                                        );
                                    })()}

                                    {/* Dots only for transitions into active states */}
                                    {activeDots.map(m => {
                                        const cx = timeToX(m.timestamp);
                                        if (cx < -20 || cx > svgWidth + 20) return null;
                                        const cy = trackY + TRACK_HEIGHT / 2;
                                        const dotColor = getStatusColor(m.toStatus || '') || trackColor;
                                        return (
                                            <g key={m.id}>
                                                {hoveredMutation?.mutation.id === m.id && (
                                                    <circle cx={cx} cy={cy} r={DOT_RADIUS + 4} fill="none" stroke={dotColor} strokeWidth={1} opacity={0.6} />
                                                )}
                                                <circle cx={cx} cy={cy} r={DOT_RADIUS} fill={dotColor} stroke="var(--background)" strokeWidth={2}
                                                    onMouseEnter={() => { if (!dragRef.current.isDragging) setHoveredMutation({ mutation: m, taskName: task.name, trackColor, x: cx + 12, y: cy - 8 }); }}
                                                    onMouseLeave={() => setHoveredMutation(null)} />
                                            </g>
                                        );
                                    })}
                                </g>
                            );
                        })}

                        {/* Dependency arrows (final layer, on top of tracks) */}
                        {sortedTasks.map(task => {
                            if (!task.dependsOn || task.dependsOn.length === 0) return null;
                            const targetIdx = taskRowIndex.get(task.id);
                            if (targetIdx === undefined) return null;
                            const targetY = HEADER_HEIGHT + targetIdx * (TRACK_HEIGHT + TRACK_GAP) + TRACK_HEIGHT / 2;
                            const targetStartX = timeToX(taskActiveStartTimes.get(task.id) || new Date(task.createdAt).getTime());

                            return task.dependsOn.map(depId => {
                                const sourceIdx = taskRowIndex.get(depId);
                                if (sourceIdx === undefined) return null;
                                const sourceY = HEADER_HEIGHT + sourceIdx * (TRACK_HEIGHT + TRACK_GAP) + TRACK_HEIGHT / 2;
                                const sourceEndTime = taskEndTimes.get(depId) || 0;
                                const sourceEndX = timeToX(sourceEndTime);

                                // Cubic bezier: leave horizontally, arrive horizontally
                                const dx = Math.abs(targetStartX - sourceEndX) * 0.4;
                                const path = `M ${sourceEndX},${sourceY} C ${sourceEndX + dx},${sourceY} ${targetStartX - dx},${targetY} ${targetStartX},${targetY}`;

                                return (
                                    <path key={`${depId}->${task.id}`} d={path}
                                        fill="none" stroke="var(--muted-foreground)" strokeWidth={1.5}
                                        strokeDasharray="4 2" opacity={0.5}
                                        markerEnd="url(#dep-arrow)" />
                                );
                            });
                        })}

                        {/* "NOW" marker */}
                        {nowX >= 0 && nowX <= svgWidth && (
                            <g>
                                <line x1={nowX} y1={HEADER_HEIGHT} x2={nowX} y2={totalHeight}
                                    stroke="var(--destructive)" strokeWidth={1.5} strokeDasharray="6 3" opacity={0.7} />
                                <text x={nowX} y={HEADER_HEIGHT - 2} fill="var(--destructive)" fontSize={9}
                                    fontWeight={700} textAnchor="middle" fontFamily="'Inter', sans-serif" opacity={0.8}>NOW</text>
                            </g>
                        )}
                    </svg>

                    {hoveredMutation && (
                        <div className="gantt-tooltip" style={{ left: hoveredMutation.x, top: hoveredMutation.y }}>
                            <div className="text-[11px] text-muted-foreground capitalize mb-1">
                                {hoveredMutation.mutation.type.replace('_', ' ')}
                            </div>
                            <div className="text-sm font-semibold mb-1">{hoveredMutation.taskName}</div>
                            <div className="text-xs text-muted-foreground leading-relaxed mb-1">{hoveredMutation.mutation.description}</div>
                            <div className="text-[11px] text-muted-foreground">by <strong className="text-primary">{hoveredMutation.mutation.actor}</strong></div>
                            <div className="text-[10px] text-muted-foreground mt-1 font-mono">{formatFullDate(hoveredMutation.mutation.date)}</div>
                        </div>
                    )}
                </div>
            </Card>

            {/* Actionable summary stats */}
            <div className="flex gap-4 mt-4 flex-wrap">
                <Card className="flex-1 min-w-[120px] border-border text-center p-3">
                    <div className="text-base font-bold mb-0.5">{doneTasks + failedTasks}/{totalTasks} ({progressPct}%)</div>
                    <div className="text-xs text-muted-foreground">Progress</div>
                </Card>
                <Card className={`flex-1 min-w-[120px] border-border text-center p-3 ${failedTasks > 0 ? 'border-destructive/50 bg-destructive/5' : ''}`}>
                    <div className={`text-base font-bold mb-0.5 ${failedTasks > 0 ? 'text-destructive' : ''}`}>{failedTasks}</div>
                    <div className="text-xs text-muted-foreground">Failed</div>
                </Card>
                <Card className="flex-1 min-w-[120px] border-border text-center p-3">
                    <div className="text-base font-bold mb-0.5">{formatDuration(totalExecMs)}</div>
                    <div className="text-xs text-muted-foreground">Total Exec Time</div>
                </Card>
                <Card className="flex-1 min-w-[120px] border-border text-center p-3">
                    <div className="text-base font-bold mb-0.5 truncate" title={slowestTask ? `${slowestTask.title || slowestTask.name}: ${formatDuration(slowestTask.executingTimeMs || slowestTask.workTimeMs)}` : '—'}>
                        {slowestTask ? `${(slowestTask.title || slowestTask.name).slice(0, 16)}… ${formatDuration(slowestTask.executingTimeMs || slowestTask.workTimeMs)}` : '—'}
                    </div>
                    <div className="text-xs text-muted-foreground">Slowest Task</div>
                </Card>
            </div>
        </div>
    );
}

// ─── Filter Bar ───
interface TimelineFilterBarProps {
    searchTerm: string; onSearchChange: (term: string) => void;
    members: string[]; statuses: string[];
    selectedMembers: Set<string>; selectedStatuses: Set<string>;
    completionFilter: CompletionFilter; onCompletionFilter: (f: CompletionFilter) => void;
    hideTrivial: boolean; zoom: number;
    onToggleMember: (m: string) => void; onToggleStatus: (s: string) => void;
    onToggleTrivial: () => void; onZoomChange: (z: number) => void;
    onZoomPreset: (preset: 'hours' | 'days' | 'all') => void;
    viewMode?: TimelineMode; onViewModeChange?: (mode: TimelineMode) => void;
}

function TimelineFilterBar({
    searchTerm, onSearchChange, members, statuses, selectedMembers, selectedStatuses,
    completionFilter, onCompletionFilter,
    hideTrivial, zoom, onToggleMember, onToggleStatus, onToggleTrivial, onZoomChange, onZoomPreset,
    viewMode = 'timeline', onViewModeChange,
}: TimelineFilterBarProps) {
    return (
        <Card className="flex flex-row flex-wrap items-center gap-4 mb-4 p-3 border-border">
            {/* View mode toggle */}
            {onViewModeChange && (
                <>
                    <div className="flex items-center gap-1 bg-muted rounded-md p-0.5">
                        <Button
                            variant={viewMode === 'timeline' ? 'default' : 'ghost'}
                            size="sm"
                            className="h-6 text-[10px] px-2 gap-1"
                            onClick={() => onViewModeChange('timeline')}
                        >
                            <GanttChart className="size-3" /> Timeline
                        </Button>
                        <Button
                            variant={viewMode === 'dag' ? 'default' : 'ghost'}
                            size="sm"
                            className="h-6 text-[10px] px-2 gap-1"
                            onClick={() => onViewModeChange('dag')}
                        >
                            <GitBranch className="size-3" /> DAG
                        </Button>
                    </div>
                    <div className="w-px h-5 bg-border" />
                </>
            )}

            <div className="relative w-[200px]">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                <Input placeholder="Search tasks..." value={searchTerm} onChange={e => onSearchChange(e.target.value)} className="pl-8 h-8 text-sm" />
            </div>

            <div className="w-px h-5 bg-border" />

            {/* Completion filter */}
            <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground flex items-center gap-1"><ListFilter className="size-3" /> Show</span>
                <Button variant={completionFilter === 'complete' ? 'default' : 'outline'} size="sm" className="h-6 text-[10px] px-2 gap-1"
                    onClick={() => onCompletionFilter('complete')}>
                    <CheckCircle2 className="size-3" /> Complete
                </Button>
                <Button variant={completionFilter === 'active' ? 'default' : 'outline'} size="sm" className="h-6 text-[10px] px-2 gap-1"
                    onClick={() => onCompletionFilter('active')}>
                    <CircleAlert className="size-3" /> Active
                </Button>
            </div>

            {/* Zoom controls — only show in timeline mode */}
            {viewMode === 'timeline' && (
                <>
                    <div className="w-px h-5 bg-border" />

                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground flex items-center gap-1"><ZoomIn className="size-3" /> Zoom</span>
                        <Button variant="outline" size="icon" className="size-7" onClick={() => onZoomChange(Math.max(zoom / ZOOM_STEP, MIN_ZOOM))} disabled={zoom <= MIN_ZOOM}>
                            <ZoomOut className="size-3" />
                        </Button>
                        <span className="text-xs text-muted-foreground min-w-[36px] text-center font-mono">
                            {zoom < 10 ? Math.round(zoom * 100) + '%' : Math.round(zoom) + 'x'}
                        </span>
                        <Button variant="outline" size="icon" className="size-7" onClick={() => onZoomChange(Math.min(zoom * ZOOM_STEP, MAX_ZOOM))} disabled={zoom >= MAX_ZOOM}>
                            <ZoomIn className="size-3" />
                        </Button>

                        <div className="w-px h-4 bg-border mx-1" />

                        <Button variant="outline" size="sm" className="h-6 text-[10px] px-2" onClick={() => onZoomPreset('hours')}>
                            <Clock className="size-3 mr-0.5" /> Hours
                        </Button>
                        <Button variant="outline" size="sm" className="h-6 text-[10px] px-2" onClick={() => onZoomPreset('days')}>
                            <Timer className="size-3 mr-0.5" /> Days
                        </Button>
                        <Button variant="outline" size="sm" className="h-6 text-[10px] px-2" onClick={() => onZoomPreset('all')}>
                            All
                        </Button>
                    </div>
                </>
            )}

            {members.length > 0 && (
                <>
                    <div className="w-px h-5 bg-border" />
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground flex items-center gap-1"><Users className="size-3" /> Members</span>
                        {members.map(member => (
                            <Button key={member} variant={selectedMembers.has(member) ? 'default' : 'outline'} size="sm" className="h-6 text-xs px-2"
                                onClick={() => onToggleMember(member)}>{member}</Button>
                        ))}
                    </div>
                </>
            )}

            {statuses.length > 0 && (
                <>
                    <div className="w-px h-5 bg-border" />
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground flex items-center gap-1"><BarChart3 className="size-3" /> Status</span>
                        {statuses.map(status => {
                            const color = getStatusColor(status);
                            const isActive = selectedStatuses.has(status);
                            return (
                                <Button key={status} variant={isActive ? 'default' : 'outline'} size="sm" className="h-6 text-xs px-2 gap-1"
                                    style={isActive && color ? { borderColor: color, backgroundColor: `color-mix(in srgb, ${color}, transparent 85%)`, color } : {}}
                                    onClick={() => onToggleStatus(status)}>
                                    <span className="size-1.5 rounded-full shrink-0" style={{ background: color }} />
                                    {status}
                                </Button>
                            );
                        })}
                    </div>
                </>
            )}

            <div className="w-px h-5 bg-border" />

            <Button variant={hideTrivial ? 'default' : 'outline'} size="sm" className="h-7 text-xs gap-1"
                onClick={onToggleTrivial}>
                {hideTrivial ? <Eye className="size-3" /> : <EyeOff className="size-3" />}
                Hide Trivial
            </Button>
        </Card>
    );
}
