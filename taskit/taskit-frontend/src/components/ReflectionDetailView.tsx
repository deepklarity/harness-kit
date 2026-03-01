import { useState, useEffect } from 'react';
import type { ReflectionReport, Task } from '../types';
import { useService } from '../contexts/ServiceContext';
import { useToast } from '@/hooks/use-toast';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import {
    ArrowLeft, Sparkles, AlertTriangle, Loader2, XCircle, Trash2,
    Clock, Cpu, User, FileInput, Bot, Zap, DollarSign, Code,
} from 'lucide-react';
import { formatCost } from '../utils/costEstimation';
import { VERDICT_STYLES } from './reflection/constants';
import { ReportSection } from './reflection/ReportSection';
import { TraceViewer } from './TraceViewer';
import { deduplicateSummary } from './reflection/utils';

interface ReflectionDetailViewProps {
    reportId: string;
    onBack: () => void;
    onTaskClick?: (taskId: string) => void;
}

export function ReflectionDetailView({ reportId, onBack, onTaskClick }: ReflectionDetailViewProps) {
    const service = useService();
    const { toast } = useToast();
    const [report, setReport] = useState<ReflectionReport | null>(null);
    const [taskContext, setTaskContext] = useState<Task | null>(null);
    const [loading, setLoading] = useState(true);
    const [cancelling, setCancelling] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [showTrace, setShowTrace] = useState(false);

    useEffect(() => {
        const load = async () => {
            try {
                const found = await service.fetchReflectionById(Number(reportId));
                setReport(found || null);
                if (found?.task) {
                    try {
                        const task = await service.fetchTaskDetail(String(found.task));
                        setTaskContext(task);
                    } catch {
                        // Task fetch is optional — don't block the view
                    }
                }
            } catch {
                // ignore
            } finally {
                setLoading(false);
            }
        };
        load();

        // Poll if pending/running
        const interval = setInterval(() => {
            if (report && (report.status === 'PENDING' || report.status === 'RUNNING')) {
                load();
            }
        }, 10000);
        return () => clearInterval(interval);
    }, [reportId, service, report?.status]);

    const handleCancel = async () => {
        if (!report) return;
        setCancelling(true);
        try {
            const updated = await service.cancelReflection(report.id);
            setReport(updated);
            toast({ title: 'Cancelled', description: 'Reflection has been cancelled.' });
        } catch {
            toast({ title: 'Error', description: 'Failed to cancel reflection', variant: 'destructive' });
        } finally {
            setCancelling(false);
        }
    };

    const handleDelete = async () => {
        if (!report) return;
        setDeleting(true);
        try {
            await service.deleteReflection(report.id);
            toast({ title: 'Deleted', description: 'Reflection has been deleted.' });
            onBack();
        } catch {
            toast({ title: 'Error', description: 'Failed to delete reflection', variant: 'destructive' });
        } finally {
            setDeleting(false);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center py-16">
                <Loader2 className="size-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (!report) {
        return (
            <div className="text-center py-16">
                <AlertTriangle className="size-12 mx-auto mb-4 text-muted-foreground/50" />
                <div className="text-lg font-semibold text-muted-foreground mb-2">Reflection not found</div>
                <Button variant="outline" onClick={onBack}>
                    <ArrowLeft className="size-4 mr-2" /> Back to Reflections
                </Button>
            </div>
        );
    }

    const verdictStyle = report.verdict ? (VERDICT_STYLES[report.verdict] || VERDICT_STYLES.NEEDS_WORK) : null;
    const canCancel = report.status === 'PENDING' || report.status === 'RUNNING';
    const tokenUsage = report.token_usage as Record<string, number> | null;
    const totalTokens = tokenUsage?.total_tokens;
    const inputTokens = tokenUsage?.input_tokens;
    const outputTokens = tokenUsage?.output_tokens;
    // Reviewer cost from backend
    const reviewerCost = report.estimated_cost_usd ?? null;

    // Original task context
    const taskMeta = taskContext?.metadata as Record<string, unknown> | undefined;
    const taskUsage = (taskContext?.usage as Record<string, number>) || (taskMeta?.last_usage as Record<string, number>) || null;
    const taskModel = taskContext?.modelName || (taskMeta?.selected_model as string) || null;
    const taskDuration = (taskMeta?.last_duration_ms as number) || null;
    const taskCost = taskContext?.estimatedCostUsd ?? null;

    // Deduplicated verdict summary
    const cleanSummary = deduplicateSummary(report.verdict_summary);

    // Determine if requested_by is meaningful
    const requestedBy = report.requested_by && report.requested_by !== 'unknown@user'
        ? report.requested_by
        : null;

    // Parse [CTX:xxx] markers from assembled prompt into per-section content
    const contextSections = parseContextSections(report.assembled_prompt);
    const hasContextSections = contextSections.length > 0;

    // Input context: do we have anything meaningful to show?
    const hasAssembledPrompt = !!report.assembled_prompt;
    const hasTaskDescription = !!taskContext?.description;
    const hasTaskComments = !!(taskContext?.comments && taskContext.comments.length > 0);
    const hasAnyInputContext = hasAssembledPrompt || hasTaskDescription || hasTaskComments;

    return (
        <div>
            {/* Navigation + actions */}
            <div className="flex items-center gap-3 mb-6">
                <Button variant="ghost" size="sm" onClick={onBack} className="gap-1.5">
                    <ArrowLeft className="size-4" /> Reflections
                </Button>
                <div className="flex-1" />
                {canCancel && (
                    <Button
                        variant="destructive"
                        size="sm"
                        onClick={handleCancel}
                        disabled={cancelling}
                        className="gap-1.5"
                    >
                        {cancelling ? <Loader2 className="size-3.5 animate-spin" /> : <XCircle className="size-3.5" />}
                        Cancel Reflection
                    </Button>
                )}
                <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleDelete}
                    disabled={deleting}
                    className="gap-1.5 text-red-400 hover:text-red-300 hover:bg-red-500/10"
                >
                    {deleting ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
                    Delete
                </Button>
            </div>

            {/* Report Header Card */}
            <Card className="mb-6 border-indigo-500/20">
                <CardContent className="p-6">
                    <div className="flex items-start gap-3">
                        <Sparkles className="size-5 text-indigo-400 mt-1 shrink-0" />
                        <div className="flex-1 min-w-0">
                            <div className="text-lg font-semibold leading-snug mb-1">
                                Reflection on{' '}
                                <button
                                    className="text-primary hover:underline"
                                    onClick={() => onTaskClick?.(String(report.task))}
                                >
                                    Task #{report.task}
                                </button>
                                {report.task_title && `: "${report.task_title}"`}
                            </div>
                            <div className="flex items-center gap-2 flex-wrap mt-2">
                                {verdictStyle && (
                                    <Badge className={`${verdictStyle.bg} ${verdictStyle.text} ${verdictStyle.border} border font-bold`}>
                                        {report.verdict}
                                    </Badge>
                                )}
                                <Badge variant="outline" className="text-xs">
                                    {report.status}
                                </Badge>
                                <span className="text-xs font-mono text-muted-foreground">
                                    {report.reviewer_agent}/{report.reviewer_model}
                                </span>
                            </div>
                            {cleanSummary && (
                                <p className="text-sm mt-3 text-foreground/80">
                                    {cleanSummary}
                                </p>
                            )}
                        </div>
                    </div>

                    <Separator className="my-4" />

                    {/* Two-row metrics: Reviewer + Original Task */}
                    <div className="space-y-3">
                        {/* Reviewer metrics */}
                        <div>
                            <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
                                <Sparkles className="size-3" /> Reviewer
                            </div>
                            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                                <MetricCard
                                    icon={<Bot className="size-3" />}
                                    label="Model"
                                    value={report.reviewer_model.split('/').pop() || report.reviewer_model}
                                />
                                <MetricCard
                                    icon={<Clock className="size-3" />}
                                    label="Duration"
                                    value={report.duration_ms ? `${(report.duration_ms / 1000).toFixed(1)}s` : '—'}
                                />
                                <MetricCard
                                    icon={<Cpu className="size-3" />}
                                    label="Tokens"
                                    value={totalTokens ? totalTokens.toLocaleString() : '—'}
                                    detail={inputTokens && outputTokens ? `${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out` : undefined}
                                />
                                <MetricCard
                                    icon={<DollarSign className="size-3" />}
                                    label="Est. Cost"
                                    value={reviewerCost != null ? formatCost(reviewerCost) : '—'}
                                />
                                <MetricCard
                                    icon={<User className="size-3" />}
                                    label="Requested by"
                                    value={requestedBy ? requestedBy.replace(/@.*/, '') : '—'}
                                    detail={requestedBy || undefined}
                                />
                            </div>
                        </div>

                        {/* Original task metrics — always show, use placeholders when loading */}
                        <div>
                            <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
                                <Zap className="size-3" /> Original Task
                            </div>
                            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                                <MetricCard
                                    icon={<Bot className="size-3" />}
                                    label="Agent / Model"
                                    value={taskModel ? (taskModel.split('/').pop() || taskContext?.assignees?.[0] || '—') : '—'}
                                    detail={taskModel || undefined}
                                />
                                <MetricCard
                                    icon={<Clock className="size-3" />}
                                    label="Duration"
                                    value={taskDuration ? `${(taskDuration / 1000).toFixed(1)}s` : '—'}
                                />
                                <MetricCard
                                    icon={<Cpu className="size-3" />}
                                    label="Tokens"
                                    value={taskUsage?.total_tokens ? taskUsage.total_tokens.toLocaleString() : '—'}
                                    detail={taskUsage?.input_tokens && taskUsage?.output_tokens
                                        ? `${taskUsage.input_tokens.toLocaleString()} in / ${taskUsage.output_tokens.toLocaleString()} out`
                                        : undefined}
                                />
                                <MetricCard
                                    icon={<DollarSign className="size-3" />}
                                    label="Est. Cost"
                                    value={taskCost != null ? formatCost(taskCost) : '—'}
                                />
                                <MetricCard
                                    icon={<FileInput className="size-3" />}
                                    label="Status"
                                    value={taskContext?.currentStatus || '—'}
                                />
                            </div>
                        </div>
                    </div>

                    {/* Error message */}
                    {report.error_message && (
                        <div className="mt-3 p-3 rounded-lg bg-red-500/10 border border-red-500/20">
                            <p className="text-sm text-red-400">{report.error_message}</p>
                        </div>
                    )}

                    {/* In-progress indicator */}
                    {(report.status === 'PENDING' || report.status === 'RUNNING') && (
                        <div className="mt-3 p-3 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center gap-2">
                            <Loader2 className="size-4 animate-spin text-indigo-400" />
                            <span className="text-sm text-indigo-400">
                                {report.status === 'PENDING' ? 'Waiting to start...' : 'Reflection in progress...'}
                            </span>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Report Sections — always show all 4 on completed reports */}
            {report.status === 'COMPLETED' && (
                <Card className="mb-6">
                    <CardContent className="p-0 divide-y divide-border/30">
                        <ReportSection
                            title="Quality Assessment"
                            content={report.quality_assessment || '_No assessment provided by reviewer._'}
                        />
                        <ReportSection
                            title="Slop Detection"
                            content={report.slop_detection || '_No slop issues detected._'}
                        />
                        <ReportSection
                            title="Actionable Improvements"
                            content={report.improvements || '_No improvements suggested._'}
                        />
                        <ReportSection
                            title="Agent Optimization"
                            content={report.agent_optimization || '_No optimization notes._'}
                        />
                    </CardContent>
                </Card>
            )}

            {/* Execution Trace — structured view of JSONL from the reviewer harness */}
            {report.execution_trace && (
                <Card className="mb-6">
                    <CardContent className="p-0">
                        <button
                            className="flex items-center gap-1.5 w-full px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider hover:bg-secondary/30 transition-colors"
                            onClick={() => setShowTrace(!showTrace)}
                        >
                            <Code className="size-3" />
                            {showTrace ? 'Hide' : 'Show'} Execution Trace
                        </button>
                        {showTrace && (
                            <div className="px-3 pb-3">
                                <TraceViewer traceText={report.execution_trace} />
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Input Context — what the reviewer saw */}
            {hasAnyInputContext && (
                <Card>
                    <CardContent className="p-0 divide-y divide-border/30">
                        <div className="px-4 py-3 bg-secondary/20">
                            <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                                <FileInput className="size-3.5" />
                                Input Context
                            </div>
                        </div>

                        {hasContextSections ? (
                            <>
                                {contextSections.map(({ key, title, content }) => (
                                    <ReportSection
                                        key={key}
                                        title={title}
                                        content={content}
                                        defaultOpen={false}
                                        mono
                                    />
                                ))}
                            </>
                        ) : hasAssembledPrompt ? (
                            <ReportSection
                                title="Assembled Prompt"
                                content={report.assembled_prompt}
                                defaultOpen={false}
                                mono
                            />
                        ) : (
                            <>
                                {hasTaskDescription && (
                                    <ReportSection
                                        title="Task Description (input)"
                                        content={taskContext!.description!}
                                        defaultOpen={false}
                                    />
                                )}
                                {hasTaskComments && (
                                    <ReportSection
                                        title={`Comments (${taskContext!.comments.length} fed to reviewer)`}
                                        content={taskContext!.comments.map(c =>
                                            `**[${c.commentType || 'comment'}]** ${c.content || ''}`
                                        ).join('\n\n---\n\n')}
                                        defaultOpen={false}
                                    />
                                )}
                            </>
                        )}

                        {/* Context selections — only show when CTX sections aren't parsed (legacy reports) */}
                        {!hasContextSections && report.context_selections && report.context_selections.length > 0 && (
                            <div className="px-4 py-2.5">
                                <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1.5">
                                    Context included
                                </div>
                                <div className="flex flex-wrap gap-1">
                                    {report.context_selections.map(sel => (
                                        <Badge key={sel} variant="outline" className="text-[10px] font-mono">
                                            {sel}
                                        </Badge>
                                    ))}
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Fallback when no input context at all */}
            {!hasAnyInputContext && report.status === 'COMPLETED' && (
                <Card>
                    <CardContent className="p-4">
                        <p className="text-sm text-muted-foreground/60">
                            Input context not captured for this reflection.
                        </p>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}

function MetricCard({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail?: string }) {
    return (
        <div className="rounded-lg border border-border/40 bg-secondary/20 px-3 py-2">
            <div className="flex items-center gap-1.5 text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-0.5">
                {icon} {label}
            </div>
            <div className="text-sm font-semibold text-foreground/90 truncate">{value}</div>
            {detail && <div className="text-[10px] text-muted-foreground truncate">{detail}</div>}
        </div>
    );
}

/** Friendly labels for [CTX:xxx] marker keys. */
const CTX_LABELS: Record<string, string> = {
    description: 'Task Description',
    execution_result: 'Execution Output',
    comments: 'Comments & Proof',
    dependencies: 'Dependent Tasks',
    metadata: 'Task Metadata',
};

interface ContextSection {
    key: string;
    title: string;
    content: string;
}

/**
 * Parse [CTX:xxx] markers from an assembled prompt into individual sections.
 * Splits on `## [CTX:` boundaries to correctly capture multi-line content.
 * Returns an empty array if no markers are found (backwards-compatible).
 */
function parseContextSections(prompt: string | undefined | null): ContextSection[] {
    if (!prompt) return [];
    const parts = prompt.split(/^## \[CTX:/gm);
    const sections: ContextSection[] = [];
    for (const part of parts) {
        const headerMatch = part.match(/^(\w+)\]\s+(.+)\n([\s\S]*)/);
        if (!headerMatch) continue;
        const key = headerMatch[1];
        const title = CTX_LABELS[key] || headerMatch[2].trim();
        // Content runs until the next ## header or end of part
        let content = headerMatch[3];
        const nextHeader = content.indexOf('\n## ');
        if (nextHeader >= 0) {
            content = content.substring(0, nextHeader);
        }
        content = content.trim();
        if (content) {
            sections.push({ key, title, content });
        }
    }
    return sections;
}
