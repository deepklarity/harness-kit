import { useState, useEffect } from 'react';
import { Timer, Pause } from 'lucide-react';

interface CountdownTimerProps {
    remainingMs: number;
    isRunning: boolean;
}

export function CountdownTimer({ remainingMs, isRunning }: CountdownTimerProps) {
    const [timeLeft, setTimeLeft] = useState(remainingMs);

    useEffect(() => {
        setTimeLeft(remainingMs);
    }, [remainingMs]);

    useEffect(() => {
        if (!isRunning || timeLeft <= 0) return;

        const interval = setInterval(() => {
            setTimeLeft(prev => Math.max(0, prev - 1000));
        }, 1000);

        return () => clearInterval(interval);
    }, [isRunning, timeLeft]);

    if (timeLeft === undefined) return null;

    const isOverdue = timeLeft <= 0;
    const totalSeconds = Math.floor(Math.abs(timeLeft) / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    const format = (n: number) => n.toString().padStart(2, '0');

    return (
        <div
            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-bold border transition-all ${
                isOverdue
                    ? 'bg-red-500/10 text-red-400 border-red-500/20'
                    : !isRunning
                        ? 'bg-white/5 text-muted-foreground border-border'
                        : 'bg-primary/10 text-primary border-primary/20'
            }`}
            title={!isRunning ? 'Timer paused (not in progress)' : isOverdue ? 'Budget exceeded' : 'Remaining budget'}
        >
            {isRunning ? <Timer className="size-3" /> : <Pause className="size-3" />}
            <span className="font-mono">
                {isOverdue ? '-' : ''}{hours}:{format(minutes)}:{format(seconds)}
            </span>
        </div>
    );
}
