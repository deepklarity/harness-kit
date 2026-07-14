import { AlertOctagon, PauseCircle } from 'lucide-react';
import type { DispatchBlockInfo } from './dispatchBlock';

const STYLE_BY_SEVERITY = {
    warning: {
        container: 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300',
        icon: 'text-amber-500',
    },
    blocked: {
        container: 'border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300',
        icon: 'text-red-500',
    },
} as const;

interface DispatchBlockBannerProps {
    info: DispatchBlockInfo;
    compact?: boolean;
    testId?: string;
}

/**
 * Visible banner for `metadata.dispatch_blocked_reason`. The reason is the
 * "loud skip" stamp the backend leaves when the dispatch guardrail decides
 * not to run the task (F43/F44); without this the operator sees a stuck
 * TODO/IN_PROGRESS task with no explanation.
 */
export function DispatchBlockBanner({ info, compact, testId = 'dispatch-blocked-banner' }: DispatchBlockBannerProps) {
    const style = STYLE_BY_SEVERITY[info.severity];
    const Icon = info.severity === 'blocked' ? AlertOctagon : PauseCircle;

    return (
        <div
            data-testid={testId}
            role="status"
            className={`flex items-start gap-1.5 rounded-md border px-2 py-1.5 ${style.container} ${
                compact ? 'text-[10px] leading-snug' : 'text-xs leading-snug'
            }`}
            title={info.code}
        >
            <Icon className={`size-3 shrink-0 mt-0.5 ${style.icon}`} aria-hidden="true" />
            <span className="flex-1 min-w-0 font-medium">{info.message}</span>
            {!compact && (
                <span className="text-[9px] font-mono opacity-60 shrink-0 mt-0.5">{info.code}</span>
            )}
        </div>
    );
}
