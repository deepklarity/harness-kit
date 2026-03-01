import { useState, useEffect, useMemo } from 'react';
import type { Spec, SpecComment, Task } from '../types';
import { useService } from '../contexts/ServiceContext';
import { ApiError } from '../services/harness/HarnessTimeService';
import { parseActor } from '../services/harness/HarnessTimeService';
import { formatCost } from '../utils/costEstimation';
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
    AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Separator } from '@/components/ui/separator';
import { getStatusColor, formatDuration, formatTokens, shortModelName } from '../utils/transformer';
import { ArrowLeft, AlertTriangle, FileText, Clock, Code2, FolderOpen, Trash2, Bug, DollarSign, ChevronDown, ChevronRight, Brain, Route, Activity } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { TraceViewer } from './TraceViewer';
import { parseCommentBody } from '../utils/commentParser';


interface SpecDetailViewProps {
    specId: string;
    spec?: Spec;
    onBack: () => void;
    onTaskClick: (taskId: string) => void;
    onDeleteSpec?: (specId: string) => void;
}

export function SpecDetailView({ specId, spec: cachedSpec, onBack, onTaskClick, onDeleteSpec }: SpecDetailViewProps) {
    const service = useService();
    const [searchParams] = useSearchParams();
    const [spec, setSpec] = useState<Spec | null>(cachedSpec || null);
    const [loading, setLoading] = useState(!cachedSpec);
    const [error, setError] = useState<{ notFound: boolean; message: string } | null>(null);
    const [showPlanningTrace, setShowPlanningTrace] = useState(false);
    const [showRoutingConfig, setShowRoutingConfig] = useState(false);
    const [showContent, setShowContent] = useState(false);
    const [showMetadata, setShowMetadata] = useState(false);

    // Sort tasks by id ascending
    const sortedTasks = useMemo(() => {
        const tasks = spec?.tasks ?? [];
        return [...tasks].sort((a, b) => a.idShort - b.idShort);
    }, [spec?.tasks]);

    // Max active time (EXECUTING+REVIEW only) for proportional timeline bars
    const maxActiveTime = useMemo(() =>
        Math.max(...(spec?.tasks ?? []).map(t => {
            const tis = t.timeInStatuses || {};
            return (tis['EXECUTING'] || 0) + (tis['REVIEW'] || 0);
        }), 1),
        [spec?.tasks]
    );

    useEffect(() => {
        if (cachedSpec) {
            setSpec(cachedSpec);
            setLoading(false);
            return;
        }
        setLoading(true);
        setError(null);
        service.fetchSpecDetail(specId)
            .then(setSpec)
            .catch(err => {
                if (err instanceof ApiError && err.isNotFound) {
                    setError({ notFound: true, message: 'Spec not found' });
                } else {
                    setError({ notFound: false, message: err instanceof Error ? err.message : 'Failed to load spec' });
                }
            })
            .finally(() => setLoading(false));
    }, [specId, cachedSpec, service]);

    if (loading) {
        return (
            <div className="text-center py-16 text-muted-foreground">
                <div className="size-10 border-3 border-border border-t-primary rounded-full animate-spin mx-auto mb-4" />
                Loading spec...
            </div>
        );
    }

    if (error) {
        if (error.notFound) {
            return (
                <div className="text-center py-16 text-muted-foreground">
                    <FileText className="size-12 mx-auto mb-4 opacity-50" />
                    <div className="text-lg font-semibold text-muted-foreground mb-2">Spec not found</div>
                    <Button variant="outline" onClick={onBack}>Back to specs</Button>
                </div>
            );
        }
        return (
            <div className="text-center py-16 text-muted-foreground">
                <AlertTriangle className="size-12 mx-auto mb-4 opacity-50 text-destructive" />
                <div className="text-lg font-semibold text-foreground mb-2">Failed to load spec</div>
                <p className="text-sm text-muted-foreground mb-4">{error.message}</p>
                <div className="flex gap-2 justify-center">
                    <Button variant="outline" onClick={onBack}>Back to specs</Button>
                    <Button onClick={() => {
                        setLoading(true);
                        setError(null);
                        service.fetchSpecDetail(specId)
                            .then(setSpec)
                            .catch(err => {
                                if (err instanceof ApiError && err.isNotFound) {
                                    setError({ notFound: true, message: 'Spec not found' });
                                } else {
                                    setError({ notFound: false, message: err instanceof Error ? err.message : 'Failed to load spec' });
                                }
                            })
                            .finally(() => setLoading(false));
                    }}>Retry</Button>
                </div>
            </div>
        );
    }

    if (!spec) return null;

    // Cost data comes from the backend — single source of truth
    const costSummary = spec.costSummary;

    return (
        <div>
            <div className="flex items-center justify-between mb-4">
                <Button variant="ghost" size="sm" className="gap-1.5" onClick={onBack}>
                    <ArrowLeft className="size-3.5" /> Back to specs
                </Button>
                <div className="flex items-center gap-2">
                    <Link to={`/specs/${specId}/debug${searchParams.get('board') ? `?board=${searchParams.get('board')}` : ''}`} className="inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-3 h-8 text-sm font-medium hover:bg-accent hover:text-accent-foreground transition-colors group">
                        <Bug className="size-3.5 group-hover:text-orange-400 transition-colors" /> Debug Execution
                    </Link>
                {onDeleteSpec && (
                    <AlertDialog>
                        <AlertDialogTrigger asChild>
                            <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive hover:bg-destructive/10 gap-1.5">
                                <Trash2 className="size-3.5" /> Delete Spec
                            </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                            <AlertDialogHeader>
                                <AlertDialogTitle>Delete Spec</AlertDialogTitle>
                                <AlertDialogDescription>
                                    Are you sure you want to delete spec "{spec?.title}" and all its associated tasks?
                                    This action cannot be undone.
                                </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction onClick={() => onDeleteSpec(specId)} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
                                    Delete
                                </AlertDialogAction>
                            </AlertDialogFooter>
                        </AlertDialogContent>
                    </AlertDialog>
                )}
                </div>
            </div>

            <Card className="border-border mb-6">
                <CardHeader>
                    <div className="flex items-start justify-between">
                        <div>
                            <div className="flex items-center gap-2 mb-2">
                                <Badge variant="outline" className="font-mono text-xs">#{spec.id}</Badge>
                                {spec.abandoned && (
                                    <Badge variant="destructive" className="gap-1 text-xs">
                                        <AlertTriangle className="size-3" /> Abandoned
                                    </Badge>
                                )}
                            </div>
                            <CardTitle className="text-xl">{spec.title}</CardTitle>
                        </div>
                        <Badge variant="secondary">{spec.taskCount} task{spec.taskCount !== 1 ? 's' : ''}</Badge>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
                        <div className="flex items-center gap-1.5">
                            <Code2 className="size-3.5" /> Source: {spec.source}
                        </div>
                        <div className="flex items-center gap-1.5">
                            <Clock className="size-3.5" /> {new Date(spec.createdAt).toLocaleDateString()}
                        </div>
                        <div className="flex items-center gap-1.5">
                            <FolderOpen className="size-3.5" /> CWD: {spec.cwd || '\u2014'}
                        </div>
                    </div>

                    {spec.content && (
                        <>
                            <Separator />
                            <div
                                className="flex items-center gap-1.5 cursor-pointer select-none"
                                onClick={() => setShowContent(v => !v)}
                            >
                                <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Content</h3>
                                {showContent
                                    ? <ChevronDown className="size-3.5 text-muted-foreground" />
                                    : <ChevronRight className="size-3.5 text-muted-foreground" />
                                }
                            </div>
                            {showContent && (
                                <div className="max-h-[300px] w-full overflow-y-auto rounded-lg border border-border bg-muted/30 p-4 text-sm leading-relaxed text-muted-foreground whitespace-pre-wrap break-words">
                                    {spec.content}
                                </div>
                            )}
                        </>
                    )}

                    {Object.keys(spec.metadata).length > 0 && (
                        <>
                            <Separator />
                            <div
                                className="flex items-center gap-1.5 cursor-pointer select-none"
                                onClick={() => setShowMetadata(v => !v)}
                            >
                                <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Metadata</h3>
                                {showMetadata
                                    ? <ChevronDown className="size-3.5 text-muted-foreground" />
                                    : <ChevronRight className="size-3.5 text-muted-foreground" />
                                }
                            </div>
                            {showMetadata && (
                                <div className="max-h-[200px] w-full overflow-y-auto rounded-lg border border-border bg-muted/30 p-3 text-xs font-mono whitespace-pre-wrap break-words">
                                    {JSON.stringify(spec.metadata, null, 2)}
                                </div>
                            )}
                        </>
                    )}
                </CardContent>
            </Card>

            {/* Cost + Tasks side by side */}
            <div className="flex gap-4 items-start mb-6 max-md:flex-col">
                {/* Cost Breakdown — compact key-value layout */}
                <Card className="border-border w-64 shrink-0">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center gap-1.5">
                            <DollarSign className="size-3.5" /> Cost Breakdown
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-1.5">
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Build</span>
                            <span className="font-mono font-semibold text-emerald-400">
                                {formatCost(costSummary?.total_cost_usd ?? null)}
                            </span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Review</span>
                            <span className="font-mono font-semibold text-violet-400">
                                {formatCost(costSummary?.reflection_cost_usd ?? null)}
                            </span>
                        </div>
                        {costSummary && ((costSummary.total_cost_usd || 0) + (costSummary.reflection_cost_usd || 0)) > 0 && (
                            <div className="flex items-center justify-between text-xs border-t border-border/50 pt-1.5">
                                <span className="text-muted-foreground font-semibold">Total</span>
                                <span className="font-mono font-semibold text-foreground">
                                    {formatCost((costSummary.total_cost_usd || 0) + (costSummary.reflection_cost_usd || 0))}
                                </span>
                            </div>
                        )}
                        <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Tokens</span>
                            <span className="font-mono text-muted-foreground">
                                {costSummary && costSummary.total_input_tokens > 0
                                    ? `${formatTokens(costSummary.total_input_tokens)} in / ${formatTokens(costSummary.total_output_tokens)} out`
                                    : '—'}
                            </span>
                        </div>
                        {(costSummary?.tasks_with_unknown_cost ?? 0) > 0 && (
                            <div className="flex items-center justify-between text-xs">
                                <span className="text-muted-foreground">Unknown</span>
                                <span className="font-mono text-muted-foreground">
                                    {costSummary!.tasks_with_unknown_cost} task{costSummary!.tasks_with_unknown_cost !== 1 ? 's' : ''}
                                </span>
                            </div>
                        )}
                        {costSummary && Object.keys(costSummary.cost_by_model).length > 0 && (
                            <>
                                <Separator className="my-1.5" />
                                {Object.entries(costSummary.cost_by_model)
                                    .sort(([, a], [, b]) => b - a)
                                    .map(([model, cost]) => (
                                        <div key={model} className="flex items-center justify-between text-xs">
                                            <span className="font-mono text-muted-foreground">{shortModelName(model)}</span>
                                            <span className="font-mono text-emerald-400">{formatCost(cost)}</span>
                                        </div>
                                    ))}
                            </>
                        )}
                    </CardContent>
                </Card>

                {/* Tasks table */}
                <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">Tasks</h3>
                    <div className="rounded-lg border border-border overflow-hidden">
                        {/* Header */}
                        <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_5.5rem_4.5rem_minmax(0,6rem)_3.5rem_3.5rem] max-md:grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_5.5rem_4.5rem_3.5rem_3.5rem] gap-x-2 px-3 py-1.5 bg-muted/40 text-[10px] text-muted-foreground uppercase tracking-wider font-semibold items-center">
                            <span>#</span>
                            <span>Task</span>
                            <span>Status</span>
                            <span>Model</span>
                            <span>Time</span>
                            <span className="max-md:hidden">Timeline</span>
                            <span className="text-right">Build</span>
                            <span className="text-right">Review</span>
                        </div>
                        {/* Rows */}
                        {sortedTasks.map(task => (
                            <TaskTableRow
                                key={task.id}
                                task={task}
                                maxActiveTime={maxActiveTime}
                                onClick={() => onTaskClick(task.id)}
                            />
                        ))}
                        {/* Totals */}
                        <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_5.5rem_4.5rem_minmax(0,6rem)_3.5rem_3.5rem] max-md:grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_5.5rem_4.5rem_3.5rem_3.5rem] gap-x-2 px-3 py-1.5 bg-muted/20 border-t border-border text-xs font-semibold items-center">
                            <span />
                            <span className="text-muted-foreground">{sortedTasks.length} tasks</span>
                            <span />
                            <span />
                            <span className="font-mono text-muted-foreground">
                                {formatDuration(sortedTasks.reduce((sum, t) => {
                                    const tis = t.timeInStatuses || {};
                                    return sum + (tis['EXECUTING'] || 0) + (tis['REVIEW'] || 0);
                                }, 0))}
                            </span>
                            <span className="max-md:hidden" />
                            <span className="text-right font-mono text-emerald-400">
                                {formatCost(costSummary?.total_cost_usd ?? null)}
                            </span>
                            <span className="text-right font-mono text-violet-400">
                                {formatCost(costSummary?.reflection_cost_usd ?? null)}
                            </span>
                        </div>
                    </div>
                </div>
            </div>

            {/* Planning Trace */}
            <PlanningTraceSection
                comments={spec.comments || []}
                expanded={showPlanningTrace}
                onToggle={() => setShowPlanningTrace(v => !v)}
            />

            {/* Routing Config — only show models actually used by tasks */}
            {spec.metadata?.model_routing && (() => {
                const usedModels = new Set(sortedTasks.map(t => {
                    const sm = t.metadata?.selected_model as string | undefined;
                    return sm || t.modelName || '';
                }).filter(Boolean));
                const routes = (spec.metadata.model_routing as Array<{ agent: string; model: string }>)
                    .filter(r => usedModels.has(r.model));
                if (routes.length === 0) return null;
                return (
                <Card className="border-border mb-6">
                    <CardHeader className="pb-2 cursor-pointer" onClick={() => setShowRoutingConfig(v => !v)}>
                        <CardTitle className="text-sm flex items-center gap-1.5">
                            <Route className="size-3.5" />
                            Models Used
                            <Badge variant="secondary" className="text-[10px] h-4 px-1.5 ml-1">{routes.length}</Badge>
                            {showRoutingConfig
                                ? <ChevronDown className="size-3.5 ml-auto" />
                                : <ChevronRight className="size-3.5 ml-auto" />
                            }
                        </CardTitle>
                    </CardHeader>
                    {showRoutingConfig && (
                        <CardContent className="space-y-2">
                            <div className="space-y-1">
                                {routes.map((route, i) => {
                                    const tiers = spec.metadata?.agent_tiers as Record<string, string> | undefined;
                                    const tier = tiers?.[route.agent];
                                    const taskCount = sortedTasks.filter(t =>
                                        (t.metadata?.selected_model as string | undefined) === route.model || t.modelName === route.model
                                    ).length;
                                    return (
                                        <div key={i} className="flex items-center gap-2 text-xs font-mono">
                                            <span className="font-medium">{route.agent}</span>
                                            <span className="text-muted-foreground">/</span>
                                            <span className="text-muted-foreground">{route.model}</span>
                                            <span className="text-muted-foreground/50 text-[10px]">{taskCount} task{taskCount !== 1 ? 's' : ''}</span>
                                            {tier && (
                                                <Badge variant="outline" className="text-[9px] px-1 py-0 ml-auto">{tier.toUpperCase()}</Badge>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </CardContent>
                    )}
                </Card>
                );
            })()}
        </div>
    );
}

function TaskTableRow({ task, maxActiveTime, onClick }: { task: Task; maxActiveTime: number; onClick: () => void }) {
    const color = getStatusColor(task.currentStatus);
    const tis = task.timeInStatuses || {};
    const activeTime = (tis['EXECUTING'] || 0) + (tis['REVIEW'] || 0);
    const model = shortModelName(task.metadata?.selected_model as string | undefined);

    // Fixed order: EXECUTING first, then REVIEW
    const SEGMENT_ORDER = ['EXECUTING', 'REVIEW'] as const;
    const activeEntries = SEGMENT_ORDER
        .filter(s => (tis[s] || 0) > 0)
        .map(s => [s, tis[s] || 0] as const);
    const activeBarTotal = activeEntries.reduce((s, [, ms]) => s + ms, 0);
    const barWidth = activeBarTotal > 0 ? (activeBarTotal / maxActiveTime) * 100 : 0;

    const tooltipLines = activeEntries
        .map(([status, ms]) => `${status}: ${formatDuration(ms)}`);

    return (
        <div
            className="grid grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_5.5rem_4.5rem_minmax(0,6rem)_3.5rem_3.5rem] max-md:grid-cols-[2.5rem_minmax(0,1fr)_5.5rem_5.5rem_4.5rem_3.5rem_3.5rem] gap-x-2 px-3 py-1.5 border-t border-border/50 hover:bg-muted/30 cursor-pointer items-center transition-colors"
            onClick={onClick}
        >
            <span className="text-[10px] font-mono text-muted-foreground">#{task.idShort}</span>
            <span className="text-xs font-medium truncate">{task.title || task.name}</span>
            <span className="flex items-center gap-1.5 text-[10px]">
                <span className="size-1.5 rounded-full shrink-0" style={{ background: color }} />
                <span className="truncate">{task.currentStatus}</span>
            </span>
            <span className="text-[10px] font-mono text-muted-foreground truncate">{model}</span>
            <span className="text-[10px] font-mono text-muted-foreground">{formatDuration(activeTime)}</span>
            {/* Timeline bar — execution+review only, hidden on narrow screens */}
            <span className="max-md:hidden flex items-center" title={tooltipLines.join('\n')}>
                {barWidth > 0 ? (
                    <span className="flex h-1.5 rounded-full overflow-hidden" style={{ width: `${barWidth}%`, minWidth: '4px' }}>
                        {activeEntries.map(([status, ms]) => (
                            <span
                                key={status}
                                className="h-full"
                                style={{
                                    width: `${(ms / activeBarTotal) * 100}%`,
                                    backgroundColor: getStatusColor(status),
                                    minWidth: '2px',
                                }}
                            />
                        ))}
                    </span>
                ) : (
                    <span className="text-[10px] text-muted-foreground">—</span>
                )}
            </span>
            <span className="text-[10px] font-mono text-emerald-400 text-right">
                {formatCost(task.estimatedCostUsd ?? null)}
            </span>
            <span className="text-[10px] font-mono text-violet-400 text-right">
                {formatCost(task.reflectionCostUsd ?? null)}
            </span>
        </div>
    );
}

function PlanningTraceSection({
    comments,
    expanded,
    onToggle,
}: {
    comments: SpecComment[];
    expanded: boolean;
    onToggle: () => void;
}) {
    const planningComments = comments.filter(c => c.commentType === 'planning');

    return (
        <Card className="border-border mb-6">
            <CardHeader className="pb-2 cursor-pointer" onClick={onToggle}>
                <CardTitle className="text-sm flex items-center gap-1.5">
                    <Brain className="size-3.5" />
                    Planning Trace
                    <Badge variant="secondary" className="text-[10px] h-4 px-1.5 ml-1">
                        {planningComments.length}
                    </Badge>
                    {expanded
                        ? <ChevronDown className="size-3.5 ml-auto" />
                        : <ChevronRight className="size-3.5 ml-auto" />
                    }
                </CardTitle>
            </CardHeader>
            {expanded && (
                <CardContent className="space-y-3">
                    {planningComments.length === 0 ? (
                        <div className="text-xs text-muted-foreground italic">
                            No planning trace available.
                        </div>
                    ) : (
                        planningComments.map(comment => (
                            <PlanningCommentItem key={comment.id} comment={comment} />
                        ))
                    )}
                </CardContent>
            )}
        </Card>
    );
}

function PlanningCommentItem({ comment }: { comment: SpecComment }) {
    const actor = parseActor(comment.authorEmail);
    const [showFullTrace, setShowFullTrace] = useState(false);

    // Check if this comment has trace data (either via attachments or embedded JSONL)
    const hasTraceAttachment = comment.attachments?.some(a => a === 'trace:execution_jsonl');

    // Split content into metrics line and body, then separate trace from summary
    const lines = comment.content.split('\n');
    const firstLine = lines[0] || '';
    const hasMetrics = firstLine.startsWith('Completed in ') || firstLine.startsWith('Failed in ');
    const metrics = hasMetrics ? firstLine : null;
    const rawBody = hasMetrics ? lines.slice(1).join('\n').trim() : comment.content;
    const isFailed = firstLine.startsWith('Failed');

    // Always parse planning comment bodies — trace JSONL can be embedded directly
    // in the content without the attachment tag
    const { summary, traceData } = useMemo(
        () => parseCommentBody(rawBody),
        [rawBody]
    );

    return (
        <div className={`rounded-lg border p-3 ${isFailed ? 'border-red-500/20 bg-red-500/5' : 'border-violet-500/20 bg-violet-500/5'}`}>
            <div className="flex items-baseline justify-between mb-1">
                <div className="flex items-center gap-1.5">
                    <Brain className="size-3.5 text-violet-400" />
                    <span className="text-xs font-medium">{actor.display}</span>
                    <Badge variant="outline" className="text-[9px] px-1 py-0">planning</Badge>
                    {(hasTraceAttachment || traceData) && (
                        <Badge variant="outline" className="text-[9px] px-1 py-0 text-violet-400 border-violet-400/30">
                            <Activity className="size-2.5 mr-0.5" /> trace
                        </Badge>
                    )}
                </div>
                <span className="text-[10px] text-muted-foreground">
                    {new Date(comment.createdAt).toLocaleString()}
                </span>
            </div>
            {metrics && (
                <div className={`text-xs font-mono mb-1 ${isFailed ? 'text-red-400' : 'text-muted-foreground'}`}>
                    {metrics}
                </div>
            )}
            {summary && (
                <>
                    <div className="text-xs whitespace-pre-wrap break-words max-h-[100px] overflow-hidden">
                        {summary.substring(0, 500)}
                        {summary.length > 500 && '...'}
                    </div>
                    {summary.length > 500 && !traceData && (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="text-[10px] h-5 px-1.5 mt-1"
                            onClick={() => setShowFullTrace(v => !v)}
                        >
                            {showFullTrace ? 'Collapse' : 'Show full text'}
                        </Button>
                    )}
                    {showFullTrace && !traceData && (
                        <div className="mt-2 max-h-[400px] overflow-y-auto rounded border border-border bg-muted/30 p-2">
                            <pre className="text-[11px] font-mono whitespace-pre-wrap break-words">
                                {summary}
                            </pre>
                        </div>
                    )}
                </>
            )}
            {traceData && (
                <div className="mt-2">
                    <Button
                        variant="ghost"
                        size="sm"
                        className="text-[10px] h-5 px-1.5 mb-1 gap-1"
                        onClick={() => setShowFullTrace(v => !v)}
                    >
                        <Activity className="size-3" />
                        {showFullTrace ? 'Hide execution trace' : 'Show execution trace'}
                    </Button>
                    {showFullTrace && <TraceViewer traceText={traceData} />}
                </div>
            )}
        </div>
    );
}
