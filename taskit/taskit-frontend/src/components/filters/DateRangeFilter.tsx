import { useCallback, useMemo, useState } from 'react';
import { format, subDays, startOfMonth, endOfMonth, subMonths, parse, isValid } from 'date-fns';
import { CalendarIcon, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Calendar } from '@/components/ui/calendar';
import { Separator } from '@/components/ui/separator';

interface DateRangeFilterProps {
    label: string;
    from?: string;
    to?: string;
    onChange: (from: string, to: string) => void;
}

interface Preset {
    key: string;
    label: string;
    triggerLabel: string;
    from: string;
    to: string;
}

const toDateStr = (date: Date): string => format(date, 'yyyy-MM-dd');

const parseDateStr = (value: string | undefined): Date | undefined => {
    if (!value) return undefined;
    const d = parse(value, 'yyyy-MM-dd', new Date());
    return isValid(d) ? d : undefined;
};

function buildPresets(today: Date): Preset[] {
    const todayStr = toDateStr(today);
    return [
        { key: 'last_7', label: '7 days', triggerLabel: 'Last 7 days', from: toDateStr(subDays(today, 6)), to: todayStr },
        { key: 'last_15', label: '15 days', triggerLabel: 'Last 15 days', from: toDateStr(subDays(today, 14)), to: todayStr },
        { key: 'last_30', label: '30 days', triggerLabel: 'Last 30 days', from: toDateStr(subDays(today, 29)), to: todayStr },
        { key: 'this_month', label: 'This month', triggerLabel: 'This month', from: toDateStr(startOfMonth(today)), to: todayStr },
        { key: 'last_month', label: 'Last month', triggerLabel: 'Last month', from: toDateStr(startOfMonth(subMonths(today, 1))), to: toDateStr(endOfMonth(subMonths(today, 1))) },
    ];
}

export function DateRangeFilter({ label, from, to, onChange }: DateRangeFilterProps) {
    const [open, setOpen] = useState(false);
    const [draftFrom, setDraftFrom] = useState<Date | undefined>(undefined);
    const [draftTo, setDraftTo] = useState<Date | undefined>(undefined);
    const [pickingField, setPickingField] = useState<'from' | 'to' | null>(null);

    const today = useMemo(() => new Date(), []);
    const presets = useMemo(() => buildPresets(today), [today]);

    const activePreset = useMemo(() => {
        if (!from && !to) return null;
        return presets.find(p => p.from === from && p.to === to) ?? null;
    }, [from, to, presets]);

    const hasActiveFilter = !!(from || to);

    const triggerLabel = useMemo(() => {
        if (!from && !to) return 'All time';
        if (activePreset) return activePreset.triggerLabel;
        const parts: string[] = [];
        const fromDate = parseDateStr(from);
        const toDate = parseDateStr(to);
        if (fromDate) parts.push(format(fromDate, 'MMM d'));
        if (toDate) parts.push(format(toDate, 'MMM d'));
        return parts.join(' – ') || 'All time';
    }, [from, to, activePreset]);

    const applyPreset = useCallback((preset: Preset) => {
        onChange(preset.from, preset.to);
        setOpen(false);
    }, [onChange]);

    const clearAll = useCallback(() => {
        onChange('', '');
        setDraftFrom(undefined);
        setDraftTo(undefined);
        setPickingField(null);
        setOpen(false);
    }, [onChange]);

    const handleOpenChange = useCallback((nextOpen: boolean) => {
        setOpen(nextOpen);
        if (nextOpen) {
            setDraftFrom(parseDateStr(from));
            setDraftTo(parseDateStr(to));
            setPickingField(null);
        }
    }, [from, to]);

    const handleCalendarSelect = useCallback((date: Date | undefined) => {
        if (!date) return;
        if (pickingField === 'from') {
            setDraftFrom(date);
            if (draftTo && date > draftTo) setDraftTo(undefined);
            setPickingField('to');
        } else if (pickingField === 'to') {
            if (draftFrom && date < draftFrom) {
                setDraftFrom(date);
                setDraftTo(undefined);
                setPickingField('to');
            } else {
                setDraftTo(date);
                setPickingField(null);
            }
        } else {
            setDraftFrom(date);
            setPickingField('to');
        }
    }, [pickingField, draftFrom, draftTo]);

    const applyCustom = useCallback(() => {
        onChange(draftFrom ? toDateStr(draftFrom) : '', draftTo ? toDateStr(draftTo) : '');
        setPickingField(null);
        setOpen(false);
    }, [draftFrom, draftTo, onChange]);

    const canApply = !!(draftFrom && draftTo);

    return (
        <Popover open={open} onOpenChange={handleOpenChange}>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    size="sm"
                    className={`gap-1.5 ${hasActiveFilter ? 'border-primary/50 bg-primary/5' : ''}`}
                    aria-label={`${label} date range filter`}
                >
                    <CalendarIcon className="size-3.5 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground mr-0.5">{label}</span>
                    <span className="text-sm">{triggerLabel}</span>
                    <ChevronDown className="size-3.5 text-muted-foreground" />
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start">
                <div className="p-3 space-y-3">
                    {/* Quick presets */}
                    <div className="flex flex-wrap gap-1.5">
                        <Button
                            size="xs"
                            variant={!hasActiveFilter ? 'default' : 'outline'}
                            onClick={clearAll}
                        >
                            All
                        </Button>
                        {presets.map(p => (
                            <Button
                                key={p.key}
                                size="xs"
                                variant={activePreset?.key === p.key ? 'default' : 'outline'}
                                onClick={() => applyPreset(p)}
                            >
                                {p.label}
                            </Button>
                        ))}
                    </div>

                    <Separator />

                    {/* Custom range inputs */}
                    <div className="space-y-2">
                        <p className="text-xs text-muted-foreground font-medium">Custom range</p>
                        <div className="flex items-center gap-2">
                            <button
                                type="button"
                                onClick={() => setPickingField('from')}
                                className={`flex-1 h-8 px-2 rounded-md border text-sm text-left ${
                                    pickingField === 'from'
                                        ? 'border-primary ring-1 ring-primary/30'
                                        : 'border-border'
                                } bg-background hover:bg-muted/50 transition-colors`}
                            >
                                {draftFrom ? format(draftFrom, 'MMM d, yyyy') : (
                                    <span className="text-muted-foreground">Start date</span>
                                )}
                            </button>
                            <span className="text-xs text-muted-foreground">–</span>
                            <button
                                type="button"
                                onClick={() => setPickingField('to')}
                                className={`flex-1 h-8 px-2 rounded-md border text-sm text-left ${
                                    pickingField === 'to'
                                        ? 'border-primary ring-1 ring-primary/30'
                                        : 'border-border'
                                } bg-background hover:bg-muted/50 transition-colors`}
                            >
                                {draftTo ? format(draftTo, 'MMM d, yyyy') : (
                                    <span className="text-muted-foreground">End date</span>
                                )}
                            </button>
                        </div>
                    </div>

                    {/* Calendar */}
                    {pickingField && (
                        <Calendar
                            mode="single"
                            selected={pickingField === 'from' ? draftFrom : draftTo}
                            onSelect={handleCalendarSelect}
                            disabled={{ after: today }}
                            defaultMonth={
                                pickingField === 'to' && draftFrom
                                    ? draftFrom
                                    : pickingField === 'from' && draftTo
                                        ? draftTo
                                        : today
                            }
                            modifiers={{
                                range_start: draftFrom ? [draftFrom] : [],
                                range_end: draftTo ? [draftTo] : [],
                                range_middle: draftFrom && draftTo
                                    ? [{ after: draftFrom, before: draftTo }]
                                    : [],
                            }}
                        />
                    )}

                    <Separator />

                    {/* Actions */}
                    <div className="flex justify-end gap-2">
                        <Button size="xs" variant="ghost" onClick={clearAll}>
                            Clear
                        </Button>
                        <Button size="xs" onClick={applyCustom} disabled={!canApply}>
                            Apply
                        </Button>
                    </div>
                </div>
            </PopoverContent>
        </Popover>
    );
}
