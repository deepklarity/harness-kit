import { memo, useState, useMemo, useEffect, useRef, useCallback } from 'react';
import type { Task, Member } from '../types';
import { TaskCard } from './TaskCard';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { getStatusColor } from '../utils/transformer';
import {
    Archive, ClipboardList, Wrench, Eye, FlaskConical, CheckCircle2, XCircle,
    ChevronDown, ChevronUp,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import {
    DndContext,
    DragOverlay,
    pointerWithin,
    rectIntersection,
    KeyboardSensor,
    PointerSensor,
    useSensor,
    useSensors,
    useDroppable,
    defaultDropAnimationSideEffects,
} from '@dnd-kit/core';
import type { CollisionDetection } from '@dnd-kit/core';
import type { DragStartEvent, DragEndEvent, DragOverEvent } from '@dnd-kit/core';
import {
    SortableContext,
    sortableKeyboardCoordinates,
    verticalListSortingStrategy,
    useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

interface TaskMove {
    status: string;
    targetIndex?: number;
}

interface KanbanBoardProps {
    tasks: Task[];
    onTaskClick: (task: Task) => void;
    onTaskMove?: (taskId: string, move: TaskMove) => Promise<boolean>;
    onStopExecution?: (taskId: string, targetStatus: string) => Promise<boolean>;
    onTaskRename?: (taskId: string, newTitle: string) => Promise<void> | void;
    memberMap?: Map<string, Member>;
}

const COLUMNS: { status: string; label: string; icon: LucideIcon; includeStatuses?: string[] }[] = [
    { status: 'BACKLOG', label: 'Backlog', icon: Archive },
    { status: 'TODO', label: 'To Do', icon: ClipboardList },
    { status: 'IN_PROGRESS', label: 'In Progress', icon: Wrench, includeStatuses: ['IN_PROGRESS', 'EXECUTING'] },
    { status: 'REVIEW', label: 'Review', icon: Eye },
    { status: 'TESTING', label: 'Testing', icon: FlaskConical },
    { status: 'DONE', label: 'Done', icon: CheckCircle2 },
    { status: 'FAILED', label: 'Failed', icon: XCircle },
];

const COLUMN_IDS = new Set(COLUMNS.map(c => c.status));

function getColumnForStatus(status: string): string {
    const column = COLUMNS.find(col => (col.includeStatuses ?? [col.status]).includes(status));
    return column?.status ?? status;
}

function getStatusesForColumn(columnStatus: string): string[] {
    const column = COLUMNS.find(col => col.status === columnStatus);
    return column?.includeStatuses ?? [columnStatus];
}

function getTargetStatusForColumn(task: Task, columnStatus: string): string {
    if (columnStatus === 'IN_PROGRESS' && task.currentStatus === 'EXECUTING') {
        return 'EXECUTING';
    }
    return columnStatus;
}

function applyOptimisticMove(tasks: Task[], taskId: string, newStatus: string, targetIndex: number): Task[] {
    const movingTask = tasks.find(task => task.id === taskId);
    if (!movingTask) return tasks;

    const targetColumn = getColumnForStatus(newStatus);
    if (!COLUMN_IDS.has(targetColumn)) return tasks;

    const listsByColumn = new Map<string, Task[]>();
    for (const column of COLUMNS) {
        const statuses = getStatusesForColumn(column.status);
        const columnTasks = tasks.filter(task => statuses.includes(task.currentStatus) && task.id !== taskId);
        listsByColumn.set(column.status, columnTasks);
    }

    const targetList = listsByColumn.get(targetColumn) ?? [];
    const clampedIndex = Math.max(0, Math.min(targetIndex, targetList.length));
    targetList.splice(clampedIndex, 0, { ...movingTask, currentStatus: newStatus });
    listsByColumn.set(targetColumn, targetList);

    const ordered: Task[] = [];
    const included = new Set<string>();
    for (const column of COLUMNS) {
        for (const task of listsByColumn.get(column.status) ?? []) {
            ordered.push(task);
            included.add(task.id);
        }
    }
    for (const task of tasks) {
        if (!included.has(task.id)) {
            ordered.push(task);
        }
    }
    return ordered;
}

// Custom collision detection that prioritizes columns over tasks
const collisionDetection: CollisionDetection = (args) => {
    // First check for pointer within droppables
    const pointerCollisions = pointerWithin(args);

    if (pointerCollisions.length > 0) {
        const activeId = args.active.id as string;
        // Prioritize task droppables for precise middle insertions.
        const taskCollision = pointerCollisions.find(c => {
            const id = c.id as string;
            return !COLUMN_IDS.has(id) && id !== activeId;
        });
        if (taskCollision) {
            return [taskCollision];
        }

        // Fallback to column droppable (empty space / empty column).
        const columnCollision = pointerCollisions.find(c => COLUMN_IDS.has(c.id as string));
        if (columnCollision) {
            return [columnCollision];
        }
        return pointerCollisions;
    }

    // Fall back to rect intersection
    return rectIntersection(args);
};



const HIDDEN_COLUMNS_STORAGE_KEY = 'taskit-kanban-hidden-columns';
const COLUMN_MIN_HEIGHT = 280;
const COLUMN_BOTTOM_MARGIN = 24;
const DRAG_CLICK_SUPPRESS_MS = 200;



const SortableTask = memo(function SortableTask({
    task,
    onClick,
    suppressClickUntil,
    memberMap,
    blockedByFailed,
    onRename,
}: {
    task: Task;
    onClick: (task: Task) => void;
    suppressClickUntil: number;
    memberMap?: Map<string, Member>;
    blockedByFailed?: boolean;
    onRename?: (taskId: string, newTitle: string) => Promise<void> | void;
}) {
    const {
        attributes,
        listeners,
        setNodeRef,
        transform,
        transition,
        isDragging,
    } = useSortable({ id: task.id });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
    };

    return (
        <div ref={setNodeRef} style={style} {...attributes} {...listeners} className="mb-2">
            <TaskCard
                task={task}
                onClick={(t: Task) => {
                    const suppressClick = Date.now() < suppressClickUntil;
                    if (!isDragging && !suppressClick) onClick(t);
                }}
                isDragging={isDragging}
                hideStatus={task.currentStatus !== 'EXECUTING'}
                memberMap={memberMap}
                blockedByFailed={blockedByFailed}
                onRename={onRename}
                showInlineEdit
                compact
            />
        </div>
    );
});

const COLUMN_TASK_LIMIT = 20;

function DroppableColumn({
    column,
    tasks,
    onTaskClick,
    suppressClickUntil,
    isFailed,
    failedCollapsed,
    setFailedCollapsed,
    isOver,
    memberMap,
    failedTaskIds,
    maxColumnHeight,
    onTaskRename,
}: {
    column: typeof COLUMNS[0];
    tasks: Task[];
    onTaskClick: (task: Task) => void;
    suppressClickUntil: number;
    isFailed: boolean;
    failedCollapsed: boolean;
    setFailedCollapsed: (v: boolean) => void;
    isOver: boolean;
    memberMap?: Map<string, Member>;
    failedTaskIds?: Set<string>;
    maxColumnHeight: number;
    onTaskRename?: (taskId: string, newTitle: string) => Promise<void> | void;
}) {
    const { setNodeRef } = useDroppable({
        id: column.status,
    });
    const [showAll, setShowAll] = useState(false);

    const Icon = column.icon;
    const statusColor = getStatusColor(column.status);
    const isCollapsed = isFailed && failedCollapsed;
    const displayTasks = showAll ? tasks : tasks.slice(0, COLUMN_TASK_LIMIT);
    const hiddenTaskCount = tasks.length - displayTasks.length;

    return (
        <div className="flex-shrink-0 w-[280px] self-start">
            <Card
                className={`border-border flex flex-col overflow-hidden transition-all ${isOver ? 'ring-2 ring-primary/50 bg-primary/5' : ''}`}
                style={{ maxHeight: `${maxColumnHeight}px` }}
            >
                <CardHeader className="p-3 pb-2 shrink-0 border-b border-border/70">
                    <CardTitle className="flex items-center justify-between text-sm">
                        <div className="flex items-center gap-2">
                            <div className="size-2 rounded-full" style={{ background: statusColor }} />
                            <Icon className="size-3.5 text-muted-foreground" />
                            <span>{column.label}</span>
                        </div>
                        <div className="flex items-center gap-1">
                            <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">{tasks.length}</Badge>
                            {isFailed && tasks.length > 0 && (
                                <Button variant="ghost" size="icon" className="size-5"
                                    onClick={() => setFailedCollapsed(!failedCollapsed)}>
                                    {isCollapsed ? <ChevronDown className="size-3" /> : <ChevronUp className="size-3" />}
                                </Button>
                            )}
                        </div>
                    </CardTitle>
                </CardHeader>
                {!(isCollapsed && tasks.length > 0) && (
                    <CardContent
                        ref={setNodeRef}
                        className="p-2 pt-0 flex-1 min-h-[180px] overflow-y-auto"
                        data-testid={`kanban-column-content-${column.status}`}
                    >
                        <div className="flex flex-col gap-2 p-1 min-h-full">
                            <SortableContext items={displayTasks.map(t => t.id)} strategy={verticalListSortingStrategy}>
                                {tasks.length === 0 && (
                                    <div className="min-h-[120px] flex items-center justify-center text-xs text-muted-foreground">
                                        Drop tasks here
                                    </div>
                                )}
                                {displayTasks.map(task => (
                                    <SortableTask key={task.id} task={task} onClick={onTaskClick} suppressClickUntil={suppressClickUntil} memberMap={memberMap}
                                        blockedByFailed={!!(failedTaskIds && task.dependsOn?.some(dep => failedTaskIds.has(dep)))} onRename={onTaskRename} />
                                ))}
                            </SortableContext>
                            {hiddenTaskCount > 0 && (
                                <Button variant="ghost" size="sm" className="text-xs text-muted-foreground w-full"
                                    onClick={() => setShowAll(true)}>
                                    Show {hiddenTaskCount} more
                                </Button>
                            )}
                            {showAll && tasks.length > COLUMN_TASK_LIMIT && (
                                <Button variant="ghost" size="sm" className="text-xs text-muted-foreground w-full"
                                    onClick={() => setShowAll(false)}>
                                    Show less
                                </Button>
                            )}
                        </div>
                    </CardContent>
                )}
            </Card>
        </div>
    );
}

export function KanbanBoard({ tasks, onTaskClick, onTaskMove, onStopExecution, onTaskRename, memberMap }: KanbanBoardProps) {
    const columnsRowRef = useRef<HTMLDivElement | null>(null);
    const [hiddenColumns, setHiddenColumns] = useState<Set<string>>(() => {
        try {
            const stored = localStorage.getItem(HIDDEN_COLUMNS_STORAGE_KEY);
            if (!stored) return new Set();

            const parsed = JSON.parse(stored);
            if (!Array.isArray(parsed)) return new Set();

            const validStatuses = new Set(COLUMNS.map(c => c.status));
            const allValid = parsed.every((s: string) => validStatuses.has(s));

            if (!allValid) return new Set();

            return new Set(parsed);
        } catch (e) {
            console.warn('Failed to load kanban settings:', e);
            return new Set();
        }
    });

    useEffect(() => {
        try {
            localStorage.setItem(HIDDEN_COLUMNS_STORAGE_KEY, JSON.stringify(Array.from(hiddenColumns)));
        } catch (e) {
            console.warn('Failed to save kanban settings:', e);
        }
    }, [hiddenColumns]);

    const [failedCollapsed, setFailedCollapsed] = useState(false);
    const [activeId, setActiveId] = useState<string | null>(null);
    const [overColumnId, setOverColumnId] = useState<string | null>(null);
    const [suppressClickUntil, setSuppressClickUntil] = useState(0);
    const [stopSubmitting, setStopSubmitting] = useState(false);
    const [pendingStopMove, setPendingStopMove] = useState<{ taskId: string; newStatus: string } | null>(null);

    // Local state to handle optimistic updates
    const [localTasks, setLocalTasks] = useState<Task[]>(tasks);

    // Sync local tasks with prop tasks when they change
    useEffect(() => {
        setLocalTasks(tasks);
    }, [tasks]);

    const [columnMaxHeight, setColumnMaxHeight] = useState<number>(COLUMN_MIN_HEIGHT);

    const updateColumnMaxHeight = useCallback(() => {
        const row = columnsRowRef.current;
        if (!row) return;

        const { top } = row.getBoundingClientRect();
        const availableHeight = Math.floor(window.innerHeight - top - COLUMN_BOTTOM_MARGIN);
        setColumnMaxHeight(Math.max(COLUMN_MIN_HEIGHT, availableHeight));
    }, []);

    useEffect(() => {
        updateColumnMaxHeight();
        window.addEventListener('resize', updateColumnMaxHeight);

        let resizeObserver: ResizeObserver | null = null;
        if (typeof ResizeObserver !== 'undefined' && columnsRowRef.current) {
            resizeObserver = new ResizeObserver(() => updateColumnMaxHeight());
            resizeObserver.observe(columnsRowRef.current);
        }

        return () => {
            window.removeEventListener('resize', updateColumnMaxHeight);
            resizeObserver?.disconnect();
        };
    }, [updateColumnMaxHeight]);

    const toggleColumn = (status: string) => {
        const next = new Set(hiddenColumns);
        if (next.has(status)) next.delete(status); else next.add(status);
        setHiddenColumns(next);
    };

    const sensors = useSensors(
        useSensor(PointerSensor, {
            activationConstraint: {
                distance: 8,
            },
        }),
        useSensor(KeyboardSensor, {
            coordinateGetter: sortableKeyboardCoordinates,
        })
    );

    const visibleColumns = useMemo(
        () => COLUMNS.filter(c => !hiddenColumns.has(c.status)),
        [hiddenColumns]
    );

    const taskById = useMemo(
        () => new Map(localTasks.map(t => [t.id, t])),
        [localTasks]
    );

    const tasksByColumn = useMemo(() => {
        const map = new Map<string, Task[]>();
        for (const col of COLUMNS) {
            const matchStatuses = col.includeStatuses ?? [col.status];
            const columnTasks = localTasks.filter(t => matchStatuses.includes(t.currentStatus));
            map.set(col.status, columnTasks);
        }
        return map;
    }, [localTasks]);

    const taskCountByColumn = useMemo(() => {
        const map = new Map<string, number>();
        for (const col of COLUMNS) {
            const matchStatuses = col.includeStatuses ?? [col.status];
            map.set(col.status, localTasks.filter(t => matchStatuses.includes(t.currentStatus)).length);
        }
        return map;
    }, [localTasks]);

    const resolveOverColumn = useCallback((id: string | null): string | null => {
        if (!id) return null;
        if (COLUMN_IDS.has(id)) return id;
        const task = taskById.get(id);
        return task ? getColumnForStatus(task.currentStatus) : null;
    }, [taskById]);

    const handleDragStart = (event: DragStartEvent) => {
        setActiveId(event.active.id as string);
    };

    const handleDragOver = (event: DragOverEvent) => {
        const { over } = event;
        if (!over) {
            setOverColumnId(null);
            return;
        }
        setOverColumnId(resolveOverColumn(over.id as string));
    };

    const getDropTargetIndex = useCallback((event: DragEndEvent, destinationColumn: string): number => {
        const over = event.over;
        if (!over) return 0;

        const overId = over.id as string;
        const destinationTasks = tasksByColumn.get(destinationColumn) ?? [];

        if (COLUMN_IDS.has(overId)) {
            return destinationTasks.length;
        }

        const overIndex = destinationTasks.findIndex(task => task.id === overId);
        if (overIndex === -1) {
            return destinationTasks.length;
        }

        const activeRect = event.active.rect.current.translated ?? event.active.rect.current.initial;
        const overRect = over.rect;
        const isBelowOverItem = !!(
            activeRect &&
            overRect &&
            activeRect.top > (overRect.top + (overRect.height / 2))
        );
        return overIndex + (isBelowOverItem ? 1 : 0);
    }, [tasksByColumn]);

    const handleDragEnd = (event: DragEndEvent) => {
        const { active, over } = event;
        setActiveId(null);
        setOverColumnId(null);
        setSuppressClickUntil(Date.now() + DRAG_CLICK_SUPPRESS_MS);

        if (!over) {
            // Revert optimistic update
            setLocalTasks(tasks);
            return;
        }

        const taskId = active.id as string;
        const overId = over.id as string;

        // Find the task being dragged
        const task = taskById.get(taskId);
        if (!task) {
            setLocalTasks(tasks);
            return;
        }

        const destinationColumn = resolveOverColumn(overId);
        if (!destinationColumn) {
            setLocalTasks(tasks);
            return;
        }

        const sourceColumn = getColumnForStatus(task.currentStatus);
        const sourceTasks = tasksByColumn.get(sourceColumn) ?? [];
        const sourceIndex = sourceTasks.findIndex(t => t.id === taskId);
        let targetIndex = getDropTargetIndex(event, destinationColumn);

        if (sourceColumn === destinationColumn && sourceIndex !== -1 && sourceIndex < targetIndex) {
            targetIndex -= 1;
        }

        const newStatus = getTargetStatusForColumn(task, destinationColumn);
        const isNoOpMove = sourceColumn === destinationColumn && sourceIndex === targetIndex;
        if (isNoOpMove) return;

        if (task.currentStatus === 'EXECUTING' && newStatus !== task.currentStatus) {
            setLocalTasks(tasks);
            setPendingStopMove({ taskId, newStatus });
            return;
        }

        if (!onTaskMove) return;

        const optimistic = applyOptimisticMove(localTasks, taskId, newStatus, targetIndex);
        setLocalTasks(optimistic);

        void (async () => {
            const ok = await onTaskMove(taskId, { status: newStatus, targetIndex });
            if (!ok) {
                setLocalTasks(tasks);
            }
        })();
    };

    const handleDragCancel = () => {
        setActiveId(null);
        setOverColumnId(null);
        setSuppressClickUntil(Date.now() + DRAG_CLICK_SUPPRESS_MS);
        // Revert optimistic update
        setLocalTasks(tasks);
    };

    const handleConfirmStopMove = async () => {
        if (!pendingStopMove || !onStopExecution || stopSubmitting) return;
        setStopSubmitting(true);
        const ok = await onStopExecution(pendingStopMove.taskId, pendingStopMove.newStatus);
        setStopSubmitting(false);
        setPendingStopMove(null);
        if (!ok) setLocalTasks(tasks);
    };

    const handleCancelStopMove = () => {
        if (stopSubmitting) return;
        setPendingStopMove(null);
        setLocalTasks(tasks);
    };

    const failedTaskIds = useMemo(() => new Set(localTasks.filter(t => t.currentStatus === 'FAILED').map(t => t.id)), [localTasks]);

    const activeTask = useMemo(() => localTasks.find(t => t.id === activeId), [localTasks, activeId]);

    return (
        <DndContext
            sensors={sensors}
            collisionDetection={collisionDetection}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDragEnd={handleDragEnd}
            onDragCancel={handleDragCancel}
        >
            <div>
                {/* Column visibility toggles */}
                <div className="flex flex-wrap gap-2 mb-4">
                    {COLUMNS.map(col => {
                        const Icon = col.icon;
                        const isVisible = !hiddenColumns.has(col.status);
                        const count = taskCountByColumn.get(col.status) ?? 0;
                        return (
                            <Button key={col.status} variant={isVisible ? 'default' : 'outline'} size="sm"
                                className="h-7 text-xs gap-1.5" onClick={() => toggleColumn(col.status)}>
                                <Icon className="size-3" />
                                {col.label}
                                {count > 0 && <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">{count}</Badge>}
                            </Button>
                        );
                    })}
                </div>

                {/* Kanban columns */}
                <div
                    className="flex items-start gap-4 overflow-x-auto pb-4 pr-2"
                    ref={columnsRowRef}
                    data-testid="kanban-columns-row"
                    style={{ minHeight: `${columnMaxHeight}px` }}
                >
                    {visibleColumns.map(col => {
                        const columnTasks = tasksByColumn.get(col.status) ?? [];
                        return (
                            <DroppableColumn
                                key={col.status}
                                column={col}
                                tasks={columnTasks}
                                onTaskClick={onTaskClick}
                                suppressClickUntil={suppressClickUntil}
                                isFailed={col.status === 'FAILED'}
                                failedCollapsed={failedCollapsed}
                                setFailedCollapsed={setFailedCollapsed}
                                isOver={overColumnId === col.status}
                                memberMap={memberMap}
                                failedTaskIds={failedTaskIds}
                                maxColumnHeight={columnMaxHeight}
                                onTaskRename={onTaskRename}
                            />
                        );
                    })}
                </div>
            </div>

            <DragOverlay dropAnimation={{
                sideEffects: defaultDropAnimationSideEffects({
                    styles: {
                        active: {
                            opacity: '0.5',
                        },
                    },
                }),
            }}>
                {activeTask ? <TaskCard task={activeTask} isOverlay onClick={() => { }} hideStatus memberMap={memberMap} compact /> : null}
            </DragOverlay>

            <AlertDialog open={!!pendingStopMove} onOpenChange={(open) => { if (!open) handleCancelStopMove(); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Task is currently executing</AlertDialogTitle>
                        <AlertDialogDescription>
                            This task is already executing. Are you sure you want to stop execution?
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel
                            onClick={(e) => { e.preventDefault(); handleCancelStopMove(); }}
                        >
                            No, keep executing
                        </AlertDialogCancel>
                        <AlertDialogAction
                            onClick={(e) => { e.preventDefault(); void handleConfirmStopMove(); }}
                        >
                            {stopSubmitting ? 'Stopping...' : 'Yes, stop and move'}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </DndContext>
    );
}
