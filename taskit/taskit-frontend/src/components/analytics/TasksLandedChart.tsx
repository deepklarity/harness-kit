import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ListChecks } from 'lucide-react';
import { customTooltipStyle } from './chartUtils';
import type { AnalyticsTimeSeries } from '../../types';

interface TasksLandedChartProps {
    data: AnalyticsTimeSeries[];
}

const DAYS = 14;

// Last 14 daily buckets by date, zero-filled — the underlying series only
// contains buckets with the current day's granularity, so days with no
// landed tasks won't appear in `data` at all unless we fill them in.
function last14Days(data: AnalyticsTimeSeries[]): { date: string; landed: number }[] {
    const byDate = new Map(data.map(d => [d.date, d.task_count]));
    const days: { date: string; landed: number }[] = [];
    const today = new Date();
    for (let i = DAYS - 1; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(d.getDate() - i);
        const key = d.toISOString().slice(0, 10);
        days.push({ date: key.slice(5), landed: byDate.get(key) ?? 0 });
    }
    return days;
}

export function TasksLandedChart({ data }: TasksLandedChartProps) {
    const chartData = last14Days(data);
    const hasAny = chartData.some(d => d.landed > 0);

    return (
        <Card className="border-border">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                    <ListChecks className="size-4 text-muted-foreground" />
                    Tasks landed / day
                </CardTitle>
            </CardHeader>
            <CardContent>
                {!hasAny ? (
                    <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
                        No tasks landed in the last 14 days
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height={220}>
                        <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                            <XAxis dataKey="date" stroke="var(--muted-foreground)" fontSize={11} tickLine={false} axisLine={false} />
                            <YAxis stroke="var(--muted-foreground)" fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} />
                            <Tooltip
                                contentStyle={customTooltipStyle}
                                itemStyle={{ color: 'var(--popover-foreground)' }}
                                labelStyle={{ color: 'var(--popover-foreground)', fontWeight: 600 }}
                                formatter={(value: unknown) => [`${value}`, 'Landed']}
                            />
                            <Bar dataKey="landed" fill="var(--chart-3)" radius={[3, 3, 0, 0]} />
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </CardContent>
        </Card>
    );
}
