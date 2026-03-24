import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { TrendingUp } from 'lucide-react';
import { customTooltipStyle, CHART_COLORS } from './chartUtils';
import { shortModelName } from '../../utils/transformer';
import type { AnalyticsTimeSeries } from '../../types';

interface CostOverTimeChartProps {
    data: AnalyticsTimeSeries[];
}

export function CostOverTimeChart({ data }: CostOverTimeChartProps) {
    // Collect all unique models and their total cost across the series
    const modelTotals = new Map<string, number>();
    for (const entry of data) {
        for (const [model, cost] of Object.entries(entry.by_model)) {
            modelTotals.set(model, (modelTotals.get(model) ?? 0) + cost);
        }
    }

    // Only show models that actually have cost, sorted by total cost desc
    const models = Array.from(modelTotals.entries())
        .filter(([, total]) => total > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([model]) => model);

    // Build short name lookup (handle collisions by appending suffix)
    const shortNames = new Map<string, string>();
    const usedShorts = new Map<string, number>();
    for (const model of models) {
        let short = shortModelName(model);
        const count = usedShorts.get(short) ?? 0;
        if (count > 0) short = `${short} (${count + 1})`;
        usedShorts.set(shortModelName(model), count + 1);
        shortNames.set(model, short);
    }

    // Transform data for Recharts: use short names as keys
    const chartData = data.map(entry => {
        const row: Record<string, string | number> = { date: entry.date, total: entry.total };
        for (const model of models) {
            row[shortNames.get(model)!] = entry.by_model[model] || 0;
        }
        return row;
    });

    const shortKeys = models.map(m => shortNames.get(m)!);

    if (chartData.length === 0) {
        return (
            <Card className="border-border">
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                        <TrendingUp className="size-4 text-muted-foreground" />
                        Cost Over Time
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center justify-center h-[280px] text-sm text-muted-foreground">
                        No cost data available for the selected period
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="border-border">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                    <TrendingUp className="size-4 text-muted-foreground" />
                    Cost Over Time
                </CardTitle>
            </CardHeader>
            <CardContent>
                <ResponsiveContainer width="100%" height={320}>
                    <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis
                            dataKey="date"
                            stroke="var(--muted-foreground)"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                        />
                        <YAxis
                            stroke="var(--muted-foreground)"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                        />
                        <Tooltip
                            contentStyle={customTooltipStyle}
                            itemStyle={{ color: 'var(--popover-foreground)' }}
                            formatter={(value: unknown, name: unknown) => {
                                const cost = value as number;
                                if (cost === 0) return [null, null]; // hide $0 lines
                                return [`$${cost.toFixed(4)}`, name as string];
                            }}
                            labelStyle={{ color: 'var(--popover-foreground)', fontWeight: 600 }}
                            filterNull
                        />
                        <Legend
                            verticalAlign="top"
                            iconType="line"
                            iconSize={12}
                            wrapperStyle={{ fontSize: 11, color: 'var(--muted-foreground)', paddingBottom: 8 }}
                        />
                        {shortKeys.map((shortName, i) => (
                            <Line
                                key={shortName}
                                type="monotone"
                                dataKey={shortName}
                                stroke={CHART_COLORS[i % CHART_COLORS.length]}
                                strokeWidth={2}
                                dot={false}
                                name={shortName}
                            />
                        ))}
                    </LineChart>
                </ResponsiveContainer>
            </CardContent>
        </Card>
    );
}
