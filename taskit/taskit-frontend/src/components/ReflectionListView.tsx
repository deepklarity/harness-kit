import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import type { ReflectionReport } from '../types';
import { useService } from '../contexts/ServiceContext';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sparkles, AlertTriangle, Loader2, Search, ArrowLeft } from 'lucide-react';
import { formatDate } from '../utils/transformer';
import { VERDICT_STYLES } from './reflection/constants';
import { deduplicateSummary } from './reflection/utils';

interface ReflectionListViewProps {
    onTaskClick?: (taskId: string) => void;
    boardName?: string;
}

const STATUS_STYLES: Record<string, string> = {
    PENDING: 'text-blue-400',
    RUNNING: 'text-indigo-400',
    COMPLETED: 'text-emerald-400',
    FAILED: 'text-red-400',
};

export function ReflectionListView({ onTaskClick, boardName }: ReflectionListViewProps) {
    const service = useService();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const boardFilter = searchParams.get('board') || undefined;
    const [reports, setReports] = useState<ReflectionReport[]>([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [statusFilter, setStatusFilter] = useState('ALL');
    const [verdictFilter, setVerdictFilter] = useState('ALL');

    useEffect(() => {
        const load = async () => {
            setLoading(true);
            try {
                const params: { status?: string; verdict?: string; board?: string } = {};
                if (statusFilter !== 'ALL') params.status = statusFilter;
                if (verdictFilter !== 'ALL') params.verdict = verdictFilter;
                if (boardFilter) params.board = boardFilter;
                const data = await service.fetchAllReflections(params);
                setReports(data || []);
            } catch {
                // Silently fail — may not be available
            } finally {
                setLoading(false);
            }
        };
        load();

        // Poll for pending/running
        const interval = setInterval(load, 15000);
        return () => clearInterval(interval);
    }, [service, statusFilter, verdictFilter, boardFilter]);

    const filtered = reports.filter(r => {
        if (!searchTerm) return true;
        const term = searchTerm.toLowerCase();
        return (
            (r.task_title || '').toLowerCase().includes(term) ||
            r.reviewer_agent.toLowerCase().includes(term) ||
            r.reviewer_model.toLowerCase().includes(term)
        );
    });

    if (loading && reports.length === 0) {
        return (
            <div className="flex items-center justify-center py-16">
                <Loader2 className="size-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    return (
        <div>
            {/* Back to Settings + board context */}
            {boardFilter && (
                <div className="flex items-center gap-3 mb-4">
                    <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => navigate(boardFilter ? `/settings?board=${boardFilter}` : '/settings')}>
                        <ArrowLeft className="size-3.5" />
                        Settings
                    </Button>
                    {boardName && (
                        <span className="text-sm text-muted-foreground">
                            Reflections for <span className="font-medium text-foreground">{boardName}</span>
                        </span>
                    )}
                </div>
            )}

            {/* Filter bar */}
            <div className="flex items-center gap-3 mb-6 flex-wrap">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                    <Input
                        placeholder="Search by task title, agent..."
                        value={searchTerm}
                        onChange={e => setSearchTerm(e.target.value)}
                        className="pl-8"
                    />
                </div>
                <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="w-36">
                        <SelectValue placeholder="Status" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="ALL">All Status</SelectItem>
                        <SelectItem value="PENDING">Pending</SelectItem>
                        <SelectItem value="RUNNING">Running</SelectItem>
                        <SelectItem value="COMPLETED">Completed</SelectItem>
                        <SelectItem value="FAILED">Failed</SelectItem>
                    </SelectContent>
                </Select>
                <Select value={verdictFilter} onValueChange={setVerdictFilter}>
                    <SelectTrigger className="w-36">
                        <SelectValue placeholder="Verdict" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="ALL">All Verdicts</SelectItem>
                        <SelectItem value="PASS">Pass</SelectItem>
                        <SelectItem value="NEEDS_WORK">Needs Work</SelectItem>
                        <SelectItem value="FAIL">Fail</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {/* Empty state */}
            {filtered.length === 0 ? (
                <div className="text-center py-16 text-muted-foreground">
                    <Sparkles className="size-12 mx-auto mb-4 opacity-50" />
                    <div className="text-lg font-semibold text-muted-foreground mb-2">No reflections found</div>
                    <p>Reflections are triggered from the task detail view on completed tasks.</p>
                </div>
            ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-5">
                    {filtered.map((report, i) => (
                        <Card
                            key={report.id}
                            className="cursor-pointer bg-card/50 backdrop-blur-sm border-border hover:border-primary/30 hover:shadow-lg hover:-translate-y-0.5 transition-all animate-in-up group"
                            style={{ animationDelay: `${Math.min(i + 1, 6) * 50}ms` }}
                            onClick={() => navigate(boardFilter ? `/reflections/${report.id}?board=${boardFilter}` : `/reflections/${report.id}`)}
                        >
                            <CardContent className="p-5">
                                {/* Header: task title + ID */}
                                <div className="flex items-start justify-between mb-2">
                                    <div className="flex-1 min-w-0">
                                        <div className="text-base font-semibold leading-snug truncate">
                                            {report.task_title || `Task #${report.task}`}
                                        </div>
                                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                            <span className="font-mono opacity-60">R-{report.id}</span>
                                            <span>·</span>
                                            <button
                                                className="hover:text-primary hover:underline transition-colors"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    onTaskClick?.(String(report.task));
                                                }}
                                            >
                                                Task #{report.task} ↗
                                            </button>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-1.5 shrink-0 ml-2">
                                        {/* Verdict badge */}
                                        {report.verdict && (() => {
                                            const style = VERDICT_STYLES[report.verdict] || VERDICT_STYLES.NEEDS_WORK;
                                            return (
                                                <Badge className={`${style.bg} ${style.text} ${style.border} border text-[10px] font-bold px-1.5`}>
                                                    {report.verdict}
                                                </Badge>
                                            );
                                        })()}
                                        {/* Status indicator */}
                                        {(report.status === 'PENDING' || report.status === 'RUNNING') && (
                                            <Loader2 className="size-3.5 animate-spin text-indigo-400" />
                                        )}
                                        {report.status === 'FAILED' && !report.verdict && (
                                            <AlertTriangle className="size-3.5 text-red-400" />
                                        )}
                                    </div>
                                </div>

                                {/* Verdict summary — deduplicated */}
                                {report.verdict_summary && (
                                    <p className="text-xs text-muted-foreground line-clamp-2 mb-3">
                                        {deduplicateSummary(report.verdict_summary)}
                                    </p>
                                )}

                                {/* Status for non-completed */}
                                {report.status !== 'COMPLETED' && (
                                    <div className={`text-xs font-medium mb-3 ${STATUS_STYLES[report.status] || 'text-muted-foreground'}`}>
                                        {report.status === 'PENDING' && 'Waiting to start...'}
                                        {report.status === 'RUNNING' && 'Reflection in progress...'}
                                        {report.status === 'FAILED' && (report.error_message || 'Failed')}
                                    </div>
                                )}

                                {/* Footer: agent/model + timestamp */}
                                <div className="flex items-center gap-2 text-[10px] text-muted-foreground pt-2 border-t border-border/30">
                                    <span className="font-mono">
                                        {report.reviewer_agent}/{report.reviewer_model.split('/').pop()}
                                    </span>
                                    <span className="ml-auto">
                                        {formatDate(report.created_at)}
                                    </span>
                                    {report.duration_ms && (
                                        <span className="font-mono">
                                            {(report.duration_ms / 1000).toFixed(1)}s
                                        </span>
                                    )}
                                </div>
                                {report.requested_by && report.requested_by !== 'unknown@user' && (
                                    <div className="text-[10px] text-muted-foreground/60 mt-1">
                                        by {report.requested_by}
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}
        </div>
    );
}
