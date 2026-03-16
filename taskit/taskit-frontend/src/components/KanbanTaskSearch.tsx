import { useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { useService } from '@/contexts/ServiceContext';
import type { TaskSearchResult } from '@/services/integration/IntegrationService';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

type SearchScope = 'board' | 'global';

interface KanbanTaskSearchProps {
    selectedBoard?: string;
    onSelect: (result: TaskSearchResult, scope: SearchScope) => void;
    className?: string;
}

const RESULT_LIMIT = 10;

export function KanbanTaskSearch({ selectedBoard, onSelect, className }: KanbanTaskSearchProps) {
    const service = useService();
    const rootRef = useRef<HTMLDivElement | null>(null);
    const [query, setQuery] = useState('');
    const [scope, setScope] = useState<SearchScope>('board');
    const [results, setResults] = useState<TaskSearchResult[]>([]);
    const [loading, setLoading] = useState(false);
    const [open, setOpen] = useState(false);
    const [activeIndex, setActiveIndex] = useState(-1);
    const [asyncError, setAsyncError] = useState<string | null>(null);

    const trimmed = query.trim();
    const canSearchBoard = scope === 'global' || !!selectedBoard;
    const error = trimmed && !canSearchBoard
        ? 'Select a board to search within the current board.'
        : asyncError;
    const showEmptyState = trimmed.length > 0 && !loading && results.length === 0 && !error;

    useEffect(() => {
        if (!trimmed || !canSearchBoard) return;

        let cancelled = false;

        const timer = window.setTimeout(() => {
            setLoading(true);
            setAsyncError(null);
            void service.searchTasks({
                q: trimmed,
                scope,
                boardId: scope === 'board' ? selectedBoard : undefined,
                limit: RESULT_LIMIT,
            }).then(next => {
                if (cancelled) return;
                setResults(next);
                setOpen(true);
                setActiveIndex(next.length > 0 ? 0 : -1);
            }).catch((err: unknown) => {
                if (cancelled) return;
                setResults([]);
                setOpen(true);
                setActiveIndex(-1);
                setAsyncError(err instanceof Error ? err.message : 'Search failed');
            }).finally(() => {
                if (!cancelled) setLoading(false);
            });
        }, 200);

        return () => {
            cancelled = true;
            window.clearTimeout(timer);
        };
    }, [canSearchBoard, scope, selectedBoard, service, trimmed]);

    useEffect(() => {
        const handlePointerDown = (event: MouseEvent) => {
            if (!rootRef.current?.contains(event.target as Node)) {
                setOpen(false);
            }
        };

        document.addEventListener('mousedown', handlePointerDown);
        return () => document.removeEventListener('mousedown', handlePointerDown);
    }, []);

    const helperText = useMemo(() => {
        if (scope === 'global') return 'Search all boards';
        if (selectedBoard) return 'Search this board';
        return 'Select a board to search';
    }, [scope, selectedBoard]);

    const handleSelect = (result: TaskSearchResult) => {
        onSelect(result, scope);
        setOpen(false);
        setQuery('');
        setResults([]);
        setActiveIndex(-1);
    };

    return (
        <div ref={rootRef} className={cn("relative w-full max-w-xl", className)}>
            <div className="flex items-center gap-2">
                <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                    <Input
                        value={query}
                        onChange={(e) => {
                            const nextValue = e.target.value;
                            setQuery(nextValue);
                            if (!nextValue.trim()) {
                                setResults([]);
                                setOpen(false);
                                setActiveIndex(-1);
                                setAsyncError(null);
                            }
                        }}
                        onFocus={() => {
                            if (trimmed || error || results.length > 0) setOpen(true);
                        }}
                        onKeyDown={(e) => {
                            if (!open || results.length === 0) {
                                if (e.key === 'Escape') setOpen(false);
                                return;
                            }

                            if (e.key === 'ArrowDown') {
                                e.preventDefault();
                                setActiveIndex((prev) => (prev + 1) % results.length);
                            } else if (e.key === 'ArrowUp') {
                                e.preventDefault();
                                setActiveIndex((prev) => (prev <= 0 ? results.length - 1 : prev - 1));
                            } else if (e.key === 'Enter') {
                                e.preventDefault();
                                const target = results[activeIndex] ?? results[0];
                                if (target) handleSelect(target);
                            } else if (e.key === 'Escape') {
                                e.preventDefault();
                                setOpen(false);
                            }
                        }}
                        className="pl-8"
                        placeholder="Search cards by title or spec..."
                        aria-label="Search cards"
                    />
                </div>
                <div className="flex items-center gap-1 rounded-md border border-input bg-background p-1">
                    <Button
                        type="button"
                        variant={scope === 'board' ? 'secondary' : 'ghost'}
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => {
                            setScope('board');
                            setResults([]);
                            setActiveIndex(-1);
                            setAsyncError(null);
                            if (trimmed) setOpen(true);
                        }}
                    >
                        Board
                    </Button>
                    <Button
                        type="button"
                        variant={scope === 'global' ? 'secondary' : 'ghost'}
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => {
                            setScope('global');
                            setResults([]);
                            setActiveIndex(-1);
                            setAsyncError(null);
                            if (trimmed) setOpen(true);
                        }}
                    >
                        Global
                    </Button>
                </div>
            </div>
            <div className="mt-1 px-1 text-[11px] text-muted-foreground">{helperText}</div>

            {open && (trimmed || error) && (
                <div className="absolute z-30 mt-2 max-h-96 w-full overflow-y-auto rounded-md border border-border bg-background shadow-lg">
                    {loading && <div className="px-3 py-2 text-sm text-muted-foreground">Searching…</div>}
                    {!loading && error && <div className="px-3 py-2 text-sm text-destructive">{error}</div>}
                    {!loading && showEmptyState && <div className="px-3 py-2 text-sm text-muted-foreground">No matching cards</div>}
                    {!loading && results.map((result, index) => (
                        <button
                            key={`${result.taskId}-${result.boardId}`}
                            type="button"
                            className={cn(
                                'flex w-full items-start justify-between gap-3 border-b border-border/60 px-3 py-2 text-left last:border-b-0',
                                index === activeIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
                            )}
                            onMouseEnter={() => setActiveIndex(index)}
                            onClick={() => handleSelect(result)}
                        >
                            <div className="min-w-0">
                                <div className="truncate text-sm font-medium">{result.title}</div>
                                <div className="truncate text-xs text-muted-foreground">
                                    {result.boardName}
                                    {result.specTitle ? ` • ${result.specTitle}` : ''}
                                </div>
                            </div>
                            <Badge variant="outline" className="shrink-0 text-[10px]">
                                {result.status}
                            </Badge>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
