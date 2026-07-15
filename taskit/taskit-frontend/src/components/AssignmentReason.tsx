import { useMemo } from 'react';
import type { Task, TaskAssignmentReason } from '../types';

interface Props {
    task: Task;
}

const RULE_LABELS: Record<TaskAssignmentReason['rule'], string> = {
    'suggested-agent': 'planner-suggested agent',
    'suggested-model': 'planner-suggested model',
    history: 'history-driven',
    'static-fallback': 'static fallback',
    escalated: 'escalated (history-driven)',
};

function computeTwinConsensus(twins: Task['twins'] | undefined, pickedAgent: string | undefined): TaskAssignmentReason['twin_consensus'] {
    if (!twins || twins.length === 0 || !pickedAgent) return null;
    const matching = twins.filter(t => t.agent === pickedAgent).length;
    if (matching === 0) return null;
    return { agent: pickedAgent, landed: matching, total: twins.length };
}

export function AssignmentReason({ task }: Props) {
    const ar = (task.metadata?.assignment_reason ?? null) as TaskAssignmentReason | null;

    // twin_consensus may already be stamped in the metadata; if not, we
    // derive it from task.twins (W5.5 already exposes this on the
    // detail API). Default First: prefer the metadata value, fall back
    // to derivation, never fabricate.
    const twinConsensus = useMemo(
        () => ar?.twin_consensus ?? computeTwinConsensus(task.twins, ar?.agent),
        [ar?.twin_consensus, task.twins, ar?.agent]
    );

    const label = ar?.override ? 'Override' : 'Auto';
    const ruleLabel = ar ? RULE_LABELS[ar.rule] : null;

    const tooltipLines: string[] = [];
    if (ar?.reason) {
        tooltipLines.push(`Rule: ${ruleLabel ?? ar.rule}`);
        if (ar.override && ar.override_by) {
            tooltipLines.push(`Overridden by: ${ar.override_by}`);
        }
    }
    if (twinConsensus) {
        tooltipLines.push(`${twinConsensus.landed} of ${twinConsensus.total} twins landed with ${twinConsensus.agent}`);
    }
    if (ar && ar.cheaper_alternatives && ar.cheaper_alternatives.length > 0) {
        tooltipLines.push('Cheaper alternatives:');
        for (const alt of ar.cheaper_alternatives) {
            const rate = alt.success_rate != null ? ` (${(alt.success_rate * 100).toFixed(0)}% success)` : '';
            tooltipLines.push(`  ${alt.agent}/${alt.model ?? '?'} — ${alt.reason}${rate}`);
        }
    }

    const tooltipText = tooltipLines.length > 0 ? tooltipLines.join('\n') : null;

    // A WHY line that says "Override —" tells the operator nothing — the
    // badge with no reason is noise. Hide the whole row when there is no
    // reason sentence to read. (WHY is a descriptive field, not a metric
    // card; the never-hide-metrics rule covers tokens/cost/duration.)
    if (!ar?.reason) return null;

    return (
        <div
            data-testid="assignment-reason-row"
            className="flex items-start gap-2 py-1 text-[11px]"
        >
            <span
                data-testid="assignment-reason-rule"
                className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${ar?.override
                        ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                        : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                    }`}
                title={tooltipText ?? undefined}
            >
                {label}
            </span>
            <span className="min-w-0 flex-1 leading-snug text-muted-foreground/90">
                {ar?.reason ?? '\u2014'}
            </span>
            {tooltipText && (
                <span
                    data-testid="assignment-reason-tooltip"
                    className="sr-only"
                >
                    {tooltipText}
                </span>
            )}
        </div>
    );
}