import { useCallback, useState } from 'react'
import { usePolling } from '@/hooks/usePolling'
import type { ExecutorCapacity, MemoryShareHolder } from '@/types'
import { Cpu, Loader2 } from 'lucide-react'

interface CapacityBadgeProps {
    service: {
        fetchExecutorCapacity(): Promise<ExecutorCapacity>
    }
    intervalMs?: number
    className?: string
}

/** Compose the operator-facing "X+Y/Z" line — task #353. */
function badgeDisplay(capacity: ExecutorCapacity): string {
    const executing = capacity.executing ?? capacity.running
    const reflecting = capacity.reflecting ?? 0
    const inUse = capacity.shares_in_use ?? executing + reflecting
    const max =
        capacity.memory_max_shares && capacity.memory_max_shares > 0
            ? capacity.memory_max_shares
            : capacity.max
    if (reflecting === 0) {
        return `${inUse}/${max}`
    }
    return `${executing}+${reflecting}/${max}`
}

/** Build the tooltip that names each share-holder — the operator signal
 * the badge must give at a glance: "3 executing + 1 review = 4/4 memory
 * shares — held by task 344, reflection on 346". */
function badgeTooltip(capacity: ExecutorCapacity): string {
    const executing = capacity.executing ?? capacity.running
    const reflecting = capacity.reflecting ?? 0
    const inUse = capacity.shares_in_use ?? executing + reflecting
    const max =
        capacity.memory_max_shares && capacity.memory_max_shares > 0
            ? capacity.memory_max_shares
            : capacity.max
    const breakdown =
        reflecting > 0
            ? `${executing} executing + ${reflecting} review = ${inUse}/${max} memory shares`
            : `Sandbox capacity: ${inUse} running of ${max} max.`
    const holders = capacity.memory_share_holders ?? []
    if (holders.length === 0) return breakdown
    const lines = holders.slice(0, 5).map(holderLabel)
    const overflow = holders.length > 5 ? `\n…and ${holders.length - 5} more` : ''
    return `${breakdown}\n— held by —\n${lines.join('\n')}${overflow}`
}

function holderLabel(h: MemoryShareHolder): string {
    if (h.kind === 'reflection') return `reflection on ${h.task_id} — ${h.task_title}`
    return `task ${h.task_id} — ${h.task_title}`
}

export function CapacityBadge({ service, intervalMs, className }: CapacityBadgeProps) {
    const [capacity, setCapacity] = useState<ExecutorCapacity | null>(null)

    const load = useCallback(async () => {
        try {
            const data = await service.fetchExecutorCapacity()
            setCapacity(data)
        } catch {
            // swallow — keep last value visible
        }
    }, [service])

    usePolling(load, {
        intervalMs: intervalMs ?? Number(import.meta.env.VITE_POLL_INTERVAL_MS || 15000),
        immediate: true,
    })

    const inUse = capacity ? (capacity.shares_in_use ?? capacity.running) : 0
    const max = capacity
        ? (capacity.memory_max_shares && capacity.memory_max_shares > 0
              ? capacity.memory_max_shares
              : capacity.max)
        : 0
    const state: 'idle' | 'partial' | 'saturated' | 'unknown' =
        !capacity
            ? 'unknown'
            : inUse >= max && max > 0
                ? 'saturated'
                : inUse > 0
                    ? 'partial'
                    : 'idle'

    const display = capacity ? badgeDisplay(capacity) : '—/—'
    const tooltip = capacity ? badgeTooltip(capacity) : 'Loading sandbox capacity...'

    return (
        <div
            data-capacity-state={state}
            title={tooltip}
            aria-label={tooltip}
            className={[
                'inline-flex items-center gap-1.5 h-8 px-2.5 rounded-md border text-xs font-medium transition-colors',
                state === 'saturated'
                    ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'
                    : state === 'partial'
                        ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                        : state === 'idle'
                            ? 'border-border bg-muted/40 text-muted-foreground'
                            : 'border-border bg-muted/20 text-muted-foreground/70',
                className ?? '',
            ].join(' ')}
        >
            {capacity ? <Cpu className="size-3.5" /> : <Loader2 className="size-3.5 animate-spin" />}
            <span className="font-mono tabular-nums">{display}</span>
        </div>
    )
}