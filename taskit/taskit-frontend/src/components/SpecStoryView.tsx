import { useState, useEffect } from 'react';
import type { SpecStory, SpecStoryTask } from '../types';
import { useService } from '../contexts/ServiceContext';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { formatCost } from '../utils/costEstimation';
import { formatDuration, formatTokens, shortModelName, getStatusColor } from '../utils/transformer';
import { ChevronDown, ChevronRight, AlertTriangle, RotateCcw, GitMerge, MessageSquare } from 'lucide-react';

interface SpecStoryViewProps {
    specId: string;
    onTaskClick: (taskId: string) => void;
}

export function SpecStoryView({ specId, onTaskClick }: SpecStoryViewProps) {
    const service = useService();
    const [story, setStory] = useState<SpecStory | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!service.getSpecStory) {
            setError('Story view is not supported by this data source.');
            setLoading(false);
            return;
        }
        setLoading(true);
        setError(null);
        service.getSpecStory(specId)
            .then(setStory)
            .catch(err => setError(err instanceof Error ? err.message : 'Failed to load story'))
            .finally(() => setLoading(false));
    }, [specId, service]);

    if (loading) {
        return (
            <div className="text-center py-16 text-muted-foreground">
                <div className="size-10 border-3 border-border border-t-primary rounded-full animate-spin mx-auto mb-4" />
                Loading story...
            </div>
        );
    }

    if (error) {
        return (
            <div className="text-center py-16 text-muted-foreground">
                <AlertTriangle className="size-12 mx-auto mb-4 opacity-50 text-destructive" />
                <div className="text-sm">{error}</div>
            </div>
        );
    }

    if (!story || story.tasks.length === 0) {
        return (
            <div className="text-center py-16 text-muted-foreground text-sm">
                No tasks yet — the story will fill in once tasks are dispatched.
            </div>
        );
    }

    return (
        <div className="space-y-2 mb-6" data-testid="spec-story-view">
            {story.tasks.map(task => (
                <StoryTaskRow key={task.task_id} task={task} onClick={() => onTaskClick(String(task.task_id))} />
            ))}
        </div>
    );
}

function StoryTaskRow({ task, onClick }: { task: SpecStoryTask; onClick: () => void }) {
    const [expanded, setExpanded] = useState(false);
    const color = getStatusColor(task.status);

    return (
        <Card className="border-border">
            <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/30 transition-colors"
                onClick={() => setExpanded(v => !v)}
                role="button"
                aria-expanded={expanded}
            >
                {expanded
                    ? <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
                    : <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />}
                <Badge variant="outline" className="font-mono text-[10px] shrink-0">#{task.task_id}</Badge>
                <span className="text-xs font-medium shrink-0 flex items-center gap-1">
                    <span className="size-1.5 rounded-full" style={{ background: color }} />
                    {task.status}
                </span>
                <button
                    type="button"
                    className="text-sm font-medium truncate flex-1 text-left hover:underline"
                    onClick={(e) => { e.stopPropagation(); onClick(); }}
                >
                    {task.title}
                </button>
                <span className="text-xs text-muted-foreground shrink-0 hidden sm:inline">{task.agent || '—'}</span>
                <span className="text-xs font-mono text-muted-foreground shrink-0 hidden md:inline">{shortModelName(task.model || undefined)}</span>
                <span className="text-xs font-mono text-muted-foreground shrink-0">{task.duration_ms ? formatDuration(task.duration_ms) : '—'}</span>
                <span className="text-xs font-mono text-muted-foreground shrink-0 hidden sm:inline">{formatTokens(task.tokens.total)}</span>
                <span className="text-xs font-mono text-emerald-400 shrink-0">{formatCost(task.cost_usd)}</span>
                {task.redo_rounds.count > 0 && (
                    <Badge variant="secondary" className="text-[10px] h-4 px-1.5 gap-1 shrink-0">
                        <RotateCcw className="size-2.5" /> {task.redo_rounds.count}
                    </Badge>
                )}
                {task.merge && (
                    <Badge variant="outline" className="text-[10px] h-4 px-1.5 gap-1 shrink-0">
                        <GitMerge className="size-2.5" /> {task.merge.mode}
                    </Badge>
                )}
                {task.gaps.length > 0 && (
                    <AlertTriangle className="size-3.5 text-amber-400 shrink-0" aria-label="gaps in this task's data" />
                )}
            </div>
            {expanded && (
                <CardContent className="border-t border-border/50 pt-3 space-y-2 text-xs">
                    {task.latest_comment ? (
                        <div className="flex items-start gap-2">
                            <MessageSquare className="size-3.5 mt-0.5 shrink-0 text-muted-foreground" />
                            <div>
                                <span className="text-muted-foreground">{task.latest_comment.author} · {task.latest_comment.comment_type}</span>
                                <div className="text-foreground">{task.latest_comment.headline}</div>
                            </div>
                        </div>
                    ) : (
                        <div className="text-muted-foreground">No comments on this task.</div>
                    )}
                    {task.redo_rounds.verdicts.length > 0 && (
                        <div>
                            <div className="text-muted-foreground mb-1">Reflection verdicts</div>
                            <div className="space-y-0.5">
                                {task.redo_rounds.verdicts.map(v => (
                                    <div key={v.id} className="font-mono flex items-center gap-2">
                                        <Badge variant={v.verdict === 'PASS' ? 'secondary' : 'outline'} className="text-[10px] h-4 px-1.5">
                                            {v.verdict || 'pending'}
                                        </Badge>
                                        <span className="text-muted-foreground">{v.reviewer_agent} / {shortModelName(v.reviewer_model)}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                    {task.merge && (
                        <div>
                            <div className="text-muted-foreground mb-1">Merge</div>
                            <div>{task.merge.mode}</div>
                            {task.merge.conflicting_files.length > 0 && (
                                <div className="text-muted-foreground">
                                    Conflicting: {task.merge.conflicting_files.join(', ')}
                                </div>
                            )}
                            {task.merge.error && (
                                <div className="text-destructive">{task.merge.error}</div>
                            )}
                        </div>
                    )}
                    {task.dispatched_at && (
                        <div className="text-muted-foreground">
                            Dispatched: {new Date(task.dispatched_at).toLocaleString()}
                        </div>
                    )}
                    {task.gaps.length > 0 && (
                        <div className="space-y-0.5">
                            {task.gaps.map((g, i) => (
                                <div key={i} className="flex items-start gap-1.5 text-amber-400/90">
                                    <AlertTriangle className="size-3 mt-0.5 shrink-0" />
                                    <span>{g}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            )}
        </Card>
    );
}
