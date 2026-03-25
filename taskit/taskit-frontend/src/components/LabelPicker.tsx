import { useState, useMemo } from 'react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Check, Plus, X, Search } from 'lucide-react';
import type { Label as LabelType } from '@/types';
import { useService } from '@/contexts/ServiceContext';

const LABEL_COLORS = [
    '#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4',
    '#3b82f6', '#8b5cf6', '#ec4899', '#64748b',
];

interface LabelPickerProps {
    boardId?: string;
    selectedLabelIds: number[];
    allLabels: LabelType[];
    onLabelsChange: (labels: LabelType[]) => void;
    onSelectionChange: (selectedIds: number[]) => void;
    readonly?: boolean;
}

export function LabelPicker({ boardId, selectedLabelIds, allLabels, onLabelsChange, onSelectionChange, readonly }: LabelPickerProps) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState('');
    const [newColor, setNewColor] = useState(LABEL_COLORS[0]);
    const service = useService();

    // Filter labels by search term
    const filteredLabels = useMemo(() => {
        if (!search.trim()) return allLabels;
        return allLabels.filter(l => l.name.toLowerCase().includes(search.toLowerCase()));
    }, [allLabels, search]);

    const hasExactMatch = useMemo(() => {
        return allLabels.some(l => l.name.toLowerCase() === search.trim().toLowerCase());
    }, [allLabels, search]);

    const toggleLabel = (id: number) => {
        if (selectedLabelIds.includes(id)) {
            onSelectionChange(selectedLabelIds.filter(x => x !== id));
        } else {
            onSelectionChange([...selectedLabelIds, id]);
        }
    };

    const handleCreate = async () => {
        const name = search.trim();
        if (!name) return;
        try {
            const label = await service.createLabel(name, newColor, boardId);
            onLabelsChange([...allLabels, label]);
            onSelectionChange([...selectedLabelIds, label.id]);
            setSearch('');
            setNewColor(LABEL_COLORS[0]);
        } catch (e) {
            console.error('Failed to create label', e);
        }
    };

    const selectedLabels = useMemo(() => {
        return selectedLabelIds.map(id => allLabels.find(l => l.id === id)).filter(Boolean) as LabelType[];
    }, [selectedLabelIds, allLabels]);

    return (
        <div className="flex flex-wrap gap-1.5 min-h-[32px] items-center">
            {selectedLabels.map(label => (
                <Badge key={label.id} className="gap-1 pr-1 text-xs text-white border-0" style={{ backgroundColor: label.color }}>
                    {label.name}
                    {!readonly && (
                        <button type="button" onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleLabel(label.id); }} className="hover:opacity-70">
                            <X className="size-3" />
                        </button>
                    )}
                </Badge>
            ))}
            {!readonly && (
                <Popover open={open} onOpenChange={setOpen}>
                    <PopoverTrigger asChild>
                        <Button type="button" variant="outline" size="sm" className="h-6 text-xs px-2">
                            <Plus className="size-3 mr-1" /> Add Labels
                        </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-64 p-2 relative" align="start">
                        <div className="relative mb-2">
                            <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground" />
                            <Input
                                placeholder="Search or create..."
                                value={search}
                                onChange={e => setSearch(e.target.value)}
                                className="pl-7 h-8 text-xs bg-background border-border shadow-none"
                                autoFocus
                            />
                        </div>

                        <div className="max-h-[140px] overflow-y-auto space-y-0.5">
                            {filteredLabels.map(label => {
                                const isSelected = selectedLabelIds.includes(label.id);
                                return (
                                    <div
                                        key={label.id}
                                        className="flex items-center gap-2 p-1.5 rounded hover:bg-secondary cursor-pointer transition-colors"
                                        onClick={(e) => { e.preventDefault(); toggleLabel(label.id); }}
                                    >
                                        <div className={`size-3.5 border rounded-sm flex items-center justify-center shrink-0 ${isSelected ? 'bg-primary border-primary text-primary-foreground' : 'border-input bg-background text-transparent'}`}>
                                            <Check className="size-2.5" />
                                        </div>
                                        <div className="h-3 w-6 rounded shrink-0" style={{ backgroundColor: label.color }} />
                                        <span className="text-xs truncate flex-1">{label.name}</span>
                                    </div>
                                );
                            })}
                        </div>

                        {search.trim() && !hasExactMatch && (
                            <div className="mt-2 pt-2 border-t border-border/50">
                                <div className="flex gap-1 flex-wrap mb-2">
                                    {LABEL_COLORS.map(c => (
                                        <div key={c}
                                            className={`size-4 rounded-full cursor-pointer ${newColor === c ? 'ring-2 ring-primary ring-offset-1 ring-offset-background' : ''}`}
                                            style={{ backgroundColor: c }}
                                            onClick={(e) => { e.preventDefault(); setNewColor(c); }}
                                        />
                                    ))}
                                </div>
                                <Button type="button" size="sm" className="w-full h-7 text-xs" onClick={(e) => { e.preventDefault(); handleCreate(); }}>
                                    Create &quot;{search.trim()}&quot;
                                </Button>
                            </div>
                        )}
                        {filteredLabels.length === 0 && !search.trim() && (
                            <div className="text-xs text-muted-foreground p-2 text-center">No labels yet</div>
                        )}
                    </PopoverContent>
                </Popover>
            )}
        </div>
    );
}
