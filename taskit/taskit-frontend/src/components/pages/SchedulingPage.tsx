import { useEffect, useMemo, useState } from 'react';
import { format } from 'date-fns';
import type { TaskSchedule } from '@/types';
import { useService } from '@/contexts/ServiceContext';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CalendarClock, History, Repeat } from 'lucide-react';

interface SchedulingPageProps {
    selectedBoard?: string;
    refreshKey?: number;
    onTaskClick: (taskId: string) => void;
}

function fmt(ts?: string | null) {
    if (!ts) return '—';
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

export function SchedulingPage({ selectedBoard, refreshKey = 0, onTaskClick }: SchedulingPageProps) {
    const service = useService();
    const [schedules, setSchedules] = useState<TaskSchedule[]>([]);
    const [historyMode, setHistoryMode] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const resp = await service.fetchSchedules({
                    board: selectedBoard,
                    history: historyMode,
                    page: 1,
                    page_size: 200,
                });
                if (!cancelled) setSchedules(resp.results);
            } catch (e) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load schedules');
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => {
            cancelled = true;
        };
    }, [service, selectedBoard, historyMode, refreshKey]);

    const emptyLabel = useMemo(
        () => historyMode ? 'No schedule history yet.' : 'No upcoming scheduled work.',
        [historyMode],
    );

    const handlePauseResume = async (schedule: TaskSchedule) => {
        if (schedule.status === 'PAUSED') await service.resumeSchedule(String(schedule.id));
        else await service.pauseSchedule(String(schedule.id));
        const resp = await service.fetchSchedules({ board: selectedBoard, history: historyMode, page: 1, page_size: 200 });
        setSchedules(resp.results);
    };

    const handleCancel = async (schedule: TaskSchedule) => {
        await service.cancelSchedule(String(schedule.id));
        const resp = await service.fetchSchedules({ board: selectedBoard, history: historyMode, page: 1, page_size: 200 });
        setSchedules(resp.results);
    };

    return (
        <div className="space-y-5">
            <div className="flex items-center justify-between gap-4">
                <div>
                    <h2 className="text-lg font-semibold">Scheduling</h2>
                    <p className="text-sm text-muted-foreground">Upcoming releases and schedule history live here before work reaches the board.</p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant={historyMode ? 'outline' : 'default'} size="sm" onClick={() => setHistoryMode(false)}>Upcoming</Button>
                    <Button variant={historyMode ? 'default' : 'outline'} size="sm" onClick={() => setHistoryMode(true)}>History</Button>
                </div>
            </div>

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
                                    {!historyMode && schedule.status !== 'CANCELED' && schedule.status !== 'COMPLETED' ? (
                                        <>
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
                                <div className="mb-3 text-sm text-muted-foreground line-clamp-3">
                                    {compactText(schedule.template.description, 180)}
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
        </div>
    );
}
