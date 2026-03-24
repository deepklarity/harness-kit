import {
    PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Users } from 'lucide-react';
import { customTooltipStyle, CHART_COLORS } from './chartUtils';
import { formatCost } from '../../utils/costEstimation';
import type { AnalyticsCostByAgent } from '../../types';

interface CostByAgentChartProps {
    data: AnalyticsCostByAgent[];
}

export function CostByAgentChart({ data }: CostByAgentChartProps) {
    const chartData = data
        .filter(d => d.cost > 0)
        .map(d => ({ name: d.agent, cost: d.cost, tasks: d.task_count }));

    if (chartData.length === 0) {
        return (
            <Card className="border-border">
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                        <Users className="size-4 text-muted-foreground" />
                        Cost by Agent
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center justify-center h-[280px] text-sm text-muted-foreground">
                        No agent cost data
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="border-border">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                    <Users className="size-4 text-muted-foreground" />
                    Cost by Agent
                </CardTitle>
            </CardHeader>
            <CardContent>
                <ResponsiveContainer width="100%" height={280}>
                    <PieChart>
                        <Pie
                            data={chartData} cx="50%" cy="45%"
                            innerRadius={55} outerRadius={85} paddingAngle={2} dataKey="cost"
                            stroke="var(--card)"
                        >
                            {chartData.map((_, i) => (
                                <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                            ))}
                        </Pie>
                        <Tooltip
                            contentStyle={customTooltipStyle}
                            itemStyle={{ color: 'var(--popover-foreground)' }}
                            formatter={(value: unknown) => [formatCost(value as number), 'Cost']}
                        />
                        <Legend
                            verticalAlign="bottom"
                            iconType="circle"
                            iconSize={8}
                            formatter={(value: string) => {
                                const entry = chartData.find(d => d.name === value);
                                return `${value} (${entry ? formatCost(entry.cost) : '$0.00'})`;
                            }}
                            wrapperStyle={{ fontSize: 12, color: 'var(--muted-foreground)' }}
                        />
                    </PieChart>
                </ResponsiveContainer>
            </CardContent>
        </Card>
    );
}
