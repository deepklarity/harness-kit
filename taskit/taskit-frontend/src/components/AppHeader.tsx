import { Link } from 'react-router-dom';
import type { Board, ViewMode } from '../types';
import { useAuth } from '../contexts/AuthContext';

import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import {
    BarChart3, LayoutDashboard, FileText,
    Plus, LogOut, Moon, Sun, Settings,
    Activity,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

const ALL_BOARDS_ID = '__ALL__';
const CREATE_BOARD_ID = '__CREATE__';

const VIEW_ROUTES: { id: ViewMode; path: string; label: string; icon: LucideIcon }[] = [
    { id: 'board', path: '/board', label: 'Board', icon: LayoutDashboard },
    { id: 'specs', path: '/specs', label: 'Specs', icon: FileText },
    { id: 'overview', path: '/stats', label: 'Stats', icon: BarChart3 },
];

export { ALL_BOARDS_ID, VIEW_ROUTES };

interface AppHeaderProps {
    boards: Board[];
    selectedBoard: string;
    currentBoard: Board | null;
    isAllBoards: boolean;
    viewMode: ViewMode;
    dark: boolean;
    onBoardChange: (value: string) => void;
    onNavChange: (value: string) => void;
    onToggleDark: () => void;
    onCreateTask: () => void;
    onCreateBoard: () => void;
    onNavigateHome: () => void;
    onOpenProcessMonitor: () => void;
}

export function AppHeader({
    boards, selectedBoard, currentBoard, isAllBoards, viewMode, dark,
    onBoardChange, onNavChange, onToggleDark,
    onCreateTask, onCreateBoard, onNavigateHome,
    onOpenProcessMonitor,
}: AppHeaderProps) {
    const { user: authUser, authEnabled, logout } = useAuth();

    return (
        <header className="sticky top-0 z-50 bg-background border-b border-border px-8">
            <div className="max-w-[90vw] mx-auto flex items-center justify-between h-14">
                <div className="flex items-center gap-4">
                    <button type="button" className="text-base font-bold tracking-tight hover:text-foreground/80 transition-colors" onClick={onNavigateHome}>Taskit</button>
                    <div className="flex items-center gap-3">
                        <Select value={selectedBoard} onValueChange={(val) => val === CREATE_BOARD_ID ? onCreateBoard() : onBoardChange(val)}>
                            <SelectTrigger className="w-[220px] h-8 text-sm">
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
                </div>

                <Tabs value={viewMode} onValueChange={onNavChange}>
                    <TabsList>
                        {VIEW_ROUTES.map(item => {
                            const Icon = item.icon;
                            return (
                                <TabsTrigger key={item.id} value={item.id} className="gap-1.5 text-xs">
                                    <Icon className="size-3.5" />
                                    {item.label}
                                </TabsTrigger>
                            );
                        })}
                    </TabsList>
                </Tabs>

                <div className="flex items-center gap-2">

                    <Button variant="outline" size="sm" className="gap-1.5" onClick={onOpenProcessMonitor}>
                        <Activity className="size-3.5" /> Process
                    </Button>
                    <Button size="sm" className="gap-1.5" onClick={onCreateTask}>
                        <Plus className="size-3.5" /> Task
                    </Button>
                    <Button variant="ghost" size="sm" className="size-8 p-0" asChild>
                        <Link to={selectedBoard && selectedBoard !== ALL_BOARDS_ID ? `/settings?board=${selectedBoard}` : '/settings'}>
                            <Settings className="size-4" />
                        </Link>
                    </Button>
                    <Button variant="ghost" size="sm" className="size-8 p-0" onClick={onToggleDark}>
                        {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
                    </Button>
                    {authEnabled && authUser && (
                        <div className="flex items-center gap-2 ml-2 pl-2 border-l border-border">
                            <span className="text-xs text-muted-foreground truncate max-w-[150px]">
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
