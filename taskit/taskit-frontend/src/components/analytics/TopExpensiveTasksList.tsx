import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DollarSign } from 'lucide-react';
import { formatCost } from '../../utils/costEstimation';
import { formatTokens, shortModelName, getStatusColor } from '../../utils/transformer';
import type { AnalyticsTopExpensiveTask } from '../../types';

interface TopExpensiveTasksListProps {
    data: AnalyticsTopExpensiveTask[];
    onTaskClick?: (taskId: string) => void;
}

export function TopExpensiveTasksList({ data, onTaskClick }: TopExpensiveTasksListProps) {
    return (
        <Card className="border-border">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                    <DollarSign className="size-4 text-muted-foreground" />
                    Top Expensive Tasks
                </CardTitle>
            </CardHeader>
            <CardContent>
                {data.length === 0 ? (
                    <div className="text-sm text-muted-foreground py-8 text-center">No tasks with cost data</div>
                ) : (
                    <div className="space-y-1">
                        {data.map((task, i) => (
                            <button
                                key={task.task_id}
                                type="button"
                                className="w-full flex items-center gap-3 py-2 px-3 rounded-md hover:bg-muted/50 transition-colors text-left"
                                onClick={() => onTaskClick?.(String(task.task_id))}
                            >
                                <span className="text-xs font-mono text-muted-foreground w-5 text-right shrink-0">
                                    {i + 1}
                                </span>
                                <span className="flex-1 min-w-0 truncate text-sm">
                                    {task.title}
                                </span>
                                <span
                                    className="text-[10px] font-medium px-1.5 py-0.5 rounded shrink-0"
                                    style={{
                                        color: getStatusColor(task.status),
                                        backgroundColor: `color-mix(in srgb, ${getStatusColor(task.status)}, transparent 85%)`,
                                    }}
                                >
                                    {task.status}
                                </span>
                                <span className="text-xs text-muted-foreground shrink-0 w-16 text-right">
                                    {shortModelName(task.model)}
                                </span>
                                <span className="text-xs text-muted-foreground shrink-0 w-16 text-right">
                                    {formatTokens(task.total_tokens)}
                                </span>
                                <span className="text-sm font-medium tabular-nums shrink-0 w-16 text-right">
                                    {formatCost(task.cost)}
                                </span>
                            </button>
                        ))}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
