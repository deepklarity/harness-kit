import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, Loader2, Radar } from 'lucide-react';
import { useService } from '@/contexts/ServiceContext';
import { usePolling } from '@/hooks/usePolling';
import { useToast } from '@/hooks/use-toast';
import type { ProcessMonitorTask } from '@/types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
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

interface ProcessMonitorModalProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    boardId?: string;
    refreshKey?: number;
    onTaskStopped?: () => void;
}

export function ProcessMonitorModal({
    open,
    onOpenChange,
    boardId,
    refreshKey = 0,
    onTaskStopped,
}: ProcessMonitorModalProps) {
    const service = useService();
    const { toast } = useToast();
    const [loading, setLoading] = useState(false);
    const [tasks, setTasks] = useState<ProcessMonitorTask[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [stoppingTaskId, setStoppingTaskId] = useState<number | null>(null);
    const [confirmTask, setConfirmTask] = useState<ProcessMonitorTask | null>(null);

    const loadRunningTasks = useCallback(async () => {
        if (!open) return;
        setLoading(true);
        setError(null);
        try {
            const resp = await service.fetchProcessMonitor({ boardId, runningOnly: true });
            setTasks(
                (resp.tasks || []).filter(
                    (t) => t.status === 'EXECUTING' || t.odin_status === 'EXECUTING',
                ),
            );
        } catch (err) {
            console.error('Failed to fetch running tasks', err);
            setError('Failed to load running tasks.');
        } finally {
            setLoading(false);
        }
    }, [boardId, open, service]);

    useEffect(() => {
        if (!open) return;
        void loadRunningTasks();
    }, [open, refreshKey, loadRunningTasks]);

    usePolling(loadRunningTasks, {
        enabled: open,
        intervalMs: Number(import.meta.env.VITE_POLL_INTERVAL_MS || 15000),
        immediate: false,
    });

    const sortedTasks = useMemo(
        () => [...tasks].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
        [tasks],
    );
    const handleConfirmStop = useCallback(async () => {
        if (!confirmTask || stoppingTaskId !== null) return;
        setStoppingTaskId(confirmTask.task_id);
        try {
            await service.stopRuntimeTask(String(confirmTask.task_id), 'TODO');
            toast({
                title: 'Task stopped',
                description: `Task #${confirmTask.task_id} was stopped.`,
            });
            setConfirmTask(null);
            onTaskStopped?.();
            await loadRunningTasks();
        } catch (err) {
            console.error('Failed to stop running task', err);
            toast({
                title: 'Stop failed',
                description: 'Failed to stop the task.',
                variant: 'destructive',
            });
        } finally {
            setStoppingTaskId(null);
        }
    }, [confirmTask, loadRunningTasks, onTaskStopped, service, stoppingTaskId, toast]);

    return (
        <>
            <Dialog open={open} onOpenChange={onOpenChange}>
                <DialogContent className="w-[min(94vw,920px)] max-w-[920px] p-0 overflow-hidden gap-0">
                    <DialogHeader className="px-6 pt-6 pb-4 border-b bg-gradient-to-r from-background via-background to-muted/30">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <DialogTitle className="flex items-center gap-2 text-xl tracking-tight">
                                    <Radar className="size-5 text-primary" />
                                    Running Tasks
                                </DialogTitle>
                                <DialogDescription className="mt-1 text-xs">
                                    {boardId ? `Board scoped: ${boardId}` : 'All boards'} · EXECUTING tasks only
                                </DialogDescription>
                            </div>
                            <Button
                                size="sm"
                                variant="outline"
                                className="h-8"
                                onClick={() => void loadRunningTasks()}
                                disabled={loading}
                            >
                                {loading ? (
                                    <>
                                        <Loader2 className="size-3.5 animate-spin" /> Refreshing
                                    </>
                                ) : (
                                    'Refresh'
                                )}
                            </Button>
                        </div>
                    </DialogHeader>

                    <div className="px-6 py-4">
                        {error && <p className="text-sm text-destructive mb-3">{error}</p>}
                        {loading && sortedTasks.length === 0 ? (
                            <div className="h-48 flex items-center justify-center text-sm text-muted-foreground gap-2">
                                <Loader2 className="size-4 animate-spin" />
                                Loading running tasks...
                            </div>
                        ) : sortedTasks.length === 0 ? (
                            <div className="h-48 flex flex-col items-center justify-center text-center rounded-lg border border-dashed bg-muted/20">
                                <Activity className="size-8 text-muted-foreground/70 mb-2" />
                                <p className="text-sm font-medium">No running tasks right now</p>
                                <p className="text-xs text-muted-foreground mt-1">Tasks in EXECUTING state will appear here.</p>
                            </div>
                        ) : (
                            <ScrollArea className="max-h-[60vh] pr-3">
                                <div className="space-y-3">
                                    {sortedTasks.map((task) => {
                                        const canStop = task.status === 'EXECUTING' || task.odin_status === 'EXECUTING';
                                        const assignee = (task.assignee || '').trim();
                                        const agent = (task.agent || '').trim();
                                        const model = (task.model || '').trim();
                                        const showAgent = !!agent && agent.toLowerCase() !== assignee.toLowerCase();
                                        const showModel = !!model
                                            && model !== '-'
                                            && model.toLowerCase() !== agent.toLowerCase()
                                            && model.toLowerCase() !== assignee.toLowerCase();
                                        return (
                                            <div
                                                key={task.task_id}
                                                className="rounded-xl border bg-card/70 backdrop-blur px-4 py-3 flex items-center justify-between gap-3"
                                            >
                                                <div className="min-w-0 space-y-1">
                                                    <p className="text-sm font-semibold truncate">#{task.task_id} {task.title}</p>
                                                    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                                                        <Badge
                                                            variant="outline"
                                                            className={`h-5 px-1.5 text-[10px] ${canStop
                                                                ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30'
                                                                : ''}`}
                                                        >
                                                            {task.odin_status || task.status}
                                                        </Badge>
                                                        {!!assignee && <span>{assignee}</span>}
                                                        {showAgent && <span>{agent}</span>}
                                                        {showModel && <span className="truncate font-mono">{model}</span>}
                                                        {task.elapsed && <span>{task.elapsed}</span>}
                                                    </div>
                                                </div>
                                                <Button
                                                    size="sm"
                                                    variant="destructive"
                                                    className="h-8 shrink-0"
                                                    disabled={!canStop || stoppingTaskId === task.task_id}
                                                    onClick={() => setConfirmTask(task)}
                                                >
                                                    {!canStop ? 'Not Running' : (stoppingTaskId === task.task_id ? 'Stopping...' : 'Stop')}
                                                </Button>
                                            </div>
                                        );
                                    })}
                                </div>
                            </ScrollArea>
                        )}
                    </div>
                </DialogContent>
            </Dialog>

            <AlertDialog open={!!confirmTask} onOpenChange={(next) => { if (!next) setConfirmTask(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Stop running task?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Are you sure you want to stop this task?
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={stoppingTaskId !== null}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={(e) => {
                                e.preventDefault();
                                void handleConfirmStop();
                            }}
                            disabled={stoppingTaskId !== null}
                        >
                            {stoppingTaskId !== null ? 'Stopping...' : 'Yes, stop task'}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}
