import { useEffect, useMemo, useState } from 'react';
import type { Task } from '../types';
import { classifyEdge, collectDownstreamTaskIds, computeDagLayout } from '../utils/dagUtils';
import { getStatusColor } from '../utils/transformer';
import { useService } from '../contexts/ServiceContext';
import { useToast } from '@/hooks/use-toast';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 72;

function normalize(ids: string[] | undefined): string[] {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const id of ids || []) {
        const value = String(id);
        if (!value || seen.has(value)) continue;
        seen.add(value);
        result.push(value);
    }
    return result;
}

function wouldCreateCycle(graph: Record<string, string[]>, sourceId: string, targetId: string): boolean {
    const next = new Set(normalize(graph[targetId]));
    next.add(sourceId);
    const stack = [...next];
    const seen = new Set<string>();
    while (stack.length > 0) {
        const current = stack.pop()!;
        if (current === targetId) return true;
        if (seen.has(current)) continue;
        seen.add(current);
        stack.push(...normalize(graph[current]));
    }
    return false;
}

function getEdgeStroke(task?: Task): string {
    if (!task) return 'var(--muted-foreground)';
    const state = classifyEdge(task);
    if (state === 'blocked') return '#ef4444';
    if (state === 'satisfied') return '#16a34a';
    if (state === 'active') return '#2563eb';
    return 'var(--muted-foreground)';
}

function pathForEdge(points: { x: number; y: number }[]): string {
    if (points.length < 2) return '';

    const radius = 14;
    let path = `M ${points[0].x} ${points[0].y}`;

    for (let index = 1; index < points.length; index += 1) {
        const previous = points[index - 1];
        const current = points[index];
        const next = points[index + 1];

        if (!next) {
            path += ` L ${current.x} ${current.y}`;
            continue;
        }

        const incomingDx = current.x - previous.x;
        const incomingDy = current.y - previous.y;
        const outgoingDx = next.x - current.x;
        const outgoingDy = next.y - current.y;

        const incomingLength = Math.hypot(incomingDx, incomingDy);
        const outgoingLength = Math.hypot(outgoingDx, outgoingDy);

        if (incomingLength === 0 || outgoingLength === 0) {
            path += ` L ${current.x} ${current.y}`;
            continue;
        }

        const bendRadius = Math.min(radius, incomingLength / 2, outgoingLength / 2);
        const beforeX = current.x - (incomingDx / incomingLength) * bendRadius;
        const beforeY = current.y - (incomingDy / incomingLength) * bendRadius;
        const afterX = current.x + (outgoingDx / outgoingLength) * bendRadius;
        const afterY = current.y + (outgoingDy / outgoingLength) * bendRadius;

        path += ` L ${beforeX} ${beforeY}`;
        path += ` Q ${current.x} ${current.y} ${afterX} ${afterY}`;
    }

    return path;
}

interface DependencyBoardViewProps {
    boardId?: string;
    allTasks: Task[];
    loading?: boolean;
    onEnsureTasks?: (boardId: string, force?: boolean) => Promise<void> | void;
    onSaved?: (taskIds: string[]) => Promise<void> | void;
}

export function DependencyBoardView({
    boardId,
    allTasks,
    loading = false,
    onEnsureTasks,
    onSaved,
}: DependencyBoardViewProps) {
    const service = useService();
    const { toast } = useToast();
    const boardTasks = useMemo(
        () => (boardId ? allTasks.filter(task => task.boardId === boardId) : []),
        [allTasks, boardId],
    );

    const [stagedDependsOn, setStagedDependsOn] = useState<Record<string, string[]>>({});
    const [initialDependsOn, setInitialDependsOn] = useState<Record<string, string[]>>({});
    const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
    const [connectionSourceId, setConnectionSourceId] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [zoom, setZoom] = useState(1);

    useEffect(() => {
        if (!boardId || !onEnsureTasks) return;
        void onEnsureTasks(boardId);
    }, [boardId, onEnsureTasks]);

    useEffect(() => {
        const nextState = Object.fromEntries(boardTasks.map(task => [task.id, normalize(task.dependsOn)]));
        setInitialDependsOn(nextState);
        setStagedDependsOn(nextState);
        setSelectedTaskId(current => (current && nextState[current] ? current : boardTasks[0]?.id || null));
        setConnectionSourceId(null);
    }, [boardTasks]);

    const visibleTasks = useMemo(
        () => boardTasks.map(task => ({
            ...task,
            dependsOn: stagedDependsOn[task.id] ?? normalize(task.dependsOn),
        })),
        [boardTasks, stagedDependsOn],
    );

    const layout = useMemo(() => computeDagLayout(visibleTasks), [visibleTasks]);
    const visibleTaskMap = useMemo(
        () => new Map(visibleTasks.map(task => [task.id, task] as const)),
        [visibleTasks],
    );
    const selectedTask = selectedTaskId ? visibleTaskMap.get(selectedTaskId) || null : null;
    const activeCandidateIds = useMemo(
        () => new Set(boardTasks.filter(task => task.currentStatus === 'TODO' || task.currentStatus === 'IN_PROGRESS').map(task => task.id)),
        [boardTasks],
    );
    const selectedTaskDownstreamIds = useMemo(
        () => selectedTaskId ? collectDownstreamTaskIds(visibleTasks, selectedTaskId) : new Set<string>(),
        [selectedTaskId, visibleTasks],
    );
    const addableDependencies = useMemo(() => {
        if (!selectedTaskId) return [];
        const currentDependsOn = new Set(normalize(stagedDependsOn[selectedTaskId]));
        return visibleTasks.filter(task =>
            task.id !== selectedTaskId
            && !currentDependsOn.has(task.id)
            && !selectedTaskDownstreamIds.has(task.id)
            && activeCandidateIds.has(task.id)
        );
    }, [activeCandidateIds, selectedTaskDownstreamIds, selectedTaskId, stagedDependsOn, visibleTasks]);

    const toggleConnection = (sourceId: string, targetId: string) => {
        if (sourceId === targetId) {
            toast({ title: 'Invalid dependency', description: 'A task cannot depend on itself.', variant: 'destructive' });
            setConnectionSourceId(null);
            return;
        }
        const existingDependsOn = normalize(stagedDependsOn[targetId]);
        if (existingDependsOn.includes(sourceId)) {
            setStagedDependsOn(prev => ({
                ...prev,
                [targetId]: normalize(prev[targetId]).filter(depId => depId !== sourceId),
            }));
            setSelectedTaskId(targetId);
            setConnectionSourceId(null);
            return;
        }
        if (!activeCandidateIds.has(sourceId)) {
            toast({
                title: 'Inactive dependency',
                description: 'Only To Do and In Progress tasks can be used as new dependencies.',
                variant: 'destructive',
            });
            setConnectionSourceId(null);
            return;
        }
        if (wouldCreateCycle(stagedDependsOn, sourceId, targetId)) {
            toast({ title: 'Cycle blocked', description: 'This connection would create a dependency cycle.', variant: 'destructive' });
            setConnectionSourceId(null);
            return;
        }
        setStagedDependsOn(prev => ({
            ...prev,
            [targetId]: normalize([...(prev[targetId] || []), sourceId]),
        }));
        setSelectedTaskId(targetId);
        setConnectionSourceId(null);
    };

    const handleNodeClick = (taskId: string) => {
        setSelectedTaskId(taskId);
        if (!connectionSourceId) {
            setConnectionSourceId(taskId);
            return;
        }
        if (connectionSourceId === taskId) {
            setConnectionSourceId(null);
            return;
        }
        toggleConnection(connectionSourceId, taskId);
    };

    const handleRemoveEdge = (sourceId: string, targetId: string) => {
        setStagedDependsOn(prev => ({
            ...prev,
            [targetId]: normalize(prev[targetId]).filter(depId => depId !== sourceId),
        }));
        if (connectionSourceId === sourceId) {
            setConnectionSourceId(null);
        }
        setSelectedTaskId(targetId);
    };

    const handleAddDependency = (sourceId: string, targetId: string) => {
        toggleConnection(sourceId, targetId);
    };

    const changedTaskIds = useMemo(() => {
        return Object.keys(stagedDependsOn).filter(taskId => {
            const before = JSON.stringify(normalize(initialDependsOn[taskId]));
            const after = JSON.stringify(normalize(stagedDependsOn[taskId]));
            return before !== after;
        });
    }, [initialDependsOn, stagedDependsOn]);

    const handleSave = async () => {
        if (changedTaskIds.length === 0) return;
        setSaving(true);
        try {
            for (const taskId of changedTaskIds) {
                await service.updateTask(taskId, { dependsOn: normalize(stagedDependsOn[taskId]) });
            }
            await onSaved?.(changedTaskIds);
            toast({ title: 'Dependencies saved' });
        } catch (error) {
            console.error('Failed to save dependency graph', error);
            toast({
                title: 'Save failed',
                description: error instanceof Error ? error.message : 'Failed to save dependency updates.',
                variant: 'destructive',
            });
        } finally {
            setSaving(false);
        }
    };

    if (!boardId) {
        return <div className="text-sm text-muted-foreground py-8">Select a board to edit dependencies.</div>;
    }

    if (loading && boardTasks.length === 0) {
        return <div className="text-sm text-muted-foreground py-8">Loading dependency graph...</div>;
    }

    if (boardTasks.length === 0) {
        return <div className="text-sm text-muted-foreground py-8">No tasks available for this board.</div>;
    }

    const canvasWidth = Math.max(layout.width + NODE_WIDTH + 120, 1280);
    const canvasHeight = Math.max(layout.height + NODE_HEIGHT + 120, 720);

    return (
        <div className="grid min-h-[70vh] gap-4 lg:grid-cols-[1fr_320px]">
            <div className="rounded-xl border bg-card min-h-0 overflow-hidden">
                <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
                    <div className="text-sm text-muted-foreground">
                        Click a prerequisite task, then click the dependent task to add or remove that link.
                    </div>
                    <div className="flex items-center gap-2">
                        <Button size="sm" variant="outline" className="h-8 px-2" onClick={() => setZoom(prev => Math.max(0.6, Number((prev - 0.1).toFixed(2))))}>
                            -
                        </Button>
                        <span className="w-12 text-center text-xs font-mono text-muted-foreground">{Math.round(zoom * 100)}%</span>
                        <Button size="sm" variant="outline" className="h-8 px-2" onClick={() => setZoom(prev => Math.min(1.8, Number((prev + 0.1).toFixed(2))))}>
                            +
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 px-2 text-xs" onClick={() => setZoom(1)}>
                            Reset
                        </Button>
                    </div>
                </div>
                <div className="h-[72vh] overflow-auto bg-[radial-gradient(circle_at_1px_1px,var(--border)_1px,transparent_0)] [background-size:24px_24px]">
                    <svg
                        width={canvasWidth}
                        height={canvasHeight}
                        viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
                        preserveAspectRatio="xMinYMin meet"
                        className="block"
                        style={{ width: `${canvasWidth * zoom}px`, height: `${canvasHeight * zoom}px` }}
                    >
                        {layout.edges.map(edge => {
                            const sourceTask = visibleTaskMap.get(edge.source);
                            const stroke = getEdgeStroke(sourceTask);
                            return (
                                <path
                                    key={`${edge.source}-${edge.target}`}
                                    d={pathForEdge(edge.points)}
                                    fill="none"
                                    stroke={stroke}
                                    strokeWidth={3}
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    opacity={0.85}
                                />
                            );
                        })}
                        {layout.nodes.map(node => {
                            const task = visibleTaskMap.get(node.id);
                            if (!task) return null;
                            const x = node.x - NODE_WIDTH / 2;
                            const y = node.y - NODE_HEIGHT / 2;
                            const isSelected = task.id === selectedTaskId;
                            const isConnectionSource = task.id === connectionSourceId;
                            const isActiveCandidate = activeCandidateIds.has(task.id);
                            return (
                                <g
                                    key={task.id}
                                    onClick={() => handleNodeClick(task.id)}
                                    className="cursor-pointer"
                                >
                                    <rect
                                        x={x}
                                        y={y}
                                        width={NODE_WIDTH}
                                        height={NODE_HEIGHT}
                                        rx={14}
                                        fill="var(--card)"
                                        stroke={isConnectionSource ? 'var(--primary)' : isSelected ? 'var(--foreground)' : getStatusColor(task.currentStatus)}
                                        strokeWidth={isConnectionSource ? 4 : isSelected ? 3 : 2}
                                        opacity={isActiveCandidate ? 1 : 0.76}
                                    />
                                    <text x={x + 14} y={y + 22} fontSize="11" fill="var(--muted-foreground)">
                                        #{task.idShort} {task.currentStatus}
                                    </text>
                                    <text x={x + 14} y={y + 44} fontSize="14" fill="var(--foreground)">
                                        {(task.title || task.name).slice(0, 26)}
                                    </text>
                                    {isConnectionSource && (
                                        <text x={x + 14} y={y + 62} fontSize="10" fill="var(--primary)">
                                            Selected source
                                        </text>
                                    )}
                                </g>
                            );
                        })}
                    </svg>
                </div>
            </div>
            <div className="min-h-0 rounded-xl border bg-card p-4">
                <div className="space-y-4 h-full flex flex-col">
                    <div className="flex items-center justify-between gap-2">
                        <div>
                            <div className="text-sm font-medium">Dependency Editor</div>
                            <p className="mt-1 text-xs text-muted-foreground">{changedTaskIds.length} staged task change(s).</p>
                        </div>
                        <Button variant="outline" size="sm" onClick={() => boardId && onEnsureTasks?.(boardId, true)} disabled={loading}>
                            Refresh
                        </Button>
                    </div>
                    <div className="min-h-0 flex-1">
                        <div className="text-sm font-medium mb-2">Selected Task</div>
                        {selectedTask ? (
                            <ScrollArea className="h-[420px] rounded-md border">
                                <div className="p-3 space-y-3">
                                    <div>
                                        <div className="text-xs text-muted-foreground font-mono">#{selectedTask.idShort}</div>
                                        <div className="text-sm font-medium">{selectedTask.title || selectedTask.name}</div>
                                        <div className="mt-1">
                                            <Badge variant="outline">{selectedTask.currentStatus}</Badge>
                                            {connectionSourceId === selectedTask.id && (
                                                <Badge variant="outline" className="ml-2 border-primary text-primary">Source</Badge>
                                            )}
                                        </div>
                                    </div>
                                    <div>
                                        <div className="mb-2 flex items-center justify-between gap-2">
                                            <div className="text-xs font-medium text-muted-foreground">Depends On</div>
                                            {addableDependencies.length > 0 && (
                                                <Badge variant="outline" className="h-5 text-[10px]">
                                                    {addableDependencies.length} addable
                                                </Badge>
                                            )}
                                        </div>
                                        <div className="space-y-2">
                                            {normalize(stagedDependsOn[selectedTask.id]).length === 0 ? (
                                                <p className="text-xs text-muted-foreground italic">No dependencies staged.</p>
                                            ) : normalize(stagedDependsOn[selectedTask.id]).map(depId => {
                                                const depTask = visibleTaskMap.get(depId) || boardTasks.find(task => task.id === depId);
                                                return (
                                                    <div key={depId} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
                                                        <div className="min-w-0">
                                                            <div className="text-xs text-muted-foreground font-mono">#{depTask?.idShort || depId}</div>
                                                            <div className="text-sm font-medium truncate">{depTask?.title || depTask?.name || `Task ${depId}`}</div>
                                                        </div>
                                                        <Button
                                                            size="sm"
                                                            variant="ghost"
                                                            className="h-7 text-xs"
                                                            onClick={() => handleRemoveEdge(depId, selectedTask.id)}
                                                        >
                                                            Remove
                                                        </Button>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    </div>
                                    <div>
                                        <div className="text-xs font-medium text-muted-foreground mb-2">Add Dependency</div>
                                        <div className="space-y-2">
                                            {addableDependencies.length === 0 ? (
                                                <p className="text-xs text-muted-foreground italic">No addable active tasks.</p>
                                            ) : addableDependencies.map(depTask => (
                                                <div key={depTask.id} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
                                                    <div className="min-w-0">
                                                        <div className="text-xs text-muted-foreground font-mono">#{depTask.idShort}</div>
                                                        <div className="text-sm font-medium truncate">{depTask.title || depTask.name}</div>
                                                    </div>
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        className="h-7 text-xs"
                                                        onClick={() => handleAddDependency(depTask.id, selectedTask.id)}
                                                    >
                                                        Add
                                                    </Button>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            </ScrollArea>
                        ) : (
                            <p className="text-sm text-muted-foreground">Select a task node to inspect its dependencies.</p>
                        )}
                    </div>
                    <div className="flex justify-end gap-2">
                        <Button onClick={handleSave} disabled={saving}>{saving ? 'Saving...' : 'Save'}</Button>
                    </div>
                </div>
            </div>
        </div>
    );
}
