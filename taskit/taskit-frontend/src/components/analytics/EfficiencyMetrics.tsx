import { Card, CardContent } from '@/components/ui/card';
import { Zap, AlertTriangle, Coins, FileSearch, Hash, Timer } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { formatCost } from '../../utils/costEstimation';
import { formatDuration, formatTokens } from '../../utils/transformer';
import type { AnalyticsEfficiencyMetrics } from '../../types';

interface EfficiencyMetricsProps {
    data: AnalyticsEfficiencyMetrics;
}

export function EfficiencyMetrics({ data }: EfficiencyMetricsProps) {
    const cards: { icon: LucideIcon; value: string; label: string; sub: string; color: string }[] = [
        {
            icon: Zap,
            value: `${data.cache_hit_rate}%`,
            label: 'Cache Hit Rate',
            sub: '',
            color: 'var(--chart-3)',
        },
        {
            icon: AlertTriangle,
            value: formatCost(data.failure_cost),
            label: 'Failure Cost',
            sub: data.failed_task_count > 0 ? `${data.failed_task_count} of ${data.total_task_count} tasks` : '',
            color: 'var(--chart-1)',
        },
        {
            icon: Coins,
            value: formatCost(data.avg_cost_per_task),
            label: 'Avg Cost/Task',
            sub: '',
            color: 'var(--chart-4)',
        },
        {
            icon: FileSearch,
            value: formatCost(data.reflection_cost),
            label: 'Reflection Cost',
            sub: '',
            color: 'var(--chart-2)',
        },
        {
            icon: Hash,
            value: formatTokens(data.avg_tokens_per_task),
            label: 'Avg Tokens/Task',
            sub: '',
            color: 'var(--chart-5)',
        },
        {
            icon: Timer,
            value: formatDuration(data.avg_duration_ms),
            label: 'Avg Duration',
            sub: '',
            color: 'var(--chart-3)',
        },
    ];

    return (
        <Card className="border-border">
            <CardContent className="p-5">
                <div className="text-sm font-medium text-foreground mb-4">Efficiency Metrics</div>
                <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-4">
                    {cards.map((card, i) => {
                        const Icon = card.icon;
                        return (
                            <div key={i}>
                                <div className="size-8 rounded-lg flex items-center justify-center mb-2" style={{ background: `color-mix(in srgb, ${card.color}, transparent 85%)` }}>
                                    <Icon className="size-3.5" style={{ color: card.color }} />
                                </div>
                                <div className="text-lg font-bold tracking-tight leading-none mb-0.5">
                                    {card.value}
                                </div>
                                <div className="text-xs text-muted-foreground">{card.label}</div>
                                {card.sub && <div className="text-[10px] text-muted-foreground mt-1">{card.sub}</div>}
                            </div>
                        );
                    })}
                </div>
            </CardContent>
        </Card>
    );
}
