import { useState, useMemo, useCallback } from 'react';
import type { TaskPreset, PresetCategory } from '../types';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { BookTemplate, X, Search } from 'lucide-react';

interface PresetPickerProps {
    presets: TaskPreset[];
    categories: PresetCategory[];
    selectedPreset: TaskPreset | null;
    onSelect: (preset: TaskPreset) => void;
    onClear: () => void;
}

const CATEGORY_COLORS: Record<string, string> = {
    'code-review': '#6366f1',
    'ui-ux-audit': '#06b6d4',
    'documentation': '#22c55e',
    'analysis': '#f97316',
    'quality-process': '#8b5cf6',
    'development': '#ec4899',
};

export function PresetPicker({ presets, categories, selectedPreset, onSelect, onClear }: PresetPickerProps) {
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
                 categories.find(c => c.slug === p.category)?.name.toLowerCase().includes(q),
        );
    }, [presets, categories, search]);

    const presetsByCategory = useMemo(() => {
        const map = new Map<string, TaskPreset[]>();
        for (const p of filteredPresets) {
            const arr = map.get(p.category) || [];
            arr.push(p);
            map.set(p.category, arr);
        }
        // Sort presets within each category
        for (const [key, arr] of map) {
            map.set(key, arr.sort((a, b) => a.sort_order - b.sort_order));
        }
        return map;
    }, [filteredPresets]);

    // Prevent Dialog's react-remove-scroll from swallowing scroll events
    // on this portaled Popover content (it's outside the Dialog content tree).
    const stopScrollCapture = useCallback((e: React.UIEvent) => e.stopPropagation(), []);

    const handleSelect = (preset: TaskPreset) => {
        onSelect(preset);
        setOpen(false);
        setSearch('');
    };

    if (selectedPreset) {
        return (
            <div className="flex items-center gap-2">
                <Badge
                    variant="secondary"
                    className="gap-1.5 pr-1 text-xs font-normal max-w-full"
                    style={{ borderLeft: `3px solid ${CATEGORY_COLORS[selectedPreset.category] || '#64748b'}` }}
                >
                    <BookTemplate className="size-3 shrink-0" />
                    <span className="truncate">{selectedPreset.title}</span>
                    <button
                        type="button"
                        onClick={onClear}
                        className="ml-0.5 hover:opacity-70 shrink-0"
                    >
                        <X className="size-3" />
                    </button>
                </Badge>
            </div>
        );
    }

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="w-full justify-start text-muted-foreground font-normal h-8 text-xs"
                >
                    <BookTemplate className="size-3.5 mr-2 shrink-0" />
                    Use a preset...
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[400px] p-0" align="start">
                <div className="p-2 border-b">
                    <div className="relative">
                        <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                        <Input
                            placeholder="Search presets..."
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            className="h-8 text-xs pl-7"
                        />
                    </div>
                </div>
                <div
                    className="max-h-[320px] overflow-y-auto p-1 overscroll-contain"
                    onWheel={stopScrollCapture}
                    onTouchMove={stopScrollCapture}
                >
                    {sortedCategories.map(cat => {
                        const catPresets = presetsByCategory.get(cat.slug);
                        if (!catPresets || catPresets.length === 0) return null;
                        return (
                            <div key={cat.slug} className="mb-1">
                                <div className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
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
                                        className="w-full text-left px-2 py-1.5 rounded-md hover:bg-accent flex flex-col gap-0.5 transition-colors"
                                        onClick={() => handleSelect(preset)}
                                    >
                                        <span className="text-xs font-medium">{preset.title}</span>
                                        <span className="text-[10px] text-muted-foreground line-clamp-1">
                                            {preset.description.substring(0, 100)}...
                                        </span>
                                    </button>
                                ))}
                            </div>
                        );
                    })}
                    {filteredPresets.length === 0 && (
                        <div className="px-2 py-4 text-xs text-muted-foreground text-center">
                            No presets match your search.
                        </div>
                    )}
                </div>
            </PopoverContent>
        </Popover>
    );
}
