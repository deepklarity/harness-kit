import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { format } from 'date-fns';
import type { TaskSchedule } from '@/types';
import { useService } from '@/contexts/ServiceContext';
import { useToast } from '@/hooks/use-toast';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
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
import { ToastAction } from '@/components/ui/toast';
import { CalendarClock, History, Repeat } from 'lucide-react';
import { FilterBar, SearchBar, MultiSelectFilter, SortControl, PaginationControls } from '@/components/filters';

interface SchedulingPageProps {
    selectedBoard?: string;
    refreshKey?: number;
    onTaskClick: (taskId: string) => void;
}

function splitParam(value: string | null): string[] {
    if (!value) return [];
    return value.split(',').map(v => v.trim()).filter(Boolean);
}

function fmt(ts?: string | null) {
    if (!ts) return '\u2014';
    return format(new Date(ts), 'PPpp');
}

function compactText(value?: string, max = 220) {
    if (!value) return '';
    const normalized = value.replace(/\s+/g, ' ').trim();
    if (normalized.length <= max) return normalized;
    return `${normalized.slice(0, max).trim()}...`;
}

function scheduleSummary(schedule: TaskSchedule) {
    if (schedule.kind === 'ONE_TIME') {
        return `Runs once on ${fmt(schedule.starts_at_utc)}.`;
    }

    const recurrence = schedule.recurrence_rule || {};
    const freq = String(recurrence.freq || '').toUpperCase();
    const interval = Math.max(1, Number(recurrence.interval) || 1);
    const baseTime = format(new Date(schedule.starts_at_utc), 'h:mm a');

    if (freq === 'DAILY') {
        return interval === 1
            ? `Runs every day at ${baseTime}.`
            : `Runs every ${interval} days at ${baseTime}.`;
    }

    if (freq === 'WEEKLY') {
        const days = Array.isArray(recurrence.by_weekday) && recurrence.by_weekday.length > 0
            ? recurrence.by_weekday.join(', ')
            : format(new Date(schedule.starts_at_utc), 'EEE').toUpperCase();
        return interval === 1
            ? `Runs every week on ${days} at ${baseTime}.`
            : `Runs every ${interval} weeks on ${days} at ${baseTime}.`;
    }

    if (freq === 'MONTHLY') {
        const day = Array.isArray(recurrence.by_monthday) && recurrence.by_monthday.length > 0
            ? recurrence.by_monthday[0]
            : format(new Date(schedule.starts_at_utc), 'd');
        return interval === 1
            ? `Runs every month on day ${day} at ${baseTime}.`
            : `Runs every ${interval} months on day ${day} at ${baseTime}.`;
    }

    return `Repeats from ${fmt(schedule.starts_at_utc)}.`;
}

function scheduleStateLabel(schedule: TaskSchedule) {
    if (schedule.status === 'COMPLETED') return 'No future runs';
    if (schedule.status === 'CANCELED') return 'Canceled';
    if (schedule.status === 'PAUSED') return 'Paused';
    return schedule.kind === 'ONE_TIME' ? 'Waiting for release' : 'Tracking future runs';
}

const UPCOMING_STATUS_OPTIONS = [
    { label: 'Active', value: 'ACTIVE' },
    { label: 'Paused', value: 'PAUSED' },
];
const HISTORY_STATUS_OPTIONS = [
    { label: 'Completed', value: 'COMPLETED' },
    { label: 'Canceled', value: 'CANCELED' },
];
const KIND_OPTIONS = [
    { label: 'One-time', value: 'ONE_TIME' },
    { label: 'Recurring', value: 'RECURRING' },
];
const SORT_OPTIONS = [
    { label: 'Next run', value: 'next_run_at_utc' },
    { label: 'Created date', value: 'created_at' },
    { label: 'Title', value: 'template_title' },
    { label: 'Kind', value: 'kind' },
    { label: 'Status', value: 'status' },
    { label: 'Start time', value: 'starts_at_utc' },
];

export function SchedulingPage({ selectedBoard, refreshKey = 0, onTaskClick }: SchedulingPageProps) {
    const service = useService();
    const { toast } = useToast();
    const [searchParams, setSearchParams] = useSearchParams();
    const [expandedBriefs, setExpandedBriefs] = useState<Set<number>>(new Set());
    const [schedules, setSchedules] = useState<TaskSchedule[]>([]);
    const [count, setCount] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [confirmingRunScheduleId, setConfirmingRunScheduleId] = useState<string | null>(null);
    const [runningScheduleId, setRunningScheduleId] = useState<string | null>(null);

    const query = useMemo(() => {
        const page = Number(searchParams.get('page') || '1');
        const pageSize = Number(searchParams.get('page_size') || '20');
        return {
            q: searchParams.get('q') || '',
            status: splitParam(searchParams.get('status')),
            kind: splitParam(searchParams.get('kind')),
            history: searchParams.get('history') === 'true',
            sort: searchParams.get('sort') || undefined,
            created_from: searchParams.get('created_from') || undefined,
            created_to: searchParams.get('created_to') || undefined,
            page: Number.isNaN(page) ? 1 : page,
            page_size: Number.isNaN(pageSize) ? 20 : pageSize,
        };
    }, [searchParams]);

    const setParam = useCallback((key: string, value?: string) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            const current = next.get(key) || '';
            const nextValue = value || '';
            const changed = current !== nextValue;
            if (!value) next.delete(key);
            else next.set(key, value);
            if (key !== 'page' && changed) next.set('page', '1');
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    const fetchQuery = useMemo(() => ({
        board: selectedBoard,
        status: query.status.length ? query.status : undefined,
        kind: query.kind.length ? query.kind : undefined,
        history: query.history,
        q: query.q || undefined,
        sort: query.sort,
        created_from: query.created_from,
        created_to: query.created_to,
        page: query.page,
        page_size: query.page_size,
    }), [selectedBoard, query]);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);
        void (async () => {
            try {
                const resp = await service.fetchSchedules(fetchQuery);
                if (!cancelled) {
                    setSchedules(resp.results);
                    setCount(resp.count);
                }
            } catch (e) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load schedules');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [service, fetchQuery, refreshKey]);

    const emptyLabel = useMemo(
        () => query.history ? 'No schedule history yet.' : 'No upcoming scheduled work.',
        [query.history],
    );

    const refetch = useCallback(async () => {
        const resp = await service.fetchSchedules(fetchQuery);
        setSchedules(resp.results);
        setCount(resp.count);
    }, [service, fetchQuery]);

    const handlePauseResume = async (schedule: TaskSchedule) => {
        if (schedule.status === 'PAUSED') await service.resumeSchedule(String(schedule.id));
        else await service.pauseSchedule(String(schedule.id));
        await refetch();
    };

    const handleCancel = async (schedule: TaskSchedule) => {
        await service.cancelSchedule(String(schedule.id));
        await refetch();
    };

    const handleRunNow = async (schedule: TaskSchedule) => {
        const scheduleKey = String(schedule.id);
        if (runningScheduleId) return;
        setRunningScheduleId(scheduleKey);
        try {
            const result = await service.runScheduleNow(scheduleKey);
            const newTaskId = result?.task_id != null ? String(result.task_id) : null;
            toast({
                title: 'Manual run dispatched',
                description: newTaskId
                    ? `Task #${newTaskId} created from "${schedule.template.title}".`
                    : `"${schedule.template.title}" fired successfully.`,
                action: newTaskId ? (
                    <ToastAction altText="Open created task" onClick={() => onTaskClick(newTaskId)}>
                        Open task
                    </ToastAction>
                ) : undefined,
            });
            await refetch();
        } catch (err) {
            const message = err instanceof Error ? err.message : 'Failed to run schedule';
            toast({
                title: 'Run now failed',
                description: message,
                variant: 'destructive',
            });
        } finally {
            setRunningScheduleId(null);
        }
    };

    const confirmingSchedule = confirmingRunScheduleId
        ? schedules.find(s => String(s.id) === confirmingRunScheduleId)
        : null;

    const statusOptions = query.history ? HISTORY_STATUS_OPTIONS : UPCOMING_STATUS_OPTIONS;

    const hasFilters = !!(query.q || query.status.length || query.kind.length || query.sort || query.created_from || query.created_to);

    return (
        <div className="space-y-5">
            <div className="flex items-center justify-between gap-4">
                <div>
                    <h2 className="text-lg font-semibold">Scheduling</h2>
                    <p className="text-sm text-muted-foreground">Upcoming releases and schedule history live here before work reaches the board.</p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant={query.history ? 'outline' : 'default'} size="sm" onClick={() => setParam('history', undefined)}>Upcoming</Button>
                    <Button variant={query.history ? 'default' : 'outline'} size="sm" onClick={() => setParam('history', 'true')}>History</Button>
                </div>
            </div>

            <FilterBar
                onClearAll={() => {
                    setSearchParams(prev => {
                        const next = new URLSearchParams();
                        const history = prev.get('history');
                        if (history) next.set('history', history);
                        return next;
                    }, { replace: true });
                }}
                showClearAll={hasFilters}
            >
                <SearchBar
                    value={query.q}
                    onSearchChange={(value) => setParam('q', value || undefined)}
                    placeholder="Search schedule title or description..."
                    ariaLabel="Search schedules"
                />
                <MultiSelectFilter
                    label="Status"
                    options={statusOptions}
                    selected={query.status}
                    onChange={(next) => setParam('status', next.length ? next.join(',') : undefined)}
                />
                <MultiSelectFilter
                    label="Kind"
                    options={KIND_OPTIONS}
                    selected={query.kind}
                    onChange={(next) => setParam('kind', next.length ? next.join(',') : undefined)}
                />
                <SortControl
                    value={query.sort}
                    onChange={(value) => setParam('sort', value)}
                    options={SORT_OPTIONS}
                />
            </FilterBar>

            {error && <div className="text-sm text-destructive">{error}</div>}
            {loading ? <div className="text-sm text-muted-foreground">Loading schedules...</div> : null}
            {!loading && schedules.length === 0 ? <div className="text-sm text-muted-foreground">{emptyLabel}</div> : null}

            <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-5">
                {schedules.map(schedule => (
                    <Card key={schedule.id} className="border-border/70 shadow-sm transition-colors hover:border-primary/30">
                        <CardContent className="p-4">
                            <div className="mb-3 flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <CardTitle className="truncate text-base leading-tight">{schedule.template.title}</CardTitle>
                                    <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                                        <Badge variant="outline" className="h-5 px-2 text-[10px]">
                                            {schedule.kind === 'ONE_TIME' ? 'One-time' : 'Recurring'}
                                        </Badge>
                                        <Badge variant={schedule.status === 'ACTIVE' ? 'default' : 'outline'} className="h-5 px-2 text-[10px]">
                                            {schedule.status}
                                        </Badge>
                                        <span className="truncate">{schedule.timezone}</span>
                                    </div>
                                </div>
                                <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                                    {schedule.materialized_task_id ? (
                                        <Button size="sm" variant="outline" className="h-8 px-3 text-xs" onClick={() => onTaskClick(String(schedule.materialized_task_id))}>
                                            Task Details
                                        </Button>
                                    ) : null}
                                    {!query.history && schedule.status !== 'CANCELED' && schedule.status !== 'COMPLETED' ? (
                                        <>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                className="h-8 px-2 text-xs"
                                                onClick={() => setConfirmingRunScheduleId(String(schedule.id))}
                                                disabled={runningScheduleId === String(schedule.id)}
                                                data-testid="run-now-button"
                                            >
                                                {runningScheduleId === String(schedule.id) ? 'Running…' : 'Run now'}
                                            </Button>
                                            <Button size="sm" variant="ghost" className="h-8 px-2 text-xs" onClick={() => void handlePauseResume(schedule)}>
                                                {schedule.status === 'PAUSED' ? 'Resume' : 'Pause'}
                                            </Button>
                                            <Button size="sm" variant="ghost" className="h-8 px-2 text-xs" onClick={() => void handleCancel(schedule)}>
                                                Cancel
                                            </Button>
                                        </>
                                    ) : null}
                                </div>
                            </div>

                            <div className="mb-3 flex items-start gap-2 rounded-md border bg-muted/25 px-3 py-2 text-sm">
                                {schedule.kind === 'ONE_TIME' ? (
                                    <CalendarClock className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                                ) : (
                                    <Repeat className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                                )}
                                <span className="leading-5">{scheduleSummary(schedule)}</span>
                            </div>

                            <div className="mb-3 grid grid-cols-2 gap-3 text-xs">
                                <div className="rounded-md border px-3 py-2">
                                    <div className="mb-1 uppercase tracking-wide text-muted-foreground">Next run</div>
                                    <div className="font-medium text-foreground">{fmt(schedule.next_run_at_utc)}</div>
                                </div>
                                <div className="rounded-md border px-3 py-2">
                                    <div className="mb-1 uppercase tracking-wide text-muted-foreground">State</div>
                                    <div className="font-medium text-foreground">{scheduleStateLabel(schedule)}</div>
                                </div>
                            </div>

                            {schedule.template.description ? (
                                <div className="mb-3">
                                    <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Task brief</div>
                                    {expandedBriefs.has(schedule.id) ? (
                                        <div className="rounded-md border bg-muted/25 px-3 py-2 text-sm text-foreground whitespace-pre-wrap max-h-72 overflow-y-auto">
                                            {schedule.template.description}
                                        </div>
                                    ) : (
                                        <div className="text-sm text-muted-foreground">
                                            {compactText(schedule.template.description, 180)}
                                        </div>
                                    )}
                                    <button
                                        type="button"
                                        className="mt-1 text-xs text-primary hover:underline"
                                        onClick={() => setExpandedBriefs(prev => {
                                            const next = new Set(prev);
                                            if (next.has(schedule.id)) next.delete(schedule.id); else next.add(schedule.id);
                                            return next;
                                        })}
                                    >
                                        {expandedBriefs.has(schedule.id) ? 'Hide full brief' : 'Show full brief'}
                                    </button>
                                </div>
                            ) : null}

                            {Array.isArray(schedule.runs) && schedule.runs.length > 0 ? (
                                <div className="border-t pt-3">
                                    <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                                        <History className="h-3.5 w-3.5" />
                                        Recent Runs
                                    </div>
                                    <div className="space-y-2">
                                        {schedule.runs.slice(0, 5).map(run => (
                                            <div key={run.id} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                                                <div className="min-w-0">
                                                    <div className="text-sm font-medium">Run #{run.run_number}</div>
                                                    <div className="text-xs text-muted-foreground">
                                                        {fmt(run.scheduled_for_utc)}
                                                    </div>
                                                    {run.result_summary ? (
                                                        <div className="mt-0.5 text-xs text-muted-foreground line-clamp-1">
                                                            {compactText(run.result_summary, 72)}
                                                        </div>
                                                    ) : null}
                                                </div>
                                                <div className="flex shrink-0 items-center gap-2">
                                                    <Badge variant="outline" className="text-[10px]">
                                                        {run.status}
                                                    </Badge>
                                                    {run.task_id ? (
                                                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onTaskClick(String(run.task_id))}>
                                                            Open
                                                        </Button>
                                                    ) : null}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            ) : null}
                        </CardContent>
                    </Card>
                ))}
            </div>

            {!loading && count > 0 && (
                <PaginationControls
                    count={count}
                    page={query.page}
                    pageSize={query.page_size}
                    onPageChange={(page) => setParam('page', String(page))}
                    onPageSizeChange={(size) => setParam('page_size', String(size))}
                />
            )}

            <AlertDialog open={!!confirmingSchedule} onOpenChange={(open) => !open && setConfirmingRunScheduleId(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Run this schedule now?</AlertDialogTitle>
                        <AlertDialogDescription>
                            {confirmingSchedule ? (
                                <>
                                    This will immediately create a new task from the
                                    {' '}<span className="font-medium text-foreground">{confirmingSchedule.template.title}</span>
                                    {' '}template and dispatch it to the board. The original schedule cadence is not affected.
                                </>
                            ) : null}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={!!runningScheduleId}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            disabled={!!runningScheduleId}
                            onClick={(e) => {
                                e.preventDefault();
                                if (confirmingSchedule) {
                                    const target = confirmingSchedule;
                                    setConfirmingRunScheduleId(null);
                                    void handleRunNow(target);
                                }
                            }}
                        >
                            {runningScheduleId ? 'Running…' : 'Run now'}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
