import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';

interface FilterBarProps {
    children: ReactNode;
    onClearAll: () => void;
    resultCount?: number;
    resultLabel?: string;
    trailing?: ReactNode;
}

export function FilterBar({ children, onClearAll, resultCount, resultLabel, trailing }: FilterBarProps) {
    return (
        <div className="flex flex-wrap items-center gap-2 p-3 rounded-lg border border-border bg-card/50 mb-4">
            <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0">
                {children}
            </div>
            {resultCount != null && (
                <span className="text-xs text-muted-foreground tabular-nums">
                    {resultCount} {resultLabel ?? (resultCount === 1 ? 'result' : 'results')}
                </span>
            )}
            <Button variant="ghost" size="sm" onClick={onClearAll} aria-label="Clear all filters">
                Clear All
            </Button>
            {trailing}
        </div>
    );
}
