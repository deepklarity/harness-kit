import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { Board, ViewMode } from '../types';
import { useAuth } from '../contexts/AuthContext';
import { useService } from '../contexts/ServiceContext';

import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
    BarChart3, LayoutDashboard, FileText,
    Plus, LogOut, Settings,
    Activity, Search, ChevronDown, Clock3, Sparkles, ListTodo, Gauge, ScrollText,
} from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { NotificationBell } from './NotificationBell';
import { CapacityBadge } from './CapacityBadge';
import type { LucideIcon } from 'lucide-react';

const ALL_BOARDS_ID = '__ALL__';
const CREATE_BOARD_ID = '__CREATE__';

const VIEW_ROUTES: { id: ViewMode; path: string; label: string; icon: LucideIcon }[] = [
    { id: 'board', path: '/board', label: 'Board', icon: LayoutDashboard },
    { id: 'specs', path: '/specs', label: 'Specs', icon: FileText },
    { id: 'scheduling', path: '/scheduling', label: 'Scheduling', icon: Clock3 },
    { id: 'overview', path: '/stats', label: 'Stats', icon: BarChart3 },
    { id: 'reflections', path: '/reflections', label: 'Reflections', icon: ScrollText },
    { id: 'providers', path: '/providers', label: 'Providers', icon: Gauge },
];

// The board-scoped work surfaces — the primary segmented tab group.
const PRIMARY_VIEWS: ViewMode[] = ['board', 'specs', 'scheduling'];
// Retrospective / analytics surfaces — grouped under the "Insights" dropdown.
const INSIGHTS_VIEWS: ViewMode[] = ['overview', 'reflections'];

export { ALL_BOARDS_ID, VIEW_ROUTES, PRIMARY_VIEWS, INSIGHTS_VIEWS };

interface AppHeaderProps {
    boards: Board[];
    selectedBoard: string;
    currentBoard: Board | null;
    isAllBoards: boolean;
    viewMode: ViewMode;
    onBoardChange: (value: string) => void;
    onNavChange: (value: string) => void;
    onCreateTask: () => void;
    onCreateSpec: () => void;
    onCreateBoard: () => void;
    onNavigateHome: () => void;
    onOpenProcessMonitor: () => void;
    onOpenCommandPalette: () => void;
}

export function AppHeader({
    boards, selectedBoard, currentBoard, isAllBoards, viewMode,
    onBoardChange, onNavChange,
    onCreateTask, onCreateSpec, onCreateBoard, onNavigateHome,
    onOpenProcessMonitor, onOpenCommandPalette,
}: AppHeaderProps) {
    const { user: authUser, authEnabled, logout } = useAuth();
    const service = useService();
    const [insightsOpen, setInsightsOpen] = useState(false);
    const boardQuery = selectedBoard && selectedBoard !== ALL_BOARDS_ID ? `?board=${selectedBoard}` : '';
    const insightsActive = INSIGHTS_VIEWS.includes(viewMode);
    const activeInsight = VIEW_ROUTES.find(item => item.id === viewMode && INSIGHTS_VIEWS.includes(item.id))
        ?? VIEW_ROUTES.find(item => item.id === 'overview');
    const ActiveInsightIcon = activeInsight?.icon ?? BarChart3;
    const providersActive = viewMode === 'providers';
    const settingsActive = viewMode === 'settings';

    return (
        <header className="sticky top-0 z-50 bg-background border-b border-border px-4 sm:px-6 lg:px-8">
            <div className="mx-auto flex items-center justify-between h-14 gap-2">
                <div className="flex items-center gap-2 sm:gap-4 min-w-0">
                    <button type="button" className="text-base font-bold tracking-tight hover:text-foreground/80 transition-colors shrink-0" onClick={onNavigateHome}>Taskit</button>
                    {import.meta.env.VITE_INSTANCE && (
                        <span className="text-[10px] font-bold uppercase tracking-wider bg-primary text-primary-foreground px-1.5 py-0.5 rounded">
                            {import.meta.env.VITE_INSTANCE}
                        </span>
                    )}
                    <Select value={selectedBoard} onValueChange={(val) => val === CREATE_BOARD_ID ? onCreateBoard() : onBoardChange(val)}>
                        <SelectTrigger className="w-[140px] sm:w-[180px] lg:w-[220px] h-8 text-sm">
                            <SelectValue placeholder="Select board">
                                {isAllBoards ? (
                                    <span className="font-medium">All Boards</span>
                                ) : (
                                    <span className="truncate">{currentBoard?.name}</span>
                                )}
                            </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                            {boards.map(board => (
                                <SelectItem key={board.id} value={board.id}>
                                    <div className="flex items-center justify-between w-full gap-8">
                                        <span>{board.name}</span>
                                        <span className="text-[10px] font-mono text-muted-foreground opacity-50 tabular-nums">
                                            {board.id}
                                        </span>
                                    </div>
                                </SelectItem>
                            ))}
                            {boards.length >= 2 && (
                                <>
                                    <Separator className="my-1" />
                                    <SelectItem value={ALL_BOARDS_ID}>
                                        <span className="text-muted-foreground">All Boards</span>
                                    </SelectItem>
                                </>
                            )}
                            <Separator className="my-1" />
                            <SelectItem value={CREATE_BOARD_ID}>
                                <div className="flex items-center gap-2 text-muted-foreground">
                                    <Plus className="size-3.5" />
                                    <span>Create Board</span>
                                </div>
                            </SelectItem>
                        </SelectContent>
                    </Select>
                </div>

                <nav aria-label="Primary" className="flex items-center gap-1">
                    <div className="inline-flex items-center justify-center rounded-lg bg-muted p-[3px] h-9 shrink-0">
                        {PRIMARY_VIEWS.map(id => VIEW_ROUTES.find(r => r.id === id)!).map(item => {
                            const Icon = item.icon;
                            const isActive = viewMode === item.id;
                            return (
                                <Link
                                    key={item.id}
                                    to={`${item.path}${boardQuery}`}
                                    aria-current={isActive ? 'page' : undefined}
                                    className={cn(
                                        "inline-flex items-center justify-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium whitespace-nowrap transition-all cursor-pointer",
                                        isActive
                                            ? "bg-background text-foreground shadow-sm"
                                            : "text-foreground/60 hover:text-foreground"
                                    )}
                                >
                                    <Icon className="size-3.5" />
                                    <span className="hidden sm:inline">{item.label}</span>
                                </Link>
                            );
                        })}
                    </div>

                    <Popover open={insightsOpen} onOpenChange={setInsightsOpen}>
                        <PopoverTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    aria-current={insightsActive ? 'page' : undefined}
                                    className={cn(
                                        "h-8 px-2.5 gap-1.5 text-xs font-medium transition-colors",
                                        insightsActive
                                            ? "bg-muted text-foreground"
                                            : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                    )}
                                >
                                <ActiveInsightIcon className="size-3.5" />
                                <span className="hidden sm:inline">{insightsActive ? activeInsight?.label : 'Insights'}</span>
                                <ChevronDown className="size-3 opacity-50" />
                            </Button>
                        </PopoverTrigger>
                        <PopoverContent align="center" className="w-44 p-1">
                            <div className="flex flex-col gap-0.5">
                                {INSIGHTS_VIEWS.map(id => VIEW_ROUTES.find(r => r.id === id)!).map(item => {
                                    const Icon = item.icon;
                                    const isActive = viewMode === item.id;
                                    return (
                                        <button
                                            key={item.id}
                                            type="button"
                                            onClick={() => {
                                                setInsightsOpen(false);
                                                onNavChange(item.id);
                                            }}
                                            aria-current={isActive ? 'page' : undefined}
                                            className={cn(
                                                "flex items-center gap-2 px-2 py-1.5 text-xs font-medium rounded-sm transition-colors w-full text-left",
                                                isActive ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                                            )}
                                        >
                                            <Icon className="size-3.5" />
                                            <span>{item.label}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        </PopoverContent>
                    </Popover>
                </nav>

                <div className="flex items-center gap-1 sm:gap-2 shrink-0">
                    <button
                        type="button"
                        onClick={onOpenCommandPalette}
                        className="hidden md:flex items-center gap-1.5 h-8 px-2.5 text-xs text-muted-foreground border border-input rounded-md bg-background hover:bg-accent hover:text-accent-foreground transition-colors"
                    >
                        <Search className="size-3.5" />
                        <span>Search</span>
                        <kbd className="ml-1 inline-flex h-5 items-center rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground pointer-events-none">
                            ⌘K
                        </kbd>
                    </button>
                    <CapacityBadge service={service} className="hidden sm:inline-flex" />
                    <Button variant="outline" size="sm" className="gap-1.5 h-8 px-2 sm:px-3" onClick={onOpenProcessMonitor}>
                        <Activity className="size-3.5" />
                        <span className="hidden md:inline">Process</span>
                    </Button>
                    <Popover>
                        <PopoverTrigger asChild>
                            <Button size="sm" className="gap-1.5 h-8 px-2 sm:px-3">
                                <Plus className="size-3.5" />
                                <span className="hidden md:inline">New</span>
                                <ChevronDown className="size-3 opacity-50" />
                            </Button>
                        </PopoverTrigger>
                        <PopoverContent align="end" className="w-40 p-1">
                            <div className="flex flex-col gap-0.5">
                                <button
                                    type="button"
                                    onClick={onCreateTask}
                                    className="flex items-center gap-2 px-2 py-1.5 text-xs font-medium rounded-sm transition-colors w-full text-left hover:bg-accent/50"
                                >
                                    <ListTodo className="size-3.5" />
                                    <span>Task</span>
                                </button>
                                <button
                                    type="button"
                                    onClick={onCreateSpec}
                                    className="flex items-center gap-2 px-2 py-1.5 text-xs font-medium rounded-sm transition-colors w-full text-left hover:bg-accent/50"
                                >
                                    <Sparkles className="size-3.5" />
                                    <span>Spec</span>
                                </button>
                            </div>
                        </PopoverContent>
                    </Popover>
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="ghost"
                                size="sm"
                                aria-label="Providers"
                                aria-current={providersActive ? 'page' : undefined}
                                className={cn("size-8 p-0", providersActive && "bg-muted text-foreground")}
                                asChild
                            >
                                <Link to={`/providers${boardQuery}`}>
                                    <Gauge className="size-4" />
                                </Link>
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent>Providers — AI quota &amp; health</TooltipContent>
                    </Tooltip>
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="ghost"
                                size="sm"
                                aria-label="Settings"
                                aria-current={settingsActive ? 'page' : undefined}
                                className={cn("size-8 p-0", settingsActive && "bg-muted text-foreground")}
                                asChild
                            >
                                <Link to={`/settings${boardQuery}`}>
                                    <Settings className="size-4" />
                                </Link>
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent>Settings</TooltipContent>
                    </Tooltip>
                    <NotificationBell />
                    {authEnabled && authUser && (
                        <div className="flex items-center gap-1 sm:gap-2 ml-1 sm:ml-2 pl-1 sm:pl-2 border-l border-border">
                            <span className="text-xs text-muted-foreground truncate max-w-[80px] lg:max-w-[150px] hidden sm:block">
                                {authUser.displayName || authUser.email}
                            </span>
                            <Button variant="ghost" size="sm" className="gap-1.5 h-7 px-2" onClick={logout}>
                                <LogOut className="size-3.5" />
                            </Button>
                        </div>
                    )}
                </div>
            </div>
        </header>
    );
}
