import { useMemo } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

interface SortOption {
    label: string;
    value: string;
}

interface SortControlProps {
    value?: string;
    options: SortOption[];
    onChange: (value?: string) => void;
}

export function SortControl({ value, options, onChange }: SortControlProps) {
    const token = useMemo(() => {
        if (!value) return '';
        return value.split(',').map(t => t.trim()).filter(Boolean)[0] || '';
    }, [value]);

    const field = token.replace('-', '') || options[0]?.value || '';
    const direction: 'asc' | 'desc' = token.startsWith('-') ? 'desc' : 'asc';

    const applySort = (nextField: string, nextDirection: 'asc' | 'desc') => {
        if (!nextField) {
            onChange(undefined);
            return;
        }
        onChange(nextDirection === 'desc' ? `-${nextField}` : nextField);
    };

    return (
        <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Sort</span>
            <Select value={field} onValueChange={(nextField) => applySort(nextField, direction)}>
                <SelectTrigger className="w-[150px] h-8" aria-label="Sort field">
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    {options.map(option => (
                        <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                    ))}
                </SelectContent>
            </Select>
            <Select value={direction} onValueChange={(v) => applySort(field, v as 'asc' | 'desc')}>
                <SelectTrigger className="w-[100px] h-8" aria-label="Sort direction">
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value="asc">Asc</SelectItem>
                    <SelectItem value="desc">Desc</SelectItem>
                </SelectContent>
            </Select>
            {value && (
                <Button size="sm" variant="outline" className="h-8" onClick={() => onChange(undefined)} aria-label="Clear sort">
                    <X className="size-3.5" />
                </Button>
            )}
        </div>
    );
}
