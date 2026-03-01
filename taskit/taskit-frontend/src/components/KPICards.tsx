import type { Task, ReflectionReport } from '../types';
import { formatDuration } from '../utils/transformer';
import { formatCost } from '../utils/costEstimation';
import { formatTokens } from '../utils/transformer';
import { Card, CardContent } from '@/components/ui/card';
import { BarChart3, CheckCircle2, Timer, Coins, FileSearch } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

interface KPICardsProps {
    tasks: Task[];
    reflections: ReflectionReport[];
}

export function KPICards({ tasks, reflections }: KPICardsProps) {
    const completed = tasks.filter(t => t.currentStatus === 'DONE');
    const completionRate = tasks.length > 0
        ? Math.round((completed.length / tasks.length) * 100)
        : 0;

    const avgCompletionMs = completed.length > 0
        ? completed.reduce((sum, t) => sum + t.workTimeMs, 0) / completed.length
        : 0;

    const totalCost = tasks.reduce((sum, t) => sum + (t.estimatedCostUsd ?? 0), 0);
    const totalTokens = tasks.reduce((sum, t) => sum + (t.usage?.total_tokens ?? 0), 0);

    const completedReflections = reflections.filter(r => r.status === 'COMPLETED');
    const passCount = completedReflections.filter(r => r.verdict.toUpperCase() === 'PASS').length;
    const passRate = completedReflections.length > 0
        ? Math.round((passCount / completedReflections.length) * 100)
        : 0;

    const cards: { icon: LucideIcon; value: string | number; label: string; sub: string; color: string }[] = [
        {
            icon: BarChart3,
            value: tasks.length,
            label: 'Tasks',
            sub: '',
            color: 'var(--chart-1)',
        },
        {
            icon: CheckCircle2,
            value: `${completionRate}%`,
            label: 'Completion Rate',
            sub: completed.length > 0 ? `${completed.length} of ${tasks.length}` : '',
            color: 'var(--chart-3)',
        },
        {
            icon: Timer,
            value: formatDuration(avgCompletionMs),
            label: 'Avg. Completion Time',
            sub: '',
            color: 'var(--chart-5)',
        },
        {
            icon: Coins,
            value: totalCost > 0 ? formatCost(totalCost) : '—',
            label: 'Execution Cost',
            sub: totalTokens > 0 ? formatTokens(totalTokens) : '',
            color: 'var(--chart-4)',
        },
        {
            icon: FileSearch,
            value: reflections.length,
            label: 'Reflections',
            sub: completedReflections.length > 0 ? `${passRate}% pass rate` : '',
            color: 'var(--chart-2)',
        },
    ];

    return (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4 mb-8">
            {cards.map((card, i) => {
                const Icon = card.icon;
                return (
                    <Card key={i} className="border-border">
                        <CardContent className="p-5">
                            <div className="size-9 rounded-lg flex items-center justify-center mb-3" style={{ background: `color-mix(in srgb, ${card.color}, transparent 85%)` }}>
                                <Icon className="size-4" style={{ color: card.color }} />
                            </div>
                            <div className="text-2xl font-bold tracking-tight leading-none mb-1">
                                {card.value}
                            </div>
                            <div className="text-sm text-muted-foreground">{card.label}</div>
                            {card.sub && <div className="text-xs text-muted-foreground mt-1.5">{card.sub}</div>}
                        </CardContent>
                    </Card>
                );
            })}
        </div>
    );
}
