import { useEffect, useState, useCallback, useMemo } from 'react';
import { ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useService } from '../../contexts/ServiceContext';
import type { Board, Spec } from '../../types';

interface AgentTaskRow {
    task_id: number;
    title: string;
    status: string;
    board_id: number | null;
    board_name: string | null;
    spec_id: number | null;
    spec_title: string | null;
    agent_name: string;
    model_name: string;
    created_at: string | null;
    last_updated_at: string | null;
}

interface AgentTasksDrilldownProps {
    agent: string;
    boards: Board[];
    boardFilter: string | undefined;
    searchParams: URLSearchParams;
    onParamsChange: (params: URLSearchParams) => void;
    onTaskClick: (taskId: string) => void;
    onBack: () => void;
}

export function AgentTasksDrilldown({
    agent,
    boards,
    boardFilter,
    searchParams,
    onParamsChange,
    onTaskClick,
    onBack,
}: AgentTasksDrilldownProps) {
    const service = useService();
    const [rows, setRows] = useState<AgentTaskRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [specs, setSpecs] = useState<Spec[]>([]);

    const statusFilter = searchParams.get('status') || '';
    const specFilter = searchParams.get('spec') || '';
    const boardFilterLocal = searchParams.get('board') || boardFilter || '';

    // Load the spec list once so the spec selector has options. Some
    // integrations don't expose fetchSpecs (it's optional on the
    // interface), so we degrade to an empty list — the selector
    // collapses to just "Any spec" but the existing `?spec` URL
    // parameter still filters correctly.
    useEffect(() => {
        let cancelled = false;
        const fetcher = service.fetchSpecs;
        if (!fetcher) {
            setSpecs([]);
            return () => { cancelled = true; };
        }
        fetcher().then(items => {
            if (cancelled) return;
            setSpecs(Array.isArray(items) ? items : []);
        }).catch(() => {
            if (cancelled) return;
            setSpecs([]);
        });
        return () => { cancelled = true; };
    }, [service]);

    // Specs scoped to the current board filter. When no board is
    // selected we still show all specs — the URL state is the source
    // of truth and the operator can drill through any spec.
    const visibleSpecs = useMemo(() => {
        if (!boardFilterLocal) return specs;
        return specs.filter(s => String(s.boardId) === String(boardFilterLocal));
    }, [specs, boardFilterLocal]);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const resp = await fetch(
                `/api/agents/${encodeURIComponent(agent)}/tasks/?` + new URLSearchParams({
                    ...(boardFilterLocal ? { board_id: boardFilterLocal } : {}),
                    ...(specFilter ? { spec_id: specFilter } : {}),
                    ...(statusFilter ? { status: statusFilter } : {}),
                    limit: '100',
                }).toString(),
                { credentials: 'same-origin' },
            );
            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}`);
            }
            const data = await resp.json();
            setRows(Array.isArray(data.tasks) ? data.tasks : []);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load tasks');
            setRows([]);
        } finally {
            setLoading(false);
        }
    }, [agent, boardFilterLocal, specFilter, statusFilter]);

    useEffect(() => {
        load();
    }, [load]);

    return (
        <div className="space-y-4">
            <div className="flex items-center gap-3">
                <Button variant="ghost" size="sm" onClick={onBack} className="gap-1.5">
                    <ArrowLeft className="size-4" />
                    Back to stats
                </Button>
                <h2 className="text-lg font-semibold">
                    Tasks for <span className="font-mono">{agent}</span>
                </h2>
                <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                    {loading ? 'Loading…' : `${rows.length} task${rows.length === 1 ? '' : 's'}`}
                </span>
            </div>

            <div className="flex flex-wrap items-center gap-3">
                <Select
                    value={boardFilterLocal || '__ANY__'}
                    onValueChange={v => {
                        const params = new URLSearchParams(searchParams);
                        if (v === '__ANY__') params.delete('board');
                        else params.set('board', v);
                        onParamsChange(params);
                    }}
                >
                    <SelectTrigger className="w-[180px] h-8 text-sm">
                        <SelectValue>
                            {boardFilterLocal
                                ? boards.find(b => b.id === boardFilterLocal)?.name ?? 'Board'
                                : 'Any board'}
                        </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="__ANY__">Any board</SelectItem>
                        {boards.map(b => (
                            <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <Select
                    value={specFilter || '__ANY__'}
                    onValueChange={v => {
                        const params = new URLSearchParams(searchParams);
                        if (v === '__ANY__') params.delete('spec');
                        else params.set('spec', v);
                        onParamsChange(params);
                    }}
                >
                    <SelectTrigger className="w-[200px] h-8 text-sm">
                        <SelectValue>
                            {specFilter
                                ? visibleSpecs.find(s => String(s.id) === specFilter)?.title
                                    ?? `Spec ${specFilter}`
                                : 'Any spec'}
                        </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="__ANY__">Any spec</SelectItem>
                        {visibleSpecs.map(s => (
                            <SelectItem key={s.id} value={s.id}>{s.title}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <Select
                    value={statusFilter || '__ANY__'}
                    onValueChange={v => {
                        const params = new URLSearchParams(searchParams);
                        if (v === '__ANY__') params.delete('status');
                        else params.set('status', v);
                        onParamsChange(params);
                    }}
                >
                    <SelectTrigger className="w-[160px] h-8 text-sm">
                        <SelectValue>{statusFilter ? statusFilter : 'Any status'}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="__ANY__">Any status</SelectItem>
                        {['DONE', 'TESTING', 'REVIEW', 'EXECUTING', 'IN_PROGRESS', 'FAILED', 'TODO', 'BACKLOG'].map(s => (
                            <SelectItem key={s} value={s}>{s}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>

            {error && (
                <div className="text-sm text-destructive">{error}</div>
            )}

            {loading ? (
                <div className="text-sm text-muted-foreground py-4">Loading…</div>
            ) : rows.length === 0 ? (
                <div className="rounded-md border border-border px-3 py-6 text-center text-sm text-muted-foreground">
                    No tasks match these filters
                </div>
            ) : (
                <div className="overflow-x-auto rounded-md border border-border">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-border bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
                                <th className="py-2 px-3 text-left font-medium">Task</th>
                                <th className="py-2 px-3 text-left font-medium">Status</th>
                                <th className="py-2 px-3 text-left font-medium">Board</th>
                                <th className="py-2 px-3 text-left font-medium">Spec</th>
                                <th className="py-2 px-3 text-left font-medium">Model</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map(row => (
                                <tr
                                    key={row.task_id}
                                    className="border-b border-border/50 hover:bg-muted/30 cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring"
                                    role="button"
                                    tabIndex={0}
                                    aria-label={`Open task ${row.title}`}
                                    onClick={() => onTaskClick(String(row.task_id))}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter' || e.key === ' ') {
                                            e.preventDefault();
                                            onTaskClick(String(row.task_id));
                                        }
                                    }}
                                >
                                    <td className="py-2 px-3 font-medium">{row.title}</td>
                                    <td className="py-2 px-3 tabular-nums">{row.status}</td>
                                    <td className="py-2 px-3 text-muted-foreground">{row.board_name ?? '—'}</td>
                                    <td className="py-2 px-3 text-muted-foreground">{row.spec_title ?? '—'}</td>
                                    <td className="py-2 px-3 text-muted-foreground tabular-nums">{row.model_name || '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}