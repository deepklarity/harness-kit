import { Card, CardContent } from '@/components/ui/card';
import type { ProviderQuota } from '../../types';

interface QuotaCardsProps {
    data: ProviderQuota[];
    loading?: boolean;
}

function formatResetDate(iso: string | null): string | null {
    if (!iso) return null;
    const reset = new Date(iso);
    const now = Date.now();
    const diffMs = reset.getTime() - now;
    if (diffMs <= 0) return 'Expired';
    const hours = Math.floor(diffMs / 3_600_000);
    const mins = Math.floor((diffMs % 3_600_000) / 60_000);
    if (hours > 24) {
        const days = Math.floor(hours / 24);
        return `Resets in ${days}d ${hours % 24}h`;
    }
    return `Resets in ${hours}h ${mins}m`;
}

function barColor(pct: number): string {
    if (pct > 80) return 'var(--destructive)';
    if (pct >= 60) return 'var(--chart-4)';
    return 'var(--chart-3)';
}

function cardBorder(pct: number | null): string {
    if (pct == null) return 'border-border';
    if (pct > 80) return 'border-destructive/60';
    return 'border-border';
}

function usageLabel(q: ProviderQuota): string {
    if (q.used != null && q.limit != null) {
        return `${q.used} / ${q.limit} ${q.unit}`;
    }
    if (q.usage_pct != null) {
        return `${q.usage_pct}% used`;
    }
    return 'No data';
}

function SkeletonCard() {
    return (
        <Card className="border-border">
            <CardContent className="p-5">
                <div className="flex items-center justify-between mb-3">
                    <div className="h-4 w-24 rounded bg-muted animate-pulse" />
                    <div className="h-3 w-12 rounded bg-muted animate-pulse" />
                </div>
                <div className="h-3 w-16 rounded bg-muted animate-pulse mb-2" />
                <div className="h-2 rounded-full bg-muted overflow-hidden mb-2">
                    <div className="h-full w-1/3 rounded-full bg-muted-foreground/10 animate-pulse" />
                </div>
                <div className="flex items-center justify-between">
                    <div className="h-3 w-20 rounded bg-muted animate-pulse" />
                    <div className="h-3 w-8 rounded bg-muted animate-pulse" />
                </div>
            </CardContent>
        </Card>
    );
}

export function QuotaCards({ data, loading }: QuotaCardsProps) {
    if (!loading && !data.length) return null;

    return (
        <div className="space-y-3">
            <h3 className="text-sm font-medium text-muted-foreground">Provider Quotas</h3>
            {loading ? (
                <div className="grid grid-cols-6 gap-4">
                    {Array.from({ length: 6 }, (_, i) => <SkeletonCard key={i} />)}
                </div>
            ) : (
                <div className="grid grid-cols-6 gap-4">
                    {data.map((q) => {
                        const pct = q.usage_pct ?? 0;
                        const color = barColor(pct);
                        return (
                            <Card key={q.provider} className={cardBorder(q.usage_pct)}>
                                <CardContent className="p-5">
                                    <div className="flex items-center justify-between mb-3">
                                        <span className="text-sm font-semibold text-foreground">{q.provider}</span>
                                        {q.state && (
                                            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{q.state}</span>
                                        )}
                                    </div>
                                    {q.plan && (
                                        <div className="text-xs text-muted-foreground mb-2">{q.plan}</div>
                                    )}
                                    {/* Progress bar */}
                                    <div className="h-2 rounded-full bg-muted overflow-hidden mb-2">
                                        <div
                                            className="h-full rounded-full transition-all duration-300"
                                            style={{ width: `${Math.min(pct, 100)}%`, background: color }}
                                        />
                                    </div>
                                    <div className="flex items-center justify-between">
                                        <span className="text-xs text-muted-foreground">{usageLabel(q)}</span>
                                        {q.usage_pct != null && (
                                            <span className="text-xs font-medium" style={{ color }}>{q.usage_pct}%</span>
                                        )}
                                    </div>
                                    {q.reset_date && (
                                        <div className="text-[10px] text-muted-foreground mt-1.5">
                                            {formatResetDate(q.reset_date)}
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
