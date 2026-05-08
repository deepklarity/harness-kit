import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { Board, ViewMode } from '../types';
import { useAuth } from '../contexts/AuthContext';

import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import {
    BarChart3, LayoutDashboard, FileText, TrendingUp,
    Plus, LogOut, Settings,
    Activity, Search, ChevronDown, Clock3, Sparkles, ListTodo, Gauge, ScrollText,
} from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { NotificationBell } from './NotificationBell';
import type { LucideIcon } from 'lucide-react';

const ALL_BOARDS_ID = '__ALL__';
const CREATE_BOARD_ID = '__CREATE__';

const VIEW_ROUTES: { id: ViewMode; path: string; label: string; icon: LucideIcon }[] = [
    { id: 'board', path: '/board', label: 'Board', icon: LayoutDashboard },
    { id: 'scheduling', path: '/scheduling', label: 'Scheduling', icon: Clock3 },
    { id: 'specs', path: '/specs', label: 'Specs', icon: FileText },
    { id: 'overview', path: '/stats', label: 'Stats', icon: BarChart3 },
    { id: 'providers', path: '/providers', label: 'Providers', icon: Gauge },
    { id: 'analytics', path: '/analytics', label: 'Analytics', icon: TrendingUp },
    { id: 'reflections', path: '/reflections', label: 'Reflections', icon: ScrollText },
];

export { ALL_BOARDS_ID, VIEW_ROUTES };

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
    const [statsOpen, setStatsOpen] = useState(false);
    const activeStatsView = VIEW_ROUTES.find(item => item.id === viewMode && ['overview', 'analytics', 'providers', 'reflections'].includes(item.id))
        ?? VIEW_ROUTES.find(item => item.id === 'overview');
    const ActiveStatsIcon = activeStatsView?.icon ?? BarChart3;

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

                <div className="flex items-center gap-1">
                    <div className="inline-flex items-center justify-center rounded-lg bg-muted p-[3px] h-9 shrink-0">
                        {VIEW_ROUTES.filter(item => ['board', 'specs'].includes(item.id)).map(item => {
                            const Icon = item.icon;
                            const isActive = viewMode === item.id;
                            return (
                                <Link
                                    key={item.id}
                                    to={selectedBoard && selectedBoard !== ALL_BOARDS_ID ? `${item.path}?board=${selectedBoard}` : item.path}
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

                    <Popover open={statsOpen} onOpenChange={setStatsOpen}>
                        <PopoverTrigger asChild>
                            <Button
                                variant="ghost"
                                size="sm"
                                className={cn(
                                    "h-8 px-2.5 gap-1.5 text-xs font-medium transition-colors",
                                    ['overview', 'analytics', 'providers', 'reflections'].includes(viewMode)
                                        ? "bg-muted text-foreground"
                                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                )}
                            >
                                <ActiveStatsIcon className="size-3.5" />
                                <span className="hidden sm:inline">{activeStatsView?.label ?? 'Stats'}</span>
                                <ChevronDown className="size-3 opacity-50" />
                            </Button>
                        </PopoverTrigger>
                        <PopoverContent align="center" className="w-40 p-1">
                            <div className="flex flex-col gap-0.5">
                                <button
                                    type="button"
                                    onClick={() => {
                                        setStatsOpen(false);
                                        onNavChange('overview');
                                    }}
                                    className={cn(
                                        "flex items-center gap-2 px-2 py-1.5 text-xs font-medium rounded-sm transition-colors w-full text-left",
                                        viewMode === 'overview' ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                                    )}
                                >
                                    <BarChart3 className="size-3.5" />
                                    <span>Stats</span>
                                </button>
                                <button
                                    type="button"
                                    onClick={() => {
                                        setStatsOpen(false);
                                        onNavChange('analytics');
                                    }}
                                    className={cn(
                                        "flex items-center gap-2 px-2 py-1.5 text-xs font-medium rounded-sm transition-colors w-full text-left",
                                        viewMode === 'analytics' ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                                    )}
                                >
                                    <TrendingUp className="size-3.5" />
                                    <span>Analytics</span>
                                </button>
                                <button
                                    type="button"
                                    onClick={() => {
                                        setStatsOpen(false);
                                        onNavChange('providers');
                                    }}
                                    className={cn(
                                        "flex items-center gap-2 px-2 py-1.5 text-xs font-medium rounded-sm transition-colors w-full text-left",
                                        viewMode === 'providers' ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                                    )}
                                >
                                    <Gauge className="size-3.5" />
                                    <span>Providers</span>
                                </button>
                                <button
                                    type="button"
                                    onClick={() => {
                                        setStatsOpen(false);
                                        onNavChange('reflections');
                                    }}
                                    className={cn(
                                        "flex items-center gap-2 px-2 py-1.5 text-xs font-medium rounded-sm transition-colors w-full text-left",
                                        viewMode === 'reflections' ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                                    )}
                                >
                                    <ScrollText className="size-3.5" />
                                    <span>Reflections</span>
                                </button>
                            </div>
                        </PopoverContent>
                    </Popover>
                </div>

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
                    <Button variant="ghost" size="sm" className="size-8 p-0" asChild>
                        <Link to={selectedBoard && selectedBoard !== ALL_BOARDS_ID ? `/settings?board=${selectedBoard}` : '/settings'}>
                            <Settings className="size-4" />
                        </Link>
                    </Button>
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
