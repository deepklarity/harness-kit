import { useCallback, useState } from 'react'
import { usePolling } from '@/hooks/usePolling'
import type { ExecutorCapacity } from '@/types'
import { Cpu, Loader2 } from 'lucide-react'

interface CapacityBadgeProps {
    service: {
        fetchExecutorCapacity(): Promise<ExecutorCapacity>
    }
    intervalMs?: number
    className?: string
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

    const state: 'idle' | 'partial' | 'saturated' | 'unknown' =
        !capacity
            ? 'unknown'
            : capacity.running >= capacity.max
                ? 'saturated'
                : capacity.running > 0
                    ? 'partial'
                    : 'idle'

    const display = capacity ? `${capacity.running}/${capacity.max}` : '—/—'
    const tooltip = capacity
        ? `Sandbox capacity: ${capacity.running} running of ${capacity.max} max.`
        : 'Loading sandbox capacity...'

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