import { useState } from 'react';
import type { ReflectionReport } from '../types';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Sparkles, AlertTriangle, Loader2, XCircle, ExternalLink, Trash2, ChevronRight } from 'lucide-react';
import { formatDate } from '../utils/transformer';
import { VERDICT_STYLES } from './reflection/constants';
import { ReportSection } from './reflection/ReportSection';
import { deduplicateSummary, hasTokenUsage } from './reflection/utils';

interface ReflectionReportViewerProps {
    reports: ReflectionReport[];
    onCancel?: (reportId: number) => void;
    onDelete?: (reportId: number) => void;
    onViewDetail?: (reportId: number) => void;
}

export function ReflectionReportViewer({ reports, onCancel, onDelete, onViewDetail }: ReflectionReportViewerProps) {
    const hasActiveReflection = reports.some(r => r.status === 'PENDING' || r.status === 'RUNNING');
    const [isOpen, setIsOpen] = useState(hasActiveReflection);

    if (reports.length === 0) return null;

    // Summarize verdicts for the collapsed header
    const verdictCounts = reports.reduce((acc, r) => {
        if (r.status === 'COMPLETED' && r.verdict) {
            acc[r.verdict] = (acc[r.verdict] || 0) + 1;
        }
        return acc;
    }, {} as Record<string, number>);

    return (
        <div className="space-y-3">
            <button
                className="text-sm font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-2 w-full hover:text-foreground/80 transition-colors"
                onClick={() => setIsOpen(!isOpen)}
            >
                <ChevronRight className={`size-3.5 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                <Sparkles className="size-3.5 text-indigo-400" />
                Reflections ({reports.length})
                {!isOpen && Object.entries(verdictCounts).map(([verdict, count]) => {
                    const style = VERDICT_STYLES[verdict] || VERDICT_STYLES.NEEDS_WORK;
                    return (
                        <Badge key={verdict} className={`${style.bg} ${style.text} ${style.border} border text-[10px] font-bold px-1.5`}>
                            {count > 1 ? `${count}× ` : ''}{verdict}
                        </Badge>
                    );
                })}
                {hasActiveReflection && !isOpen && (
                    <Loader2 className="size-3 animate-spin text-indigo-400" />
                )}
            </button>
            {isOpen && reports.map(report => (
                <ReflectionCard key={report.id} report={report} onCancel={onCancel} onDelete={onDelete} onViewDetail={onViewDetail} />
            ))}
        </div>
    );
}

function ReflectionCard({ report, onCancel, onDelete, onViewDetail }: {
    report: ReflectionReport;
    onCancel?: (reportId: number) => void;
    onDelete?: (reportId: number) => void;
    onViewDetail?: (reportId: number) => void;
}) {
    if (report.status === 'PENDING' || report.status === 'RUNNING') {
        return (
            <div className="rounded-lg border border-indigo-500/20 bg-indigo-500/5 p-4">
                <div className="flex items-center gap-2 text-sm text-indigo-400">
                    <Loader2 className="size-4 animate-spin" />
                    <span className="font-medium">Reflection in progress...</span>
                    <span className="text-xs text-muted-foreground font-mono ml-auto">
                        {report.reviewer_agent}/{report.reviewer_model}
                    </span>
                    {onCancel && (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 px-2 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10"
                            onClick={() => onCancel(report.id)}
                        >
                            <XCircle className="size-3 mr-1" /> Cancel
                        </Button>
                    )}
                </div>
            </div>
        );
    }

    if (report.status === 'FAILED') {
        return (
            <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
                <div className="flex items-center gap-2 text-sm">
                    <AlertTriangle className="size-4 text-red-500" />
                    <span className="font-medium text-red-400">Reflection failed</span>
                    <span className="text-xs text-muted-foreground font-mono ml-auto">
                        {report.reviewer_agent}/{report.reviewer_model}
                    </span>
                    {onViewDetail && (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 px-2 text-xs text-muted-foreground hover:text-primary"
                            onClick={() => onViewDetail(report.id)}
                        >
                            View details <ExternalLink className="size-3 ml-1" />
                        </Button>
                    )}
                    {onDelete && (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 px-2 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10"
                            onClick={() => onDelete(report.id)}
                        >
                            <Trash2 className="size-3" />
                        </Button>
                    )}
                </div>
                {report.error_message && (
                    <p className="text-xs text-red-400/70 mt-2">{report.error_message}</p>
                )}
            </div>
        );
    }

    // COMPLETED
    const verdictStyle = VERDICT_STYLES[report.verdict] || VERDICT_STYLES.NEEDS_WORK;
    const tokenUsage = report.token_usage as Record<string, number> | null;
    const cleanSummary = deduplicateSummary(report.verdict_summary);
    const requestedBy = report.requested_by && report.requested_by !== 'unknown@user'
        ? report.requested_by
        : null;

    return (
        <div className="rounded-lg border border-indigo-500/20 bg-card overflow-hidden">
            {/* Header */}
            <div className="flex items-center gap-2 px-4 py-2.5 bg-indigo-500/5 border-b border-indigo-500/10">
                <Sparkles className="size-3.5 text-indigo-400" />
                <span className="text-sm font-semibold">Reflection Report</span>
                {report.verdict && (
                    <Badge className={`${verdictStyle.bg} ${verdictStyle.text} ${verdictStyle.border} border text-[10px] font-bold px-1.5`}>
                        {report.verdict}
                    </Badge>
                )}
                <div className="flex-1" />
                <span className="text-[10px] font-mono text-muted-foreground">
                    {report.reviewer_model}
                </span>
                <span className="text-[10px] text-muted-foreground">
                    {formatDate(report.created_at)}
                </span>
                {onViewDetail && (
                    <Button
                        variant="ghost"
                        size="sm"
                        className="h-5 px-1.5 text-[10px] text-muted-foreground hover:text-primary"
                        onClick={() => onViewDetail(report.id)}
                    >
                        View details <ExternalLink className="size-2.5 ml-0.5" />
                    </Button>
                )}
                {onDelete && (
                    <Button
                        variant="ghost"
                        size="sm"
                        className="h-5 px-1.5 text-[10px] text-red-400 hover:text-red-300 hover:bg-red-500/10"
                        onClick={() => onDelete(report.id)}
                    >
                        <Trash2 className="size-2.5" />
                    </Button>
                )}
            </div>

            {/* Verdict summary — deduplicated */}
            {cleanSummary && (
                <div className="px-4 py-2 border-b border-indigo-500/10 bg-secondary/20">
                    <p className="text-sm text-foreground/80">
                        {cleanSummary}
                    </p>
                </div>
            )}

            {/* Sections */}
            <div className="divide-y divide-border/30">
                {report.quality_assessment && (
                    <ReportSection title="Quality Assessment" content={report.quality_assessment} />
                )}
                {report.slop_detection && (
                    <ReportSection title="Slop Detection" content={report.slop_detection} />
                )}
                {report.improvements && (
                    <ReportSection title="Actionable Improvements" content={report.improvements} />
                )}
                {report.agent_optimization && (
                    <ReportSection title="Agent Optimization" content={report.agent_optimization} />
                )}
            </div>

            {/* Footer — only show metrics that exist */}
            <div className="px-4 py-2 bg-secondary/30 flex items-center gap-3 text-[10px] text-muted-foreground">
                {report.duration_ms && (
                    <span className="font-mono">{(report.duration_ms / 1000).toFixed(1)}s</span>
                )}
                {hasTokenUsage(tokenUsage) && tokenUsage?.total_tokens && (
                    <span className="font-mono">
                        {tokenUsage.total_tokens.toLocaleString()} tokens
                    </span>
                )}
                {requestedBy && (
                    <span className="ml-auto">by {requestedBy}</span>
                )}
            </div>
        </div>
    );
}
