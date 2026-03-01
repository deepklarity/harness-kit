import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
    PieChart, Pie, Cell,
    Legend,
} from 'recharts';
import type { Task, Member, ReflectionReport } from '../types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { getStatusColor } from '../utils/transformer';
import { BarChart3, Users, Coins, Zap, FileSearch } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

interface DashboardChartsProps {
    tasks: Task[];
    members: Member[];
    reflections: ReflectionReport[];
}

const CHART_COLORS = [
    'var(--chart-1)',
    'var(--chart-2)',
    'var(--chart-3)',
    'var(--chart-4)',
    'var(--chart-5)',
    'var(--chart-1)', // Cycle if needed
];

const customTooltipStyle = {
    backgroundColor: 'var(--popover)',
    border: '1px solid var(--border)',
    borderRadius: 'calc(var(--radius) - 2px)',
    padding: '8px 12px',
    boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
    color: 'var(--popover-foreground)',
    fontSize: '12px',
};

function ChartCard({ title, icon: Icon, children, className = '' }: {
    title: string; icon: LucideIcon; children: React.ReactNode; className?: string;
}) {
    return (
        <Card className={`border-border ${className}`}>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                    <Icon className="size-4 text-muted-foreground" />
                    {title}
                </CardTitle>
            </CardHeader>
            <CardContent>{children}</CardContent>
        </Card>
    );
}

export function DashboardCharts({ tasks, members, reflections }: DashboardChartsProps) {
    const statusCounts: Record<string, number> = {};
    for (const t of tasks) {
        statusCounts[t.currentStatus] = (statusCounts[t.currentStatus] || 0) + 1;
    }
    const statusData = Object.entries(statusCounts).map(([name, value]) => ({ name, value }));

    const countByMember = new Map<string, number>();
    for (const t of tasks) {
        for (const id of t.assigneeIds) {
            countByMember.set(id, (countByMember.get(id) ?? 0) + 1);
        }
    }
    const memberTaskData = members
        .map(m => ({ name: m.fullName, tasks: countByMember.get(m.id) ?? 0 }))
        .filter(m => m.tasks > 0)
        .sort((a, b) => b.tasks - a.tasks);


    const costByModel = new Map<string, number>();
    for (const t of tasks) {
        if (!t.modelName || t.estimatedCostUsd == null) continue;
        costByModel.set(t.modelName, (costByModel.get(t.modelName) ?? 0) + t.estimatedCostUsd);
    }
    const costByModelData = Array.from(costByModel.entries())
        .filter(([, cost]) => cost > 0)
        .map(([name, cost]) => ({ name, cost }))
        .sort((a, b) => b.cost - a.cost);

    const formatCost = (value: number) =>
        value < 0.01 ? '< $0.01' : `$${value.toFixed(2)}`;

    const tokenByModel = new Map<string, { input: number; output: number; cacheRead: number }>();
    for (const t of tasks) {
        if (!t.modelName || !t.usage) continue;
        const existing = tokenByModel.get(t.modelName) ?? { input: 0, output: 0, cacheRead: 0 };
        tokenByModel.set(t.modelName, {
            input: existing.input + (t.usage.input_tokens ?? 0),
            output: existing.output + (t.usage.output_tokens ?? 0),
            cacheRead: existing.cacheRead + (t.usage.cache_read_input_tokens ?? 0),
        });
    }
    const tokenByModelData = Array.from(tokenByModel.entries())
        .filter(([, v]) => v.input + v.output + v.cacheRead > 0)
        .map(([model, v]) => ({
            model: model.replace(/^claude-/, ''),
            input: v.input,
            output: v.output,
            cacheRead: v.cacheRead,
        }))
        .sort((a, b) => (b.input + b.output + b.cacheRead) - (a.input + a.output + a.cacheRead));

    const formatTokens = (n: number) => n.toLocaleString('en-US');

    // Pass Rate by Assignee: map reflections → task → assignee
    const taskById = new Map(tasks.map(t => [String(t.id), t]));
    const assigneeReflectionStats = new Map<string, { pass: number; total: number }>();
    for (const r of reflections) {
        if (r.status !== 'COMPLETED') continue;
        const task = taskById.get(String(r.task));
        if (!task) continue;
        const assigneeIds = task.assigneeIds.length > 0 ? task.assigneeIds : ['unassigned'];
        for (const aid of assigneeIds) {
            const stats = assigneeReflectionStats.get(aid) ?? { pass: 0, total: 0 };
            stats.total += 1;
            if (r.verdict.toUpperCase() === 'PASS') stats.pass += 1;
            assigneeReflectionStats.set(aid, stats);
        }
    }
    const passRateByAssigneeData = Array.from(assigneeReflectionStats.entries())
        .map(([id, stats]) => {
            const member = members.find(m => m.id === id);
            return {
                name: member?.fullName ?? (id === 'unassigned' ? 'Unassigned' : id),
                passRate: Math.round((stats.pass / stats.total) * 100),
                pass: stats.pass,
                total: stats.total,
            };
        })
        .sort((a, b) => b.passRate - a.passRate);

    return (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(400px,1fr))] gap-4 mb-8">
            <ChartCard title="Status Distribution" icon={BarChart3}>
                <ResponsiveContainer width="100%" height={280}>
                    <PieChart>
                        <Pie
                            data={statusData} cx="50%" cy="45%"
                            innerRadius={55} outerRadius={85} paddingAngle={2} dataKey="value"
                            stroke="var(--card)"
                        >
                            {statusData.map((entry, index) => (
                                <Cell key={`cell-${index}`} fill={getStatusColor(entry.name) || CHART_COLORS[index % CHART_COLORS.length]} />
                            ))}
                        </Pie>
                        <Tooltip contentStyle={customTooltipStyle} itemStyle={{ color: 'var(--popover-foreground)' }} formatter={(value: unknown) => [`${value} tasks`, 'Count']} />
                        <Legend
                            verticalAlign="bottom"
                            iconType="circle"
                            iconSize={8}
                            formatter={(value: string) => {
                                const entry = statusData.find(d => d.name === value);
                                const total = statusData.reduce((sum, d) => sum + d.value, 0);
                                const pct = entry && total > 0 ? Math.round((entry.value / total) * 100) : 0;
                                return `${value} (${pct}%)`;
                            }}
                            wrapperStyle={{ fontSize: 12, color: 'var(--muted-foreground)' }}
                        />
                    </PieChart>
                </ResponsiveContainer>
            </ChartCard>

            <ChartCard title="Tasks per Member" icon={Users}>
                <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={memberTaskData} layout="vertical" margin={{ left: 20 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis type="number" stroke="var(--muted-foreground)" fontSize={11} tickLine={false} axisLine={false} />
                        <YAxis type="category" dataKey="name" stroke="var(--muted-foreground)" fontSize={12} width={100} tickLine={false} axisLine={false} />
                        <Tooltip contentStyle={customTooltipStyle} cursor={{ fill: 'color-mix(in srgb, var(--muted), transparent 80%)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                        <Bar dataKey="tasks" radius={[0, 4, 4, 0]} barSize={20}>
                            {memberTaskData.map((_, index) => (
                                <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                            ))}
                        </Bar>
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            <ChartCard title="Cost by Model" icon={Coins}>
                <ResponsiveContainer width="100%" height={280}>
                    <PieChart>
                        <Pie
                            data={costByModelData} cx="50%" cy="45%"
                            innerRadius={55} outerRadius={85} paddingAngle={2} dataKey="cost"
                            stroke="var(--card)"
                        >
                            {costByModelData.map((_, index) => (
                                <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
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
                                const entry = costByModelData.find(d => d.name === value);
                                return `${value} (${entry ? formatCost(entry.cost) : '$0.00'})`;
                            }}
                            wrapperStyle={{ fontSize: 12, color: 'var(--muted-foreground)' }}
                        />
                    </PieChart>
                </ResponsiveContainer>
            </ChartCard>

            <ChartCard title="Token Usage by Model" icon={Zap}>
                <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={tokenByModelData} margin={{ top: 10, right: 10, bottom: 20, left: 10 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                        <XAxis
                            dataKey="model"
                            stroke="var(--muted-foreground)"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            angle={-25}
                            textAnchor="end"
                            interval={0}
                            height={50}
                        />
                        <YAxis
                            stroke="var(--muted-foreground)"
                            fontSize={11}
                            tickLine={false}
                            axisLine={false}
                            tickFormatter={(v: number) => v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : v >= 1_000 ? `${(v / 1_000).toFixed(0)}K` : String(v)}
                        />
                        <Tooltip
                            contentStyle={customTooltipStyle}
                            cursor={{ fill: 'color-mix(in srgb, var(--muted), transparent 80%)' }}
                            itemStyle={{ color: 'var(--popover-foreground)' }}
                            formatter={(value: unknown, name: unknown) => {
                                const labels: Record<string, string> = { input: 'Input Tokens', output: 'Output Tokens', cacheRead: 'Cache Read Tokens' };
                                return [formatTokens(value as number), labels[name as string] ?? name];
                            }}
                        />
                        <Legend
                            verticalAlign="top"
                            iconType="square"
                            iconSize={8}
                            formatter={(value: string) => {
                                const labels: Record<string, string> = { input: 'Input', output: 'Output', cacheRead: 'Cache Read' };
                                return labels[value] ?? value;
                            }}
                            wrapperStyle={{ fontSize: 12, color: 'var(--muted-foreground)' }}
                        />
                        <Bar dataKey="input" stackId="tokens" fill="var(--chart-1)" name="input" radius={[0, 0, 0, 0]} />
                        <Bar dataKey="output" stackId="tokens" fill="var(--chart-2)" name="output" radius={[0, 0, 0, 0]} />
                        <Bar dataKey="cacheRead" stackId="tokens" fill="var(--chart-3)" name="cacheRead" radius={[4, 4, 0, 0]} />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            {passRateByAssigneeData.length > 0 && (
                <ChartCard title="Reflection Pass Rate by Assignee" icon={FileSearch}>
                    <ResponsiveContainer width="100%" height={280}>
                        <BarChart data={passRateByAssigneeData} layout="vertical" margin={{ left: 20, right: 20 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                            <XAxis
                                type="number"
                                domain={[0, 100]}
                                stroke="var(--muted-foreground)"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                                tickFormatter={(v: number) => `${v}%`}
                            />
                            <YAxis type="category" dataKey="name" stroke="var(--muted-foreground)" fontSize={12} width={100} tickLine={false} axisLine={false} />
                            <Tooltip
                                contentStyle={customTooltipStyle}
                                cursor={{ fill: 'color-mix(in srgb, var(--muted), transparent 80%)' }}
                                itemStyle={{ color: 'var(--popover-foreground)' }}
                                formatter={(_: unknown, __: unknown, props: { payload?: { pass?: number; total?: number } }) => {
                                    const { pass = 0, total = 0 } = props.payload ?? {};
                                    return [`${pass} of ${total} passed`, 'Reflections'];
                                }}
                            />
                            <Bar dataKey="passRate" radius={[0, 4, 4, 0]} barSize={20}>
                                {passRateByAssigneeData.map((entry, index) => (
                                    <Cell
                                        key={`cell-${index}`}
                                        fill={entry.passRate >= 80 ? 'var(--chart-3)' : entry.passRate >= 50 ? 'var(--chart-4)' : 'var(--chart-1)'}
                                    />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>
            )}

        </div>
    );
}
