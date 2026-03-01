import type { Task } from '../types';
import { formatDuration, getStatusColor } from '../utils/transformer';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import { Timer } from 'lucide-react';

interface TaskTimeDisplayProps {
    task: Task;
    className?: string;
}

export function TaskTimeDisplay({ task, className = '' }: TaskTimeDisplayProps) {
    const displayMs = task.executingTimeMs > 0 ? task.executingTimeMs : task.workTimeMs;
    const label = task.executingTimeMs > 0 ? 'Executing time' : 'Work time (in progress + review)';

    const stagesWithTime = Object.entries(task.timeInStatuses)
        .filter(([, ms]) => ms > 0)
        .sort(([, a], [, b]) => b - a);

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <div className={`flex items-center gap-0.5 shrink-0 text-muted-foreground cursor-default ${className}`} title={label}>
                    <Timer className="size-3" />
                    <span className="font-mono text-[11px]">{formatDuration(displayMs)}</span>
                </div>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-[240px] p-3 space-y-2 text-left">
                <div className="space-y-1">
                    <div className="flex justify-between gap-4">
                        <span className="text-[11px] opacity-70">Total lifespan</span>
                        <span className="text-[11px] font-mono">{formatDuration(task.totalLifespanMs)}</span>
                    </div>
                    <div className="flex justify-between gap-4">
                        <span className="text-[11px] opacity-70">Work time</span>
                        <span className="text-[11px] font-mono">{formatDuration(task.workTimeMs)}</span>
                    </div>
                    {task.executingTimeMs > 0 && (
                        <div className="flex justify-between gap-4">
                            <span className="text-[11px] opacity-70">Executing</span>
                            <span className="text-[11px] font-mono font-semibold">{formatDuration(task.executingTimeMs)}</span>
                        </div>
                    )}
                </div>
                {stagesWithTime.length > 0 && (
                    <>
                        <div className="border-t border-current opacity-20" />
                        <div className="space-y-0.5">
                            {stagesWithTime.map(([status, ms]) => (
                                <div key={status} className="flex items-center justify-between gap-3">
                                    <div className="flex items-center gap-1.5">
                                        <span
                                            className="size-1.5 rounded-full shrink-0"
                                            style={{ background: getStatusColor(status) }}
                                        />
                                        <span className="text-[10px]">{status}</span>
                                    </div>
                                    <span className="text-[10px] font-mono opacity-80">{formatDuration(ms)}</span>
                                </div>
                            ))}
                        </div>
                    </>
                )}
            </TooltipContent>
        </Tooltip>
    );
}
