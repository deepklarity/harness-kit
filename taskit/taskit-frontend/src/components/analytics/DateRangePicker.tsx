import { Button } from '@/components/ui/button';
import { Calendar } from 'lucide-react';

interface DateRangePickerProps {
    dateFrom: string;
    dateTo: string;
    granularity: string;
    onDateFromChange: (value: string) => void;
    onDateToChange: (value: string) => void;
    onGranularityChange: (value: string) => void;
    onDateRangeChange?: (from: string, to: string) => void;
}

const PRESETS = [
    { label: '7d', days: 7 },
    { label: '30d', days: 30 },
    { label: '90d', days: 90 },
    { label: 'All', days: 0 },
];

const GRANULARITIES = [
    { label: 'Day', value: 'day' },
    { label: 'Week', value: 'week' },
    { label: 'Month', value: 'month' },
];

export function DateRangePicker({
    dateFrom, dateTo, granularity,
    onDateFromChange, onDateToChange, onGranularityChange,
    onDateRangeChange,
}: DateRangePickerProps) {
    const applyPreset = (days: number) => {
        if (days === 0) {
            if (onDateRangeChange) {
                onDateRangeChange('', '');
            } else {
                onDateFromChange('');
                onDateToChange('');
            }
            return;
        }
        const to = new Date();
        const from = new Date();
        from.setDate(from.getDate() - days);
        const fromStr = from.toISOString().slice(0, 10);
        const toStr = to.toISOString().slice(0, 10);
        if (onDateRangeChange) {
            onDateRangeChange(fromStr, toStr);
        } else {
            onDateFromChange(fromStr);
            onDateToChange(toStr);
        }
    };

    const activePreset = PRESETS.find(p => {
        if (p.days === 0) return !dateFrom && !dateTo;
        if (!dateFrom) return false;
        const from = new Date(dateFrom);
        const diff = Math.round((Date.now() - from.getTime()) / (1000 * 60 * 60 * 24));
        return Math.abs(diff - p.days) <= 1;
    });

    return (
        <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5">
                <Calendar className="size-4 text-muted-foreground" />
                <span className="text-sm font-medium text-muted-foreground">Range:</span>
                {PRESETS.map(p => (
                    <Button
                        key={p.label}
                        variant={activePreset?.label === p.label ? 'default' : 'outline'}
                        size="sm"
                        className="h-7 px-2.5 text-xs"
                        onClick={() => applyPreset(p.days)}
                    >
                        {p.label}
                    </Button>
                ))}
            </div>

            <div className="flex items-center gap-1.5">
                <input
                    type="date"
                    value={dateFrom}
                    onChange={e => onDateFromChange(e.target.value)}
                    className="h-7 px-2 text-xs border border-input rounded-md bg-background text-foreground"
                />
                <span className="text-xs text-muted-foreground">to</span>
                <input
                    type="date"
                    value={dateTo}
                    onChange={e => onDateToChange(e.target.value)}
                    className="h-7 px-2 text-xs border border-input rounded-md bg-background text-foreground"
                />
            </div>

            <div className="flex items-center gap-1.5 ml-auto">
                <span className="text-sm font-medium text-muted-foreground">Group:</span>
                {GRANULARITIES.map(g => (
                    <Button
                        key={g.value}
                        variant={granularity === g.value ? 'default' : 'outline'}
                        size="sm"
                        className="h-7 px-2.5 text-xs"
                        onClick={() => onGranularityChange(g.value)}
                    >
                        {g.label}
                    </Button>
                ))}
            </div>
        </div>
    );
}
