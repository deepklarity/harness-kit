import type { AnalyticsThroughputFunnel } from '../../types';

interface ThroughputFunnelProps {
    data: AnalyticsThroughputFunnel;
}

interface BucketVisual {
    key: 'pass' | 'rework' | 'fail' | 'in_flight';
    label: string;
    description: string;
    accent: string;
    bg: string;
}

const VISUAL: Record<BucketVisual['key'], BucketVisual> = {
    pass: {
        key: 'pass',
        label: 'Pass',
        description: 'DONE without rework',
        accent: 'text-green-700 dark:text-green-400',
        bg: 'bg-green-600/80 dark:bg-green-500/80',
    },
    rework: {
        key: 'rework',
        label: 'Rework',
        description: 'DONE after at least one redo',
        accent: 'text-amber-700 dark:text-amber-400',
        bg: 'bg-amber-500/80 dark:bg-amber-400/80',
    },
    fail: {
        key: 'fail',
        label: 'Fail',
        description: 'Terminal failure',
        accent: 'text-red-700 dark:text-red-400',
        bg: 'bg-red-600/80 dark:bg-red-500/80',
    },
    in_flight: {
        key: 'in_flight',
        label: 'In flight',
        description: 'Backlog / in-progress / review / testing',
        accent: 'text-sky-700 dark:text-sky-400',
        bg: 'bg-sky-500/80 dark:bg-sky-400/80',
    },
};

function bucketByKey(funnel: AnalyticsThroughputFunnel, key: BucketVisual['key']) {
    return funnel.buckets.find(b => b.bucket === key) ?? { bucket: key, count: 0, pct: 0 };
}

export function ThroughputFunnel({ data }: ThroughputFunnelProps) {
    const total = data?.total ?? 0;
    const order: BucketVisual['key'][] = ['pass', 'rework', 'fail', 'in_flight'];

    return (
        <div className="rounded-md border border-border p-4">
            <div className="flex items-baseline justify-between mb-3">
                <h3 className="text-sm font-medium">Throughput funnel</h3>
                <div className="text-xs text-muted-foreground tabular-nums">
                    <span className="font-semibold text-foreground">{total}</span> task{total === 1 ? '' : 's'} total
                </div>
            </div>

            {/* Stacked bar — the visible proof that the buckets sum to 100%. */}
            <div className="flex h-7 w-full overflow-hidden rounded-md border border-border/60">
                {total === 0 ? (
                    <div className="flex-1 bg-muted/40" aria-label="No throughput data" />
                ) : (
                    order.map(key => {
                        const b = bucketByKey(data, key);
                        const widthPct = total > 0 ? (b.pct) : 0;
                        return (
                            <div
                                key={key}
                                className={`${VISUAL[key].bg} flex items-center justify-center text-[10px] font-medium text-white tabular-nums`}
                                style={{ width: `${widthPct}%` }}
                                title={`${VISUAL[key].label}: ${b.count} (${Math.round(b.pct)}%)`}
                                aria-label={`${VISUAL[key].label} ${b.count} ${Math.round(b.pct)} percent`}
                            >
                                {widthPct >= 8 ? `${Math.round(b.pct)}%` : ''}
                            </div>
                        );
                    })
                )}
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {order.map(key => {
                    const b = bucketByKey(data, key);
                    const meta = VISUAL[key];
                    return (
                        <div
                            key={key}
                            className="rounded-md border border-border/60 px-3 py-2"
                            data-testid={`funnel-bucket-${key}`}
                        >
                            <div className={`text-xs uppercase tracking-wide ${meta.accent}`}>{meta.label}</div>
                            <div className="mt-1 flex items-baseline gap-1.5 tabular-nums">
                                <span className="text-xl font-semibold leading-none">{b.count}</span>
                                <span className="text-xs text-muted-foreground">({Math.round(b.pct)}%)</span>
                            </div>
                            <div className="mt-0.5 text-[11px] text-muted-foreground">{meta.description}</div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}