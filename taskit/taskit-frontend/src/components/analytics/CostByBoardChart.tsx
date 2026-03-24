import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { LayoutDashboard } from 'lucide-react';
import { customTooltipStyle, CHART_COLORS } from './chartUtils';
import { formatCost } from '../../utils/costEstimation';
import type { AnalyticsCostByBoard } from '../../types';

interface CostByBoardChartProps {
    data: AnalyticsCostByBoard[];
}

export function CostByBoardChart({ data }: CostByBoardChartProps) {
    const chartData = data
        .filter(d => d.cost > 0)
        .map(d => ({ name: d.board_name, cost: d.cost, tasks: d.task_count }));

    if (chartData.length === 0) {
        return (
            <Card className="border-border">
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                        <LayoutDashboard className="size-4 text-muted-foreground" />
                        Cost by Board
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center justify-center h-[280px] text-sm text-muted-foreground">
                        No board cost data
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="border-border">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                    <LayoutDashboard className="size-4 text-muted-foreground" />
                    Cost by Board
                </CardTitle>
            </CardHeader>
            <CardContent>
                <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={chartData} layout="vertical" margin={{ left: 20 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis
                            type="number"
                            stroke="var(--muted-foreground)"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                        />
                        <YAxis
                            type="category"
                            dataKey="name"
                            stroke="var(--muted-foreground)"
                            fontSize={12}
                            width={120}
                            tickLine={false}
                            axisLine={false}
                        />
                        <Tooltip
                            contentStyle={customTooltipStyle}
                            cursor={{ fill: 'color-mix(in srgb, var(--muted), transparent 80%)' }}
                            itemStyle={{ color: 'var(--popover-foreground)' }}
                            formatter={(value: unknown) => [formatCost(value as number), 'Cost']}
                        />
                        <Bar dataKey="cost" radius={[0, 4, 4, 0]} barSize={20}>
                            {chartData.map((_, i) => (
                                <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                            ))}
                        </Bar>
                    </BarChart>
                </ResponsiveContainer>
            </CardContent>
        </Card>
    );
}
