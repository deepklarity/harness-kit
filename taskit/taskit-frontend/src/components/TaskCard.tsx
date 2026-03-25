import { memo, useState, useMemo, type MouseEvent, type PointerEvent, type ReactNode } from 'react';

import type { Task, Member } from '../types';
import { getStatusIcon, classifyStatus } from '../utils/transformer';
import { TaskTimeDisplay } from './TaskTimeDisplay';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

import { Inbox, FileText, Package, User, AlertTriangle, MessageCircle, HelpCircle, Pencil, BellRing, Trash2, GitBranch } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

import { getSeenCommentCount } from '../utils/unseenComments';
import { hasUnseenExecutionCompletion } from '../utils/unseenStatusTransitions';

interface TaskCardProps {
    task: Task;
    onClick: (task: Task) => void;
    isOverlay?: boolean;
    isDragging?: boolean;
    hideStatus?: boolean;
    memberMap?: Map<string, Member>;
    allTasks?: Task[];
    taskMap?: Map<string, Task>;
    blockedByFailed?: boolean;
    onRename?: (taskId: string, newName: string) => Promise<void> | void;
    onDelete?: (taskId: string) => void;
    showInlineEdit?: boolean;
    compact?: boolean;
}



const COMPLEXITY_COLORS: Record<string, string> = {
    trivial: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-400 dark:border-emerald-800',
    simple: 'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-950 dark:text-blue-400 dark:border-blue-800',
    moderate: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-400 dark:border-amber-800',
    complex: 'bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-950 dark:text-orange-400 dark:border-orange-800',
    epic: 'bg-red-100 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-400 dark:border-red-800',
};

const STATUS_STYLES: Record<string, string> = {
    backlog: 'bg-secondary text-muted-foreground',
    todo: 'bg-secondary text-muted-foreground',
    executing: 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400 animate-pulse-subtle',
    doing: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-400',
    review: 'bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-400',
    testing: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-400',
    done: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400',
    failed: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400',
    other: 'bg-secondary text-muted-foreground',
};

export const TaskCard = memo(function TaskCard({
    task,
    onClick,
    isOverlay,
    isDragging,
    hideStatus,
    memberMap,
    allTasks = [],
    taskMap,
    blockedByFailed,
    onRename,
    onDelete,
    showInlineEdit = false,
    compact,
}: TaskCardProps) {
    const statusCategory = classifyStatus(task.currentStatus);
    const StatusIcon = getStatusIcon(task.currentStatus);

    const statusStyle = STATUS_STYLES[statusCategory] || STATUS_STYLES.other;

    const seenCount = getSeenCommentCount(task.id);
    const totalComments = task.commentCount ?? task.comments?.length ?? 0;
    const hasUnseen = totalComments > seenCount;
    const needsExecutionReviewAttention = hasUnseenExecutionCompletion(task);
    const hasPendingQuestion = !!(task.metadata?.has_pending_question);
    const model = task.modelName || (task.metadata?.model ?? task.metadata?.selected_model) as string | undefined;

    const assigneeName = task.assignees.length > 0
        ? (memberMap?.get(task.assigneeIds[0])?.fullName || task.assignees[0])
        : null;
    const assigneeColor = task.assignees.length > 0
        ? (memberMap?.get(task.assigneeIds[0])?.color || 'hsl(240, 60%, 50%)')
        : null;

    const resolvedTaskMap = useMemo(
        () => taskMap || new Map(allTasks.map(item => [item.id, item] as const)),
        [allTasks, taskMap],
    );
    const dependencyItems = (task.dependsOn || []).map(depId => ({ id: depId, task: resolvedTaskMap.get(depId) }));
    const dependencyLabel = dependencyItems
        .slice(0, compact ? 2 : 3)
        .map(({ id, task: depTask }) => `#${depTask?.idShort || id}`)
        .join(', ');
    const hiddenDependencyCount = Math.max(0, dependencyItems.length - (compact ? 2 : 3));

    const [isEditingTitle, setIsEditingTitle] = useState(false);
    const [draftTitle, setDraftTitle] = useState(task.title || task.name);
    const [savingTitle, setSavingTitle] = useState(false);
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

    const dependents = useMemo(() => {
        if (!onDelete || isOverlay || !allTasks.length) return [];
        return allTasks.filter(item => item.dependsOn?.includes(task.id));
    }, [task.id, allTasks, onDelete, isOverlay]);

    const canEditInline = !!showInlineEdit && !!onRename && !isOverlay && !compact;

    const startEditingTitle = (e: MouseEvent | PointerEvent) => {
        e.stopPropagation();
        setDraftTitle(task.title || task.name);
        setIsEditingTitle(true);
    };

    const cancelEditingTitle = (e?: MouseEvent | PointerEvent) => {
        e?.stopPropagation();
        setIsEditingTitle(false);
        setDraftTitle(task.title || task.name);
    };

    const saveTitle = async (e: MouseEvent | PointerEvent) => {
        e.stopPropagation();
        if (!onRename) return;
        const nextTitle = draftTitle.trim();
        if (!nextTitle || nextTitle === (task.title || task.name)) {
            setIsEditingTitle(false);
            return;
        }
        setSavingTitle(true);
        try {
            await onRename(task.id, nextTitle);
            setIsEditingTitle(false);
        } finally {
            setSavingTitle(false);
        }
    };

    const handleDelete = (e: MouseEvent | PointerEvent) => {
        e.stopPropagation();
        setShowDeleteConfirm(true);
    };

    const confirmDelete = (e: MouseEvent) => {
        e.stopPropagation();
        if (onDelete) {
            onDelete(task.id);
        }
        setShowDeleteConfirm(false);
    };

    return (
        <>
            <Card
                className={`cursor-pointer group hover:shadow-md transition-all duration-200 hover:ring-1 hover:ring-ring/40 hover:border-ring/40
                ${compact ? 'py-0.5 gap-0.5' : ''}
                ${isOverlay ? 'shadow-xl cursor-grabbing ring-2 ring-primary/20 rotate-2 bg-background z-50' : ''}
                ${isDragging ? 'opacity-30' : ''}
                ${blockedByFailed ? 'border-red-300 dark:border-red-800 bg-red-50/50 dark:bg-red-950/20' : ''}
                ${needsExecutionReviewAttention ? 'border-amber-400 dark:border-amber-500 ring-1 ring-amber-300/80 dark:ring-amber-600/70' : ''}`}
                onClick={() => {
                    if (!isEditingTitle) onClick(task);
                }}
            >
                <CardContent className={compact ? "relative !px-2.5 !py-1.5" : "relative px-2 py-1.5 space-y-0.5"} title={compact ? task.name : undefined}>
                    {onDelete && !isOverlay && (
                        <button
                            className="absolute right-1.5 top-1.5 opacity-40 hover:opacity-100 transition-opacity p-1 rounded-sm hover:bg-destructive/10 text-muted-foreground hover:text-destructive z-20"
                            onClick={handleDelete}
                            onPointerDown={(e) => e.stopPropagation()}
                            title="Delete task"
                        >
                            <Trash2 className="size-3.5" />
                        </button>
                    )}
                    {compact ? (
                        <div className="space-y-0.5">
                            {hasPendingQuestion && (
                                <div
                                    className={`absolute top-1 ${onDelete ? 'right-7' : 'right-1'} z-10 inline-flex size-5 items-center justify-center rounded-full border border-amber-300 bg-amber-100 text-amber-600 shadow-sm dark:border-amber-700 dark:bg-amber-950 dark:text-amber-400 animate-pulse-subtle`}
                                    title="Pending question — needs reply"
                                >
                                    <BellRing className="size-2.5" />
                                </div>
                            )}
                            <div className="flex items-center gap-1.5 min-w-0">
                                <span className="text-muted-foreground font-mono font-semibold text-[10px] shrink-0">#{task.idShort}</span>
                                {task.complexity && !['HIGH', 'MEDIUM', 'LOW', 'CRITICAL'].includes(task.complexity.toUpperCase()) && (
                                    <span className={`text-[9px] font-semibold uppercase shrink-0 px-1 rounded-sm border leading-tight ${COMPLEXITY_COLORS[task.complexity.toLowerCase()] || 'bg-muted text-muted-foreground'}`}>{task.complexity}</span>
                                )}
                                {blockedByFailed && <AlertTriangle className="size-2.5 shrink-0 text-red-500" />}
                                {task.labels && task.labels.length > 0 && (
                                    <div className="flex gap-0.5 shrink-0 ml-auto">
                                        {task.labels.map(label => (
                                            <span key={label.id} className="size-1.5 rounded-full" style={{ backgroundColor: label.color }} />
                                        ))}
                                    </div>
                                )}
                            </div>

                            <div className="font-medium text-[12.5px] leading-snug truncate group-hover:text-primary transition-colors">
                                {task.name}
                            </div>

                            {(task.specName || model) && (
                                <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground truncate">
                                    {task.specName && (
                                        <span className="flex items-center gap-0.5 truncate min-w-0">
                                            <FileText className="size-2.5 shrink-0 opacity-60" />
                                            <span className="truncate">{task.specName}</span>
                                        </span>
                                    )}
                                    {model && !['HIGH', 'MEDIUM', 'LOW', 'CRITICAL'].includes(model.toUpperCase()) && (
                                        <span className="flex items-center gap-0.5 shrink-0 font-mono">
                                            <Package className="size-2.5 shrink-0 opacity-60" />
                                            {model}
                                        </span>
                                    )}
                                </div>
                            )}

                            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                                {!hideStatus && (
                                    <Badge variant="outline" className={`text-[10px] h-4 px-1 font-medium gap-0.5 border-0 rounded-sm ${statusStyle}`}>
                                        <StatusIcon className="size-2.5" />
                                        {task.currentStatus}
                                    </Badge>
                                )}
                                {dependencyItems.length > 0 && (
                                    <DependencyPreview dependencyItems={dependencyItems}>
                                        <span className="inline-flex max-w-[120px] items-center gap-1 rounded-sm border border-amber-300/60 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-300">
                                            <GitBranch className="size-2.5 shrink-0" />
                                            <span className="truncate">Deps: {dependencyLabel}{hiddenDependencyCount > 0 ? ` +${hiddenDependencyCount}` : ''}</span>
                                        </span>
                                    </DependencyPreview>
                                )}
                                <div className="flex items-center gap-1 ml-auto shrink-0">
                                    {hasUnseen && (
                                        <span className="relative flex size-1.5" title={`${totalComments - seenCount} new`}>
                                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                                            <span className="relative inline-flex rounded-full size-1.5 bg-blue-500" />
                                        </span>
                                    )}
                                    {assigneeName ? (
                                        <span className="size-3.5 rounded-full flex items-center justify-center text-[7px] font-bold text-white" style={{ background: assigneeColor || undefined }}>
                                            {assigneeName.charAt(0)}
                                        </span>
                                    ) : (
                                        <User className="size-2.5" />
                                    )}
                                    <TaskTimeDisplay task={task} />
                                </div>
                            </div>
                        </div>
                    ) : (
                        <>
                            {canEditInline && !isEditingTitle && (
                                <button
                                    className={`absolute ${onDelete ? 'right-7' : 'right-1.5'} top-1.5 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded-sm hover:bg-secondary`}
                                    onClick={startEditingTitle}
                                    onPointerDown={(e) => e.stopPropagation()}
                                    title="Rename"
                                >
                                    <Pencil className="size-3.5 text-muted-foreground" />
                                </button>
                            )}
                            {blockedByFailed && (
                                <div className="flex items-center gap-1.5 text-[11px] font-medium text-red-600 dark:text-red-400">
                                    <AlertTriangle className="size-3 shrink-0" />
                                    <span>Blocked — dependency failed</span>
                                </div>
                            )}
                            <div className="flex items-center gap-1.5">
                                <span className="text-xs text-muted-foreground font-mono font-semibold">#{task.idShort}</span>
                                {task.complexity && !['HIGH', 'MEDIUM', 'LOW', 'CRITICAL'].includes(task.complexity.toUpperCase()) && (
                                    <Badge variant="outline" className={`text-[10px] h-4 px-1 rounded-sm border font-semibold uppercase ${COMPLEXITY_COLORS[task.complexity.toLowerCase()] || 'bg-muted text-muted-foreground'}`}>
                                        {task.complexity}
                                    </Badge>
                                )}
                                {task.labels && task.labels.length > 0 && (
                                    <div className="flex gap-0.5 ml-auto">
                                        {task.labels.map(label => (
                                            <span
                                                key={label.id}
                                                className="size-2 rounded-full"
                                                style={{ backgroundColor: label.color }}
                                                title={label.name}
                                            />
                                        ))}
                                    </div>
                                )}
                            </div>

                            {isEditingTitle ? (
                                <div className="space-y-1.5">
                                    <Input
                                        value={draftTitle}
                                        onChange={(e) => setDraftTitle(e.target.value)}
                                        onClick={(e) => e.stopPropagation()}
                                        onPointerDown={(e) => e.stopPropagation()}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Escape') {
                                                cancelEditingTitle();
                                            }
                                        }}
                                        autoFocus
                                        className="h-8 text-sm"
                                        disabled={savingTitle}
                                    />
                                    <div className="flex items-center justify-end gap-1">
                                        <Button
                                            size="sm"
                                            className="h-6 px-2 text-[10px]"
                                            onClick={saveTitle}
                                            onPointerDown={(e) => e.stopPropagation()}
                                            disabled={savingTitle || !draftTitle.trim()}
                                        >
                                            {savingTitle ? 'Saving...' : 'Save'}
                                        </Button>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            className="h-6 px-2 text-[10px]"
                                            onClick={cancelEditingTitle}
                                            onPointerDown={(e) => e.stopPropagation()}
                                            disabled={savingTitle}
                                        >
                                            Cancel
                                        </Button>
                                    </div>
                                </div>
                            ) : (
                                <div className="text-sm font-medium leading-snug line-clamp-2 group-hover:text-primary transition-colors">
                                    {task.title || task.name}
                                </div>
                            )}

                            {/* Row 3: Spec & Model context (Combined) */}
                            {(task.specName || (model && !['HIGH', 'MEDIUM', 'LOW', 'CRITICAL'].includes(model.toUpperCase()))) && (
                                <div className="flex items-center gap-2 text-muted-foreground text-[10px] truncate max-w-full">
                                    {task.specName && (
                                        <div className="flex items-center gap-1 truncate min-w-0 max-w-[50%]">
                                            <FileText className="size-2.5 shrink-0 opacity-70" />
                                            <span className="truncate">{task.specName}</span>
                                        </div>
                                    )}
                                    {task.specName && model && !['HIGH', 'MEDIUM', 'LOW', 'CRITICAL'].includes(model.toUpperCase()) && (
                                        <span className="text-muted-foreground/30">•</span>
                                    )}
                                    {model && !['HIGH', 'MEDIUM', 'LOW', 'CRITICAL'].includes(model.toUpperCase()) && (
                                        <div className="flex items-center gap-1 truncate min-w-0 font-mono">
                                            <Package className="size-2.5 shrink-0 opacity-70" />
                                            <span className="truncate">{model}</span>
                                        </div>
                                    )}
                                </div>
                            )}

                            <div className="flex items-center justify-between pt-1 border-t border-border">
                                <div className="flex items-center gap-1.5">
                                    {!hideStatus && (
                                        <Badge variant="outline" className={`text-[11px] h-5 px-1.5 font-medium gap-1 border-0 rounded-sm ${statusStyle}`}>
                                            <StatusIcon className="size-3" />
                                            {task.currentStatus}
                                        </Badge>
                                    )}
                                    {dependencyItems.length > 0 && (
                                        <DependencyPreview dependencyItems={dependencyItems}>
                                            <span className="inline-flex max-w-[170px] items-center gap-1 rounded-sm border border-amber-300/60 bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-300">
                                                <GitBranch className="size-3 shrink-0" />
                                                <span className="truncate">Deps: {dependencyLabel}{hiddenDependencyCount > 0 ? ` +${hiddenDependencyCount}` : ''}</span>
                                            </span>
                                        </DependencyPreview>
                                    )}
                                </div>

                                <div className="flex items-center gap-2 text-xs text-muted-foreground min-w-0">
                                    {assigneeName && (
                                        <div className="flex items-center gap-1 min-w-0" title={assigneeName}>
                                            <span className="size-4 rounded-full shrink-0 flex items-center justify-center text-[8px] font-bold text-white" style={{ background: assigneeColor || undefined }}>
                                                {assigneeName.charAt(0)}
                                            </span>
                                            <span className="truncate max-w-[90px] font-medium">{assigneeName}</span>
                                        </div>
                                    )}
                                    {!assigneeName && (
                                        <div className="flex items-center gap-1 text-muted-foreground" title="Unassigned">
                                            <User className="size-3" />
                                        </div>
                                    )}
                                    {hasPendingQuestion && (
                                        <div className="flex items-center gap-0.5 shrink-0" title="Pending question — needs reply">
                                            <span className="relative flex size-2">
                                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
                                                <span className="relative inline-flex rounded-full size-2 bg-amber-500" />
                                            </span>
                                            <HelpCircle className="size-3 text-amber-500" />
                                        </div>
                                    )}
                                    {hasUnseen && (
                                        <div className="flex items-center gap-0.5 shrink-0" title={`${totalComments - seenCount} new comment${totalComments - seenCount > 1 ? 's' : ''}`}>
                                            <span className="relative flex size-2">
                                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                                                <span className="relative inline-flex rounded-full size-2 bg-blue-500" />
                                            </span>
                                            <MessageCircle className="size-3 text-blue-500" />
                                        </div>
                                    )}
                                    <TaskTimeDisplay task={task} />
                                </div>
                            </div>
                        </>
                    )}
                </CardContent>
            </Card>

            <AlertDialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
                <AlertDialogContent onClick={(e) => e.stopPropagation()}>
                    <AlertDialogHeader>
                        <AlertDialogTitle className="flex items-center gap-2">
                            {dependents.length > 0 && <AlertTriangle className="size-5 text-destructive" />}
                            Delete Task?
                        </AlertDialogTitle>
                        <AlertDialogDescription className="space-y-3">
                            <div>
                                Do you really want to delete this task: <span className="font-bold text-foreground">"{task.title || task.name}"</span>?
                                This action cannot be undone.
                            </div>

                            {dependents.length > 0 && (
                                <div className="p-3 rounded-md bg-destructive/10 border border-destructive/20 text-destructive text-sm space-y-2">
                                    <div className="font-semibold flex items-center gap-1.5">
                                        <AlertTriangle className="size-4" />
                                        Warning: {dependents.length} {dependents.length === 1 ? 'task depends' : 'tasks depend'} on this
                                    </div>
                                    <ul className="list-disc list-inside opacity-90 text-[13px]">
                                        {dependents.slice(0, 3).map(dep => (
                                            <li key={dep.id} className="truncate">
                                                #{dep.idShort}: {dep.title || dep.name}
                                            </li>
                                        ))}
                                        {dependents.length > 3 && (
                                            <li>...and {dependents.length - 3} more</li>
                                        )}
                                    </ul>
                                    <p className="text-[12px] opacity-80 italic">
                                        Deleting this task may break the execution flow for these dependents.
                                    </p>
                                </div>
                            )}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel onClick={(e) => { e.stopPropagation(); setShowDeleteConfirm(false); }}>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={confirmDelete} className="bg-destructive hover:bg-destructive/90">
                            Delete
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
});

interface TaskListProps {
    tasks: Task[];
    onTaskClick: (task: Task) => void;
    onDelete?: (taskId: string) => void;
    memberMap?: Map<string, Member>;
    allTasks?: Task[];
}

export function TaskList({ tasks, onTaskClick, onDelete, memberMap, allTasks }: TaskListProps) {
    const taskMap = useMemo(
        () => new Map((allTasks || tasks).map(task => [task.id, task] as const)),
        [allTasks, tasks],
    );
    if (tasks.length === 0) {
        return (
            <div className="text-center py-16 text-muted-foreground">
                <Inbox className="size-10 mx-auto mb-3 opacity-50" />
                <div className="text-base font-medium mb-1">No tasks found</div>
                <p className="text-sm">Select a different board or create a task.</p>
            </div>
        );
    }

    return (
        <div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4 mb-4">
                {tasks.map(task => (
                    <TaskCard
                        key={task.id}
                        task={task}
                        onClick={onTaskClick}
                        onDelete={onDelete}
                        memberMap={memberMap}
                        allTasks={allTasks}
                        taskMap={taskMap}
                    />
                ))}
            </div>
        </div>
    );
}

function DependencyPreview({
    dependencyItems,
    children,
}: {
    dependencyItems: Array<{ id: string; task?: Task }>;
    children: ReactNode;
}) {
    return (
        <Popover>
            <PopoverTrigger asChild>
                <button
                    type="button"
                    className="inline-flex"
                    onClick={(e) => e.stopPropagation()}
                    onPointerDown={(e) => e.stopPropagation()}
                    title="View dependencies"
                >
                    {children}
                </button>
            </PopoverTrigger>
            <PopoverContent
                align="start"
                className="w-80"
                onClick={(e) => e.stopPropagation()}
                onPointerDown={(e) => e.stopPropagation()}
            >
                <div className="space-y-2">
                    <div className="text-sm font-medium">Dependencies</div>
                    {dependencyItems.map(({ id, task: depTask }) => (
                        <div key={id} className="rounded-md border px-3 py-2">
                            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                <span className="font-mono">#{depTask?.idShort || id}</span>
                                {depTask?.currentStatus && (
                                    <Badge variant="outline" className="h-5 text-[10px]">
                                        {depTask.currentStatus}
                                    </Badge>
                                )}
                            </div>
                            <div className="mt-1 text-sm font-medium">
                                {depTask?.title || depTask?.name || `Task ${id}`}
                            </div>
                        </div>
                    ))}
                </div>
            </PopoverContent>
        </Popover>
    );
}
