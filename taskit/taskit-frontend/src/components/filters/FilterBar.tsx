import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';

interface FilterBarProps {
    children: ReactNode;
    onClearAll: () => void;
    showClearAll?: boolean;
    resultCount?: number;
    resultLabel?: string;
    trailing?: ReactNode;
}

export function FilterBar({ children, onClearAll, showClearAll = true, resultCount, resultLabel, trailing }: FilterBarProps) {
    return (
        <div className="flex flex-wrap items-center gap-2 p-3 rounded-lg border border-border bg-card/50 mb-4">
            {/* Left side: Filters + Reset */}
            <div className="flex flex-wrap items-center gap-2 min-w-0">
                {children}
                {showClearAll && (
                    <Button variant="ghost" size="sm" onClick={onClearAll} aria-label="Reset filters" className="h-8 text-muted-foreground hover:text-foreground">
                        Reset
                    </Button>
                )}
            </div>

            {/* Right side: Results + Trailing content */}
            <div className="flex items-center gap-4 ml-auto">
                {resultCount != null && (
                    <span className="text-xs text-muted-foreground tabular-nums">
                        {resultCount} {resultLabel ?? (resultCount === 1 ? 'result' : 'results')}
                    </span>
                )}
                {trailing}
            </div>
        </div>
    );
}
