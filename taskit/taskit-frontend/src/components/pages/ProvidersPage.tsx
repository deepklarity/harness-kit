import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useService } from '../../contexts/ServiceContext';
import type { ProviderUsage, ProviderUsageResponse } from '../../types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
    RefreshCw, Clock, ChevronDown, ChevronRight,
    ArrowUpDown, Wifi, WifiOff, AlertTriangle, HelpCircle,
} from 'lucide-react';

const STATE_CONFIG: Record<string, { label: string; color: string; icon: typeof Wifi }> = {
    online: { label: 'Online', color: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/20', icon: Wifi },
    offline: { label: 'Offline', color: 'bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/20', icon: WifiOff },
    degraded: { label: 'Degraded', color: 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/20', icon: AlertTriangle },
    unknown: { label: 'Unknown', color: 'bg-muted text-muted-foreground border-border', icon: HelpCircle },
};

function getBarColor(pct: number | null): string {
    if (pct === null) return 'bg-muted-foreground/30';
    if (pct < 60) return 'bg-emerald-500';
    if (pct < 80) return 'bg-amber-500';
    return 'bg-red-500';
}

function formatProviderName(name: string): string {
    return name.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

function formatResetTime(resetDate: string | null): string {
    if (!resetDate) return '—';
    const reset = new Date(resetDate);
    const now = new Date();
    const diffMs = reset.getTime() - now.getTime();
    if (diffMs <= 0) return 'now';
    const hours = Math.floor(diffMs / 3600000);
    const minutes = Math.floor((diffMs % 3600000) / 60000);
    if (hours > 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
}

function formatLatency(ms: number | null): string {
    if (ms === null) return '—';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

function StateBadge({ state }: { state: string }) {
    const cfg = STATE_CONFIG[state] || STATE_CONFIG.unknown;
    const Icon = cfg.icon;
    return (
        <Badge variant="outline" className={`${cfg.color} gap-1 text-[11px] font-medium`}>
            <Icon className="size-3" />
            {cfg.label}
        </Badge>
    );
}

function ProgressBar({ pct }: { pct: number | null }) {
    const displayPct = pct ?? 0;
    return (
        <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
            <div
                className={`h-full rounded-full transition-all duration-500 ${getBarColor(pct)}`}
                style={{ width: `${Math.min(displayPct, 100)}%` }}
            />
        </div>
    );
}

function ProviderCard({ provider }: { provider: ProviderUsage }) {
    return (
        <Card className="relative">
            <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                    <CardTitle className="text-sm font-semibold">
                        {formatProviderName(provider.name)}
                    </CardTitle>
                    <StateBadge state={provider.state} />
                </div>
                {provider.plan && (
                    <span className="text-xs text-muted-foreground">{provider.plan}</span>
                )}
            </CardHeader>
            <CardContent className="space-y-3">
                <div>
                    <div className="flex items-center justify-between text-xs mb-1.5">
                        <span className="text-muted-foreground">Usage</span>
                        <span className="font-mono font-medium">
                            {provider.usage_pct !== null ? `${provider.usage_pct.toFixed(1)}%` : '—'}
                        </span>
                    </div>
                    <ProgressBar pct={provider.usage_pct} />
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">Remaining</span>
                        <span className="font-mono">
                            {provider.remaining !== null
                                ? `${provider.remaining.toLocaleString()} ${provider.unit}`
                                : '—'
                            }
                        </span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">Latency</span>
                        <span className="font-mono">{formatLatency(provider.latency_ms)}</span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">Resets in</span>
                        <span className="font-mono">{formatResetTime(provider.reset_date)}</span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">Unit</span>
                        <span className="font-mono">{provider.unit}</span>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}

function RawBreakdown({ raw, providerName }: { raw: Record<string, unknown> | null; providerName: string }) {
    if (!raw) return <span className="text-xs text-muted-foreground">No detail data</span>;

    // Claude Code — show 5h/7d windows
    if (providerName === 'claude_code' && raw.five_hour && raw.seven_day) {
        const fiveHour = raw.five_hour as { utilization?: number; resets_at?: string };
        const sevenDay = raw.seven_day as { utilization?: number; resets_at?: string };
        return (
            <div className="grid grid-cols-2 gap-4 text-xs">
                <div className="space-y-1">
                    <div className="font-medium">5-Hour Window</div>
                    <div className="flex justify-between"><span className="text-muted-foreground">Utilization</span><span className="font-mono">{fiveHour.utilization?.toFixed(1) ?? '—'}%</span></div>
                    <div className="flex justify-between"><span className="text-muted-foreground">Resets</span><span className="font-mono">{formatResetTime(fiveHour.resets_at ?? null)}</span></div>
                </div>
                <div className="space-y-1">
                    <div className="font-medium">7-Day Window</div>
                    <div className="flex justify-between"><span className="text-muted-foreground">Utilization</span><span className="font-mono">{sevenDay.utilization?.toFixed(1) ?? '—'}%</span></div>
                    <div className="flex justify-between"><span className="text-muted-foreground">Resets</span><span className="font-mono">{formatResetTime(sevenDay.resets_at ?? null)}</span></div>
                </div>
            </div>
        );
    }

    // Gemini — show per-model quotas
    if (providerName === 'gemini' && raw.models && typeof raw.models === 'object') {
        const models = raw.models as Record<string, { used_pct?: number; remaining_pct?: number; remaining?: number; limit?: number; reset_time?: string }>;
        return (
            <div className="space-y-2 text-xs">
                {Object.entries(models).map(([model, data]) => (
                    <div key={model} className="space-y-1">
                        <div className="font-medium font-mono">{model}</div>
                        <div className="flex items-center gap-3">
                            <div className="flex-1">
                                <ProgressBar pct={data.used_pct ?? null} />
                            </div>
                            <span className="font-mono w-12 text-right">{data.used_pct?.toFixed(0) ?? '—'}%</span>
                            <span className="text-muted-foreground">{data.remaining?.toLocaleString() ?? '—'} / {data.limit?.toLocaleString() ?? '—'}</span>
                        </div>
                    </div>
                ))}
            </div>
        );
    }

    // Codex — show primary/secondary windows
    if (providerName === 'codex' && (raw.primary_window || raw.secondary_window)) {
        const primary = raw.primary_window as { used_percent?: number; windowDurationMins?: number; resetsAt?: number } | undefined;
        const secondary = raw.secondary_window as { used_percent?: number; windowDurationMins?: number; resetsAt?: number } | undefined;
        return (
            <div className="grid grid-cols-2 gap-4 text-xs">
                {primary && (
                    <div className="space-y-1">
                        <div className="font-medium">Primary ({primary.windowDurationMins ?? '?'}m window)</div>
                        <div className="flex justify-between"><span className="text-muted-foreground">Used</span><span className="font-mono">{primary.used_percent?.toFixed(1) ?? '—'}%</span></div>
                    </div>
                )}
                {secondary && (
                    <div className="space-y-1">
                        <div className="font-medium">Secondary ({secondary.windowDurationMins ?? '?'}m window)</div>
                        <div className="flex justify-between"><span className="text-muted-foreground">Used</span><span className="font-mono">{secondary.used_percent?.toFixed(1) ?? '—'}%</span></div>
                    </div>
                )}
            </div>
        );
    }

    // MiniMax — show per-model prompt counts
    if (providerName === 'minimax' && raw.model_remains && Array.isArray(raw.model_remains)) {
        const models = raw.model_remains as Array<{ model?: string; current_interval_usage_count?: number; current_interval_total_count?: number }>;
        return (
            <div className="space-y-1 text-xs">
                {models.map((m, idx) => (
                    <div key={idx} className="flex justify-between">
                        <span className="font-mono">{m.model ?? 'unknown'}</span>
                        <span className="font-mono">{m.current_interval_usage_count?.toLocaleString() ?? '—'} / {m.current_interval_total_count?.toLocaleString() ?? '—'} prompts</span>
                    </div>
                ))}
            </div>
        );
    }

    // Generic fallback — show raw as key/value (skip nested objects)
    const entries = Object.entries(raw).filter(([, v]) => typeof v !== 'object');
    if (entries.length === 0) return <span className="text-xs text-muted-foreground">No detail data</span>;
    return (
        <div className="space-y-0.5 text-xs">
            {entries.map(([k, v]) => (
                <div key={k} className="flex justify-between">
                    <span className="text-muted-foreground">{k}</span>
                    <span className="font-mono">{String(v)}</span>
                </div>
            ))}
        </div>
    );
}

type SortField = 'usage_pct' | 'name' | 'state' | 'latency_ms';
type SortDir = 'asc' | 'desc';

function ProviderTable({ providers }: { providers: ProviderUsage[] }) {
    const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
    const [sortField, setSortField] = useState<SortField>('usage_pct');
    const [sortDir, setSortDir] = useState<SortDir>('desc');

    const toggleExpand = (name: string) => {
        setExpandedRows(prev => {
            const next = new Set(prev);
            if (next.has(name)) next.delete(name); else next.add(name);
            return next;
        });
    };

    const toggleSort = (field: SortField) => {
        if (sortField === field) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(field);
            setSortDir(field === 'name' ? 'asc' : 'desc');
        }
    };

    const sorted = useMemo(() => {
        return [...providers].sort((a, b) => {
            let cmp = 0;
            if (sortField === 'name') {
                cmp = a.name.localeCompare(b.name);
            } else if (sortField === 'state') {
                const order: Record<string, number> = { online: 0, degraded: 1, unknown: 2, offline: 3 };
                cmp = (order[a.state] ?? 2) - (order[b.state] ?? 2);
            } else {
                const aVal = a[sortField] ?? -1;
                const bVal = b[sortField] ?? -1;
                cmp = (aVal as number) - (bVal as number);
            }
            return sortDir === 'asc' ? cmp : -cmp;
        });
    }, [providers, sortField, sortDir]);

    const renderSortHeader = (field: SortField, children: React.ReactNode) => (
        <button type="button" className="flex items-center gap-1 hover:text-foreground transition-colors" onClick={() => toggleSort(field)}>
            {children}
            <ArrowUpDown className={`size-3 ${sortField === field ? 'opacity-100' : 'opacity-30'}`} />
        </button>
    );

    return (
        <div className="rounded-lg border border-border overflow-hidden">
            <table className="w-full text-sm">
                <thead>
                    <tr className="border-b border-border bg-muted/50">
                        <th className="text-left font-medium text-muted-foreground px-4 py-2.5 w-8" />
                        <th className="text-left font-medium text-muted-foreground px-4 py-2.5">
                            {renderSortHeader('name', 'Provider')}
                        </th>
                        <th className="text-left font-medium text-muted-foreground px-4 py-2.5">
                            {renderSortHeader('state', 'State')}
                        </th>
                        <th className="text-left font-medium text-muted-foreground px-4 py-2.5 w-48">
                            {renderSortHeader('usage_pct', 'Usage')}
                        </th>
                        <th className="text-right font-medium text-muted-foreground px-4 py-2.5">Remaining</th>
                        <th className="text-right font-medium text-muted-foreground px-4 py-2.5">
                            {renderSortHeader('latency_ms', 'Latency')}
                        </th>
                        <th className="text-right font-medium text-muted-foreground px-4 py-2.5">Resets</th>
                    </tr>
                </thead>
                <tbody>
                    {sorted.map(p => {
                        const isExpanded = expandedRows.has(p.name);
                        const hasRaw = p.raw && Object.keys(p.raw).length > 0;
                        return (
                            <React.Fragment key={p.name}>
                                <tr
                                    className={`border-b border-border hover:bg-muted/30 transition-colors ${hasRaw ? 'cursor-pointer' : ''}`}
                                    onClick={() => hasRaw && toggleExpand(p.name)}
                                >
                                    <td className="px-4 py-2.5 text-muted-foreground">
                                        {hasRaw ? (
                                            isExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />
                                        ) : null}
                                    </td>
                                    <td className="px-4 py-2.5 font-medium">{formatProviderName(p.name)}</td>
                                    <td className="px-4 py-2.5"><StateBadge state={p.state} /></td>
                                    <td className="px-4 py-2.5">
                                        <div className="flex items-center gap-2">
                                            <div className="flex-1"><ProgressBar pct={p.usage_pct} /></div>
                                            <span className="font-mono text-xs w-12 text-right">
                                                {p.usage_pct !== null ? `${p.usage_pct.toFixed(0)}%` : '—'}
                                            </span>
                                        </div>
                                    </td>
                                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                                        {p.remaining !== null ? `${p.remaining.toLocaleString()} ${p.unit}` : '—'}
                                    </td>
                                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                                        {formatLatency(p.latency_ms)}
                                    </td>
                                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                                        {formatResetTime(p.reset_date)}
                                    </td>
                                </tr>
                                {isExpanded && (
                                    <tr key={`${p.name}-detail`} className="border-b border-border bg-muted/20">
                                        <td />
                                        <td colSpan={6} className="px-4 py-3">
                                            <RawBreakdown raw={p.raw} providerName={p.name} />
                                        </td>
                                    </tr>
                                )}
                            </React.Fragment>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

export function ProvidersPage() {
    const service = useService();
    const [data, setData] = useState<ProviderUsageResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const result = await service.fetchProviderUsage();
            setData(result);
            if (result.error) setError(result.error);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch provider data');
        } finally {
            setLoading(false);
        }
    }, [service]);

    useEffect(() => {
        fetchData();
        const interval = setInterval(fetchData, 60000);
        return () => clearInterval(interval);
    }, [fetchData]);

    const providers = data?.providers ?? [];

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-xl font-bold tracking-tight">Providers</h1>
                    <p className="text-sm text-muted-foreground mt-0.5">
                        AI provider quota and health status
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    {data?.fetched_at && (
                        <span className="text-xs text-muted-foreground flex items-center gap-1">
                            <Clock className="size-3" />
                            {new Date(data.fetched_at).toLocaleTimeString()}
                        </span>
                    )}
                    <Button variant="outline" size="sm" className="gap-1.5" onClick={fetchData} disabled={loading}>
                        <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            {error && (
                <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3">
                    {error}
                </div>
            )}

            {!loading && providers.length === 0 && !error && (
                <div className="text-center py-12 text-muted-foreground">
                    <p className="text-sm">No provider data available</p>
                </div>
            )}

            {providers.length > 0 && (
                <>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                        {providers.map(p => (
                            <ProviderCard key={p.name} provider={p} />
                        ))}
                    </div>

                    <div>
                        <h2 className="text-sm font-semibold mb-3">Detail</h2>
                        <ProviderTable providers={providers} />
                    </div>
                </>
            )}

            {loading && providers.length === 0 && (
                <div className="flex items-center justify-center py-12">
                    <div className="size-8 rounded-full border-2 border-border border-t-primary animate-spin" />
                </div>
            )}
        </div>
    );
}
