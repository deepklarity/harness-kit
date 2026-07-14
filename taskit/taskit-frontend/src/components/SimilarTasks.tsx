import { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import type { Task, TaskTwin } from '../types';
import { getStatusColor } from '../utils/transformer';

const MAX_TWINS = 3;

function formatTwinTokens(n: number | null | undefined): string {
    if (n === null || n === undefined) return '\u2014';
    const v = Number(n);
    if (!Number.isFinite(v) || v <= 0) return '\u2014';
    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
    if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
    return `${Math.round(v)}`;
}

function formatTwinDuration(ms: number | null | undefined): string {
    if (ms === null || ms === undefined) return '\u2014';
    const v = Number(ms);
    if (!Number.isFinite(v) || v <= 0) return '\u2014';
    const minutes = v / 60_000;
    if (minutes < 1) return `${Math.round(minutes * 60)}s`;
    if (minutes < 60) return `${minutes.toFixed(1)} min`;
    const hours = minutes / 60;
    return `${hours.toFixed(1)} h`;
}

function TwinRow({ twin }: { twin: TaskTwin }) {
    const href = `?taskId=${twin.task_id}`;
    const color = getStatusColor(twin.outcome);
    return (
        <div data-testid={`twin-row-${twin.task_id}`} className="rounded-md border border-border/60 bg-muted/30 px-2 py-1.5 space-y-1">
            <div className="flex items-center gap-1.5">
                <a
                    data-testid={`twin-link-${twin.task_id}`}
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 rounded bg-background px-1.5 py-0.5 font-mono text-[10px] text-primary hover:underline"
                    title={`Open task #${twin.task_id} in a new tab`}
                >
                    #{twin.task_id}
                </a>
                <span
                    className="shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold border"
                    style={{ background: `${color}15`, color, borderColor: `${color}30` }}
                >
                    {twin.outcome}
                </span>
                <span className="ml-auto shrink-0 text-[10px] font-mono text-muted-foreground/80" title="Match score">
                    {twin.score.toFixed(2)}
                </span>
            </div>
            <div className="text-[11px] text-foreground/80 leading-snug line-clamp-2">{twin.title}</div>
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground/70 font-mono">
                <span title="Tokens">Tokens: {formatTwinTokens(twin.tokens)}</span>
                <span title="Duration">Duration: {formatTwinDuration(twin.duration_ms)}</span>
            </div>
            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground/70">
                {(twin.agent || twin.model) ? (
                    <>
                        <span className="truncate" title="Agent + model">{twin.agent || '\u2014'} / {twin.model || '\u2014'}</span>
                    </>
                ) : (
                    <span>{'\u2014'}</span>
                )}
            </div>
        </div>
    );
}

export function SimilarTasks({ task }: { task: Task }) {
    const [isOpen, setIsOpen] = useState(true);
    const twins = (task.twins || []).slice(0, MAX_TWINS);
    const estimate = task.estimate ?? null;
    const actual = task.actual ?? null;

    const quoteParts: string[] = [];
    if (estimate && estimate.twin_count > 0 && estimate.confidence !== 'none') {
        if (estimate.duration_ms_median != null) quoteParts.push(`~${formatTwinDuration(estimate.duration_ms_median)}`);
        if (estimate.tokens_median != null) quoteParts.push(`~${formatTwinTokens(estimate.tokens_median)} tokens`);
    }

    const actualParts: string[] = [];
    if (actual) {
        if (actual.tokens != null) actualParts.push(`~${formatTwinTokens(actual.tokens)} tokens`);
        if (actual.duration_ms != null) actualParts.push(`~${formatTwinDuration(actual.duration_ms)}`);
    }

    return (
        <div data-testid="similar-tasks" className="border-b border-border/30">
            <button
                className="flex items-center gap-1.5 w-full py-1.5 text-[10px] text-muted-foreground/60 uppercase tracking-widest font-bold hover:text-muted-foreground transition-colors"
                onClick={() => setIsOpen(!isOpen)}
            >
                <ChevronRight className={`size-3 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                Similar tasks{twins.length > 0 ? ` (${twins.length})` : ''}
            </button>
            {isOpen && (
                <div className="pb-2 pl-1 space-y-1.5">
                    {twins.length === 0 ? (
                        <div className="text-[11px] text-muted-foreground/60 italic">
                            No similar finished tasks yet
                        </div>
                    ) : (
                        twins.map(twin => <TwinRow key={twin.task_id} twin={twin} />)
                    )}
                    <div data-testid="twin-quote" className="text-[10px] text-muted-foreground/80 px-1">
                        {quoteParts.length > 0
                            ? `Estimate: ${quoteParts.join(', ')} (${estimate!.confidence}, ${estimate!.twin_count} ${estimate!.twin_count === 1 ? 'twin' : 'twins'})`
                            : `Estimate: ${'\u2014'}`}
                    </div>
                    {actual && actualParts.length > 0 && (
                        <div data-testid="twin-actual" className="text-[10px] text-muted-foreground/80 px-1">
                            Actual: {actualParts.join(', ')}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
