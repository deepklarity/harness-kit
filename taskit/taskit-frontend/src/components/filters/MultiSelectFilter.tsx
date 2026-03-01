import { useMemo } from 'react';
import { ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

interface Option {
    label: string;
    value: string;
}

interface MultiSelectFilterProps {
    label: string;
    options: Option[];
    selected: string[];
    onChange: (next: string[]) => void;
}

export function MultiSelectFilter({ label, options, selected, onChange }: MultiSelectFilterProps) {
    const selectedSet = useMemo(() => new Set(selected), [selected]);

    const toggle = (value: string) => {
        const next = new Set(selectedSet);
        if (next.has(value)) next.delete(value);
        else next.add(value);
        onChange(Array.from(next));
    };

    return (
        <Popover>
            <PopoverTrigger asChild>
                <Button variant="outline" className="gap-1.5" aria-label={`${label} filter`}>
                    {label}
                    {selected.length > 0 && <span className="text-xs text-muted-foreground">({selected.length})</span>}
                    <ChevronDown className="size-3.5 text-muted-foreground" />
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-56 p-2" align="start">
                <div className="space-y-1 max-h-60 overflow-auto" role="listbox" aria-label={label}>
                    {options.map(option => (
                        <label key={option.value} className="flex items-center gap-2 px-2 py-1 rounded hover:bg-muted/60 cursor-pointer">
                            <Checkbox
                                checked={selectedSet.has(option.value)}
                                onCheckedChange={() => toggle(option.value)}
                                aria-label={option.label}
                            />
                            <span className="text-sm">{option.label}</span>
                        </label>
                    ))}
                    {options.length === 0 && (
                        <div className="text-xs text-muted-foreground px-2 py-1">No options</div>
                    )}
                </div>
            </PopoverContent>
        </Popover>
    );
}
