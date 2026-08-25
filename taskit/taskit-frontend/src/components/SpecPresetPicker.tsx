import { useState, useMemo, useCallback } from 'react';
import type { SpecPreset, PresetCategory } from '../types';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Sparkles, Search } from 'lucide-react';

interface SpecPresetPickerProps {
    presets: SpecPreset[];
    categories: PresetCategory[];
    onSelect: (preset: SpecPreset) => void;
}

const CATEGORY_COLORS: Record<string, string> = {
    'code-quality': '#6366f1',
    'testing-discipline': '#06b6d4',
    'architecture-domain': '#22c55e',
    'planning-clarity': '#f97316',
    'custom-skills': '#3b82f6',
};

export function SpecPresetPicker({ presets, categories, onSelect }: SpecPresetPickerProps) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState('');

    const sortedCategories = useMemo(
        () => [...categories].sort((a, b) => a.sort_order - b.sort_order),
        [categories],
    );

    const filteredPresets = useMemo(() => {
        if (!search.trim()) return presets;
        const q = search.toLowerCase();
        return presets.filter(
            p => p.title.toLowerCase().includes(q) ||
                 p.description.toLowerCase().includes(q) ||
                 categories.find(c => c.slug === p.category)?.name.toLowerCase().includes(q),
        );
    }, [presets, categories, search]);

    const presetsByCategory = useMemo(() => {
        const map = new Map<string, SpecPreset[]>();
        for (const p of filteredPresets) {
            const arr = map.get(p.category) || [];
            arr.push(p);
            map.set(p.category, arr);
        }
        for (const [key, arr] of map) {
            map.set(key, arr.sort((a, b) => a.sort_order - b.sort_order));
        }
        return map;
    }, [filteredPresets]);

    const stopScrollCapture = useCallback((e: React.UIEvent) => e.stopPropagation(), []);

    const handleSelect = (preset: SpecPreset) => {
        onSelect(preset);
        setOpen(false);
        setSearch('');
    };

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
                >
                    <Sparkles className="size-3 text-amber-500" />
                    Use preset
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[420px] p-0" align="end">
                <div className="p-2 border-b">
                    <div className="relative">
                        <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                        <Input
                            placeholder="Search skill directives..."
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            className="h-8 text-xs pl-7"
                        />
                    </div>
                </div>
                <div
                    className="max-h-[340px] overflow-y-auto p-1 overscroll-contain"
                    onWheel={stopScrollCapture}
                    onTouchMove={stopScrollCapture}
                >
                    {sortedCategories.map(cat => {
                        const catPresets = presetsByCategory.get(cat.slug);
                        if (!catPresets || catPresets.length === 0) return null;
                        return (
                            <div key={cat.slug} className="mb-1.5">
                                <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                                    <div
                                        className="size-2 rounded-full shrink-0"
                                        style={{ backgroundColor: CATEGORY_COLORS[cat.slug] || '#64748b' }}
                                    />
                                    {cat.name}
                                </div>
                                {catPresets.map(preset => (
                                    <button
                                        key={preset.id}
                                        type="button"
                                        className="w-full text-left px-2 py-1.5 rounded-md hover:bg-accent flex flex-col gap-0.5 transition-colors group"
                                        onClick={() => handleSelect(preset)}
                                    >
                                        <div className="flex items-center justify-between gap-1">
                                            <span className="text-xs font-medium group-hover:text-primary transition-colors">{preset.title}</span>
                                        </div>
                                        <span className="text-[11px] text-muted-foreground line-clamp-2">
                                            {preset.description}
                                        </span>
                                    </button>
                                ))}
                            </div>
                        );
                    })}
                    {filteredPresets.length === 0 && (
                        <div className="px-2 py-4 text-xs text-muted-foreground text-center">
                            No skill directives match your search.
                        </div>
                    )}
                </div>
            </PopoverContent>
        </Popover>
    );
}
