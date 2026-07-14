import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Loader2, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useToast } from '@/hooks/use-toast'
import type { ExecutorMaxConcurrency } from '@/types'

interface ExecutorCapacitySettingsProps {
    service: {
        fetchExecutorMaxConcurrency(): Promise<ExecutorMaxConcurrency>
        setExecutorMaxConcurrency(value: number): Promise<ExecutorMaxConcurrency>
    }
}

export function ExecutorCapacitySettings({ service }: ExecutorCapacitySettingsProps) {
    const { toast } = useToast()
    const [current, setCurrent] = useState<ExecutorMaxConcurrency | null>(null)
    const [draft, setDraft] = useState<string>('')
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [validationError, setValidationError] = useState<string | null>(null)

    const load = useCallback(async () => {
        setLoading(true)
        try {
            const data = await service.fetchExecutorMaxConcurrency()
            setCurrent(data)
            setDraft(String(data.value))
        } catch (e) {
            toast({
                title: 'Failed to load sandbox capacity',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            })
        } finally {
            setLoading(false)
        }
    }, [service, toast])

    useEffect(() => {
        void load()
    }, [load])

    const parsed = draft.trim() === '' ? NaN : Number(draft)
    const isValidNumber = Number.isFinite(parsed) && Number.isInteger(parsed)
    const belowMin = isValidNumber && parsed < 1
    const aboveSuggestion =
        isValidNumber && current !== null && parsed > current.suggested_max

    const showError = validationError !== null
    const errorMessage = validationError
        ?? (belowMin ? 'Max concurrency must be at least 1.' : null)

    const handleSave = async () => {
        if (belowMin) {
            setValidationError('Max concurrency must be at least 1.')
            return
        }
        if (!isValidNumber) {
            setValidationError('Enter a whole number.')
            return
        }
        setValidationError(null)
        setSaving(true)
        try {
            const updated = await service.setExecutorMaxConcurrency(parsed)
            setCurrent(updated)
            setDraft(String(updated.value))
            toast({
                title: 'Sandbox capacity updated',
                description: `New max: ${updated.value}.`,
            })
        } catch (e) {
            toast({
                title: 'Failed to save',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            })
        } finally {
            setSaving(false)
        }
    }

    const handleReset = () => {
        setValidationError(null)
        if (current) setDraft(String(current.value))
    }

    if (loading || !current) {
        return (
            <div className="text-xs text-muted-foreground flex items-center gap-2 py-3">
                <Loader2 className="size-3.5 animate-spin" />
                Loading sandbox capacity settings...
            </div>
        )
    }

    return (
        <div className="space-y-3">
            <div className="flex items-center gap-3">
                <label htmlFor="max-concurrency-input" className="text-sm text-muted-foreground shrink-0">
                    Max concurrent sandboxes
                </label>
                <Input
                    id="max-concurrency-input"
                    type="number"
                    min={1}
                    step={1}
                    inputMode="numeric"
                    value={draft}
                    onChange={e => {
                        setDraft(e.target.value)
                        setValidationError(null)
                    }}
                    aria-invalid={showError}
                    aria-describedby="max-concurrency-help"
                    className="h-8 w-24 font-mono text-xs"
                    disabled={saving}
                />
                <Button
                    type="button"
                    size="sm"
                    className="h-8 gap-1.5"
                    onClick={handleSave}
                    disabled={saving || !isValidNumber || belowMin}
                >
                    {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
                    Save
                </Button>
                {draft !== String(current.value) && (
                    <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-8"
                        onClick={handleReset}
                        disabled={saving}
                    >
                        Reset
                    </Button>
                )}
            </div>

            <p id="max-concurrency-help" className="text-xs text-muted-foreground">
                The DAG executor reads this value on every poll cycle, so changes take effect
                without restarting the worker. Rough sizing: ~4 GB RAM per sandbox on this host.
                {current.suggested_max > 0 && (
                    <> Suggested for current hardware: <span className="font-mono">{current.suggested_max}</span>.</>
                )}
            </p>

            {aboveSuggestion && (
                <div
                    role="alert"
                    className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300"
                >
                    <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
                    <div>
                        <div className="font-medium">
                            Above the suggested {current.suggested_max}
                        </div>
                        <div className="text-amber-700/80 dark:text-amber-300/80">
                            Each sandbox uses roughly 4 GB of RAM. Going above the suggestion may
                            cause swap, OOM kills, or queueing. You can still save — this is a warning, not a block.
                        </div>
                    </div>
                </div>
            )}

            {errorMessage && (
                <p role="alert" className="text-xs text-destructive">
                    {errorMessage}
                </p>
            )}
        </div>
    )
}