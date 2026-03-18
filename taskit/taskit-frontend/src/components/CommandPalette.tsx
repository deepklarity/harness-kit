import { useEffect, useMemo, useRef, useState } from 'react';
import { Search, LayoutDashboard, FileText, BarChart3, Bell, Settings, Plus, Activity } from 'lucide-react';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { useService } from '@/contexts/ServiceContext';
import { cn } from '@/lib/utils';
import type { Board } from '@/types';
import type { TaskSearchResult } from '@/services/integration/IntegrationService';

interface CommandPaletteProps {
    open: boolean;
    onClose: () => void;
    boards: Board[];
    navigate: (path: string) => void;
    onCreateTask: () => void;
    onCreateBoard: () => void;
    onTaskSelect: (taskId: string) => void;
    onBoardChange: (boardId: string) => void;
    onOpenProcessMonitor: () => void;
    initialQuery?: string;
}

type CommandItem =
    | { kind: 'task'; result: TaskSearchResult }
    | { kind: 'nav'; label: string; shortcut?: string; action: () => void; icon: React.ReactNode }
    | { kind: 'action'; label: string; shortcut?: string; action: () => void; icon: React.ReactNode }
    | { kind: 'board'; label: string; boardId: string; action: () => void }
    | { kind: 'shortcut'; label: string; key: string };

type CommandGroup = {
    label: string;
    items: CommandItem[];
};

const isMac = () => navigator.platform.toUpperCase().includes('MAC');

function Kbd({ children }: { children: React.ReactNode }) {
    return (
        <kbd className="inline-flex h-5 items-center rounded border border-border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground pointer-events-none">
            {children}
        </kbd>
    );
}

export function CommandPalette({
    open,
    onClose,
    boards,
    navigate,
    onCreateTask,
    onCreateBoard,
    onTaskSelect,
    onBoardChange,
    onOpenProcessMonitor,
    initialQuery,
}: CommandPaletteProps) {
    const service = useService();
    const inputRef = useRef<HTMLInputElement>(null);
    const listRef = useRef<HTMLDivElement>(null);

    const [query, setQuery] = useState('');
    const [taskResults, setTaskResults] = useState<TaskSearchResult[]>([]);
    const [taskLoading, setTaskLoading] = useState(false);
    const [activeIndex, setActiveIndex] = useState(0);

    // Auto-focus input when opened
    useEffect(() => {
        if (open) {
            setQuery(initialQuery === 'shortcuts' ? '' : (initialQuery ?? ''));
            setTaskResults([]);
            setActiveIndex(0);
            setTimeout(() => inputRef.current?.focus(), 0);
        }
    }, [open, initialQuery]);

    // Debounced task search
    useEffect(() => {
        const trimmed = query.trim();
        if (trimmed.length < 2) {
            setTaskResults([]);
            return;
        }

        let cancelled = false;
        setTaskLoading(true);

        const timer = window.setTimeout(() => {
            void service.searchTasks({ q: trimmed, scope: 'global', limit: 8 })
                .then(results => { if (!cancelled) setTaskResults(results); })
                .catch(() => { if (!cancelled) setTaskResults([]); })
                .finally(() => { if (!cancelled) setTaskLoading(false); });
        }, 200);

        return () => {
            cancelled = true;
            window.clearTimeout(timer);
            setTaskLoading(false);
        };
    }, [query, service]);

    const modKey = isMac() ? '⌘' : 'Ctrl';
    const showShortcuts = initialQuery === 'shortcuts' && query === '';

    const groups = useMemo((): CommandGroup[] => {
        const q = query.toLowerCase();
        const result: CommandGroup[] = [];

        // Tasks group (dynamic search results)
        if (taskLoading || taskResults.length > 0) {
            result.push({
                label: 'TASKS',
                items: taskResults.map(r => ({ kind: 'task' as const, result: r })),
            });
        }

        // Navigation group
        const navItems: CommandItem[] = [
            { kind: 'nav', label: 'Go to Board', shortcut: 'G B', action: () => navigate('/board'), icon: <LayoutDashboard className="size-3.5" /> },
            { kind: 'nav', label: 'Go to Specs', shortcut: 'G S', action: () => navigate('/specs'), icon: <FileText className="size-3.5" /> },
            { kind: 'nav', label: 'Go to Stats', shortcut: 'G D', action: () => navigate('/stats'), icon: <BarChart3 className="size-3.5" /> },
            { kind: 'nav', label: 'Go to Notifications', shortcut: 'G N', action: () => navigate('/notifications'), icon: <Bell className="size-3.5" /> },
            { kind: 'nav', label: 'Go to Settings', shortcut: 'G T', action: () => navigate('/settings'), icon: <Settings className="size-3.5" /> },
        ];
        const filteredNav = q ? navItems.filter(i => i.kind === 'nav' && i.label.toLowerCase().includes(q)) : navItems;
        if (filteredNav.length > 0) result.push({ label: 'NAVIGATION', items: filteredNav });

        // Actions group
        const actionItems: CommandItem[] = [
            { kind: 'action', label: 'Create Task', shortcut: 'N', action: onCreateTask, icon: <Plus className="size-3.5" /> },
            { kind: 'action', label: 'Create Board', action: onCreateBoard, icon: <Plus className="size-3.5" /> },
            { kind: 'action', label: 'Open Process Monitor', action: onOpenProcessMonitor, icon: <Activity className="size-3.5" /> },
        ];
        const filteredActions = q ? actionItems.filter(i => i.kind === 'action' && i.label.toLowerCase().includes(q)) : actionItems;
        if (filteredActions.length > 0) result.push({ label: 'ACTIONS', items: filteredActions });

        // Boards group
        const boardItems: CommandItem[] = boards.map(board => ({
            kind: 'board' as const,
            label: `Switch to "${board.name}"`,
            boardId: board.id,
            action: () => onBoardChange(board.id),
        }));
        const filteredBoards = q ? boardItems.filter(i => i.kind === 'board' && i.label.toLowerCase().includes(q)) : boardItems;
        if (filteredBoards.length > 0) result.push({ label: 'BOARDS', items: filteredBoards });

        // Shortcuts group
        const shortcutMatch = !q || 'shortcuts keyboard hotkeys'.includes(q);
        if (showShortcuts || shortcutMatch) {
            const shortcutItems: CommandItem[] = [
                { kind: 'shortcut', label: 'Open command palette', key: `${modKey}K` },
                { kind: 'shortcut', label: 'Create new task', key: 'N' },
                { kind: 'shortcut', label: 'Show keyboard shortcuts', key: '?' },
                { kind: 'shortcut', label: 'Go to Board', key: 'G B' },
                { kind: 'shortcut', label: 'Go to Specs', key: 'G S' },
                { kind: 'shortcut', label: 'Go to Stats', key: 'G D' },
                { kind: 'shortcut', label: 'Go to Notifications', key: 'G N' },
                { kind: 'shortcut', label: 'Go to Settings', key: 'G T' },
            ];
            result.push({ label: 'SHORTCUTS', items: shortcutItems });
        }

        return result;
    }, [query, taskResults, taskLoading, boards, navigate, onCreateTask, onCreateBoard, onBoardChange, onOpenProcessMonitor, showShortcuts, modKey]);

    // Flat list of interactive items (excluding shortcut-only rows)
    const interactiveItems = useMemo(() => {
        const items: { groupIndex: number; itemIndex: number; item: CommandItem }[] = [];
        groups.forEach((group, gi) => {
            group.items.forEach((item, ii) => {
                if (item.kind !== 'shortcut') {
                    items.push({ groupIndex: gi, itemIndex: ii, item });
                }
            });
        });
        return items;
    }, [groups]);

    const totalInteractive = interactiveItems.length;

    // Scroll active item into view
    useEffect(() => {
        if (!listRef.current) return;
        const active = listRef.current.querySelector('[data-active="true"]');
        if (active) active.scrollIntoView({ block: 'nearest' });
    }, [activeIndex]);

    const executeItem = (item: CommandItem) => {
        if (item.kind === 'task') {
            onTaskSelect(item.result.taskId);
        } else if (item.kind === 'shortcut') {
            // non-interactive, do nothing
            return;
        } else {
            item.action();
        }
        onClose();
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveIndex(i => (i + 1) % Math.max(totalInteractive, 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveIndex(i => (i <= 0 ? Math.max(totalInteractive - 1, 0) : i - 1));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            const entry = interactiveItems[activeIndex];
            if (entry) executeItem(entry.item);
        } else if (e.key === 'Escape') {
            e.preventDefault();
            onClose();
        }
    };

    // Build flat index map for rendering
    const getInteractiveIndex = (gi: number, ii: number) =>
        interactiveItems.findIndex(x => x.groupIndex === gi && x.itemIndex === ii);

    return (
        <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
            <DialogContent className="p-0 gap-0 overflow-hidden sm:max-w-[580px] [&>button]:hidden">
                {/* Search input */}
                <div className="flex items-center gap-2 border-b border-border px-3">
                    <Search className="size-4 shrink-0 text-muted-foreground" />
                    <input
                        ref={inputRef}
                        value={query}
                        onChange={e => { setQuery(e.target.value); setActiveIndex(0); }}
                        onKeyDown={handleKeyDown}
                        placeholder="Search tasks, commands..."
                        className="flex-1 bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground"
                        aria-label="Command palette search"
                    />
                    {taskLoading && (
                        <div className="size-4 rounded-full border-2 border-border border-t-primary animate-spin shrink-0" />
                    )}
                </div>

                {/* Results */}
                <div ref={listRef} className="max-h-[400px] overflow-y-auto">
                    {groups.length === 0 && (
                        <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                            No results for &ldquo;{query}&rdquo;
                        </div>
                    )}
                    {groups.map((group, gi) => (
                        <div key={group.label}>
                            <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 bg-muted/30">
                                {group.label}
                            </div>
                            {group.items.map((item, ii) => {
                                if (item.kind === 'shortcut') {
                                    return (
                                        <div key={ii} className="flex items-center justify-between px-3 py-2 text-sm text-muted-foreground">
                                            <span>{item.label}</span>
                                            <Kbd>{item.key}</Kbd>
                                        </div>
                                    );
                                }

                                const flatIdx = getInteractiveIndex(gi, ii);
                                const isActive = flatIdx === activeIndex;

                                if (item.kind === 'task') {
                                    const r = item.result;
                                    return (
                                        <button
                                            key={`task-${r.taskId}`}
                                            type="button"
                                            data-active={isActive}
                                            className={cn(
                                                'flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm',
                                                isActive ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
                                            )}
                                            onMouseEnter={() => setActiveIndex(flatIdx)}
                                            onClick={() => executeItem(item)}
                                        >
                                            <div className="min-w-0">
                                                <div className="truncate font-medium">{r.title}</div>
                                                <div className="truncate text-xs text-muted-foreground">{r.boardName}{r.specTitle ? ` • ${r.specTitle}` : ''}</div>
                                            </div>
                                            <Badge variant="outline" className="shrink-0 text-[10px]">{r.status}</Badge>
                                        </button>
                                    );
                                }

                                const label = item.kind === 'board' ? item.label
                                    : item.kind === 'nav' ? item.label
                                    : item.label;
                                const shortcut = (item.kind === 'nav' || item.kind === 'action') ? item.shortcut : undefined;
                                const icon = (item.kind === 'nav' || item.kind === 'action') ? item.icon : null;

                                return (
                                    <button
                                        key={`${item.kind}-${ii}`}
                                        type="button"
                                        data-active={isActive}
                                        className={cn(
                                            'flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm',
                                            isActive ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
                                        )}
                                        onMouseEnter={() => setActiveIndex(flatIdx)}
                                        onClick={() => executeItem(item)}
                                    >
                                        <div className="flex items-center gap-2.5">
                                            {icon && <span className="text-muted-foreground">{icon}</span>}
                                            <span>{label}</span>
                                        </div>
                                        {shortcut && <Kbd>{shortcut}</Kbd>}
                                    </button>
                                );
                            })}
                        </div>
                    ))}
                </div>

                {/* Footer */}
                <div className="flex items-center gap-3 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
                    <span><Kbd>↑↓</Kbd> navigate</span>
                    <span><Kbd>↵</Kbd> select</span>
                    <span><Kbd>esc</Kbd> close</span>
                    <span className="ml-auto">{isMac() ? '⌘K' : 'Ctrl+K'} to open</span>
                </div>
            </DialogContent>
        </Dialog>
    );
}
