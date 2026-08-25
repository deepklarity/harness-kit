import { useEffect, useRef, useState, useCallback } from 'react';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useService } from '../contexts/ServiceContext';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import type { DirectoryEntry } from '../services/integration/IntegrationService';
import { Folder, Loader2 } from 'lucide-react';

interface DirectoryPickerProps {
    value: string;
    onChange: (newValue: string) => void;
    placeholder?: string;
    className?: string;
    onFocus?: () => void;
    onBlur?: () => void;
}

export function DirectoryPicker({
    value,
    onChange,
    placeholder = "/absolute/path/to/directory",
    className,
    onFocus,
    onBlur,
}: DirectoryPickerProps) {
    const service = useService();
    const containerRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    const [isFocused, setIsFocused] = useState(false);
    const [suggestions, setSuggestions] = useState<DirectoryEntry[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [activeIndex, setActiveIndex] = useState(-1);

    const debouncedValue = useDebouncedValue(value, 180);

    const fetchSuggestions = useCallback(async (pathQuery: string) => {
        const query = pathQuery.trim() || '~/';
        setIsLoading(true);
        try {
            const res = await service.suggestDirectories(query, 50);
            setSuggestions(res.entries || []);
        } catch {
            setSuggestions([]);
        } finally {
            setIsLoading(false);
        }
    }, [service]);

    // Fetch on initial mount or debounced value change
    useEffect(() => {
        let active = true;
        fetchSuggestions(debouncedValue).then(() => {
            if (active) setActiveIndex(-1);
        });
        return () => { active = false; };
    }, [debouncedValue, fetchSuggestions]);

    // Handle clicking outside
    useEffect(() => {
        const handleOutsideClick = (e: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
                setIsFocused(false);
                if (onBlur) onBlur();
            }
        };
        document.addEventListener('mousedown', handleOutsideClick);
        return () => document.removeEventListener('mousedown', handleOutsideClick);
    }, [onBlur]);

    // Live filter on current value
    const queryLower = value.trim().toLowerCase();
    const lastSlashIdx = queryLower.lastIndexOf('/');
    const leafQuery = lastSlashIdx >= 0 ? queryLower.slice(lastSlashIdx + 1) : queryLower;
    const filteredSuggestions = suggestions.filter(entry => {
        if (!queryLower || queryLower === '~/' || queryLower === '~') return true;
        const entryPathLower = entry.path.toLowerCase();
        const entryNameLower = entry.name.toLowerCase();
        return entryPathLower.includes(queryLower) || entryNameLower.includes(queryLower) || Boolean(leafQuery && entryNameLower.startsWith(leafQuery));
    });

    const handleSelectPath = (newPath: string) => {
        onChange(newPath);
        setIsFocused(true);
        inputRef.current?.focus();
        fetchSuggestions(newPath);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (!isFocused || filteredSuggestions.length === 0) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveIndex(prev => (prev + 1) % filteredSuggestions.length);
            return;
        }

        if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveIndex(prev => (prev <= 0 ? filteredSuggestions.length - 1 : prev - 1));
            return;
        }

        if (e.key === 'Enter' && activeIndex >= 0 && activeIndex < filteredSuggestions.length) {
            e.preventDefault();
            handleSelectPath(filteredSuggestions[activeIndex].path);
            return;
        }

        if (e.key === 'Escape') {
            setIsFocused(false);
            setActiveIndex(-1);
        }
    };

    const showPopover = isFocused && (filteredSuggestions.length > 0 || isLoading);

    return (
        <div ref={containerRef} className="flex flex-col relative w-full">
            <div className="relative flex items-center">
                <Folder className="size-4 absolute left-2.5 text-muted-foreground pointer-events-none" />
                <Input
                    ref={inputRef}
                    value={value}
                    onChange={e => {
                        onChange(e.target.value);
                        setIsFocused(true);
                    }}
                    onFocus={() => {
                        setIsFocused(true);
                        fetchSuggestions(value || '~/');
                        if (onFocus) onFocus();
                    }}
                    onKeyDown={handleKeyDown}
                    placeholder={placeholder}
                    className={`pl-8 font-mono text-xs ${className || ''}`}
                />
                {isLoading && (
                    <Loader2 className="size-3.5 absolute right-2.5 animate-spin text-muted-foreground" />
                )}
            </div>

            {showPopover && (
                <div className="absolute top-[40px] left-0 right-0 z-50 rounded-md border bg-popover text-popover-foreground shadow-lg overflow-hidden">
                    <ScrollArea className="max-h-[220px]">
                        <div className="p-1">
                            {filteredSuggestions.length === 0 ? (
                                <div className="px-3 py-2 text-xs font-mono text-muted-foreground text-center">
                                    No matching directories
                                </div>
                            ) : (
                                filteredSuggestions.map((entry, idx) => {
                                    const isActive = idx === activeIndex;
                                    return (
                                        <button
                                            key={entry.path}
                                            type="button"
                                            className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded text-left text-xs font-mono transition-colors ${
                                                isActive ? 'bg-accent text-accent-foreground font-medium' : 'hover:bg-accent/70'
                                            }`}
                                            onMouseDown={e => {
                                                e.preventDefault();
                                                handleSelectPath(entry.path);
                                            }}
                                        >
                                            <Folder className="size-3.5 text-primary shrink-0" />
                                            <span className="truncate">{entry.path}</span>
                                        </button>
                                    );
                                })
                            )}
                        </div>
                    </ScrollArea>
                </div>
            )}
        </div>
    );
}
