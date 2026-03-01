import { useState, useEffect, useCallback } from 'react';
import type { AgentConfig, Board, Member } from '../types';
import { useService } from '../contexts/ServiceContext';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { useToast } from '@/hooks/use-toast';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Trash2, FlaskConical, Bot, FolderOpen, CheckCircle2, AlertCircle, Zap, Plus, Sparkles, Users } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { ManageMembersModal } from './ManageMembersModal';

interface SettingsViewProps {
    boards: Board[];
    members: Member[];
    onDataChange: () => void;
    onCreateBoard: () => void;
    onDeleteBoard: (boardId: string) => Promise<void>;
}

export function SettingsView({ boards, members, onDataChange, onCreateBoard, onDeleteBoard }: SettingsViewProps) {
    const service = useService();
    const { toast } = useToast();
    const navigate = useNavigate();
    const [boardToClear, setBoardToClear] = useState<Board | null>(null);
    const [clearing, setClearing] = useState(false);

    // Manage members modal
    const [managingBoard, setManagingBoard] = useState<Board | null>(null);

    const [initializingBoard, setInitializingBoard] = useState<string | null>(null);

    // Board deletion — double confirmation
    const [boardToDelete, setBoardToDelete] = useState<Board | null>(null);
    const [deleteConfirmStep, setDeleteConfirmStep] = useState<1 | 2>(1);
    const [deleteConfirmName, setDeleteConfirmName] = useState('');
    const [deleting, setDeleting] = useState(false);

    // Agent configs per board (for showing enabled/disabled status in summary)
    const [agentsByBoard, setAgentsByBoard] = useState<Record<string, AgentConfig[]>>({});

    const loadBoardAgents = useCallback(async (board: Board) => {
        if (!board.odinInitialized) return;
        try {
            const agents = await service.fetchBoardAgents(board.id);
            setAgentsByBoard(prev => ({ ...prev, [board.id]: agents }));
        } catch {
            // Silent — summary just won't show disabled agents
        }
    }, [service]);

    useEffect(() => {
        boards.filter(b => b.odinInitialized).forEach(loadBoardAgents);
    }, [boards, loadBoardAgents]);

    const handleClear = async () => {
        if (!boardToClear) return;
        setClearing(true);
        try {
            const result = await service.clearBoard(boardToClear.id);
            toast({
                title: 'Board cleared',
                description: `Deleted ${result.tasks_deleted} task(s) and ${result.specs_deleted} spec(s) from "${boardToClear.name}".`,
            });
            onDataChange();
        } catch (e) {
            toast({
                title: 'Failed to clear board',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        } finally {
            setClearing(false);
            setBoardToClear(null);
        }
    };

    const handleInitOdin = async (board: Board) => {
        setInitializingBoard(board.id);
        try {
            await service.initOdin(board.id);
            toast({ title: 'Odin initialized', description: `Initialized odin in ${board.workingDir}` });
            onDataChange();
        } catch (e) {
            toast({
                title: 'Failed to initialize odin',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        } finally {
            setInitializingBoard(null);
        }
    };

    const handleDeleteBoard = async () => {
        if (!boardToDelete) return;
        setDeleting(true);
        try {
            await onDeleteBoard(boardToDelete.id);
            toast({
                title: 'Board deleted',
                description: `"${boardToDelete.name}" and all its data have been permanently deleted.`,
            });
            onDataChange();
        } catch (e) {
            toast({
                title: 'Failed to delete board',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        } finally {
            setDeleting(false);
            setBoardToDelete(null);
            setDeleteConfirmStep(1);
            setDeleteConfirmName('');
        }
    };

    const isAgentUser = (member: Member) => member.email.endsWith('@odin.agent');

    return (
        <div className="space-y-8">
            {/* Boards Section */}
            <div>
                <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-medium text-muted-foreground">Boards</h3>
                    <Button variant="outline" size="sm" className="gap-1.5" onClick={onCreateBoard}>
                        <Plus className="size-3.5" />
                        Create Board
                    </Button>
                </div>
                <div className="space-y-4">
                    {boards.length === 0 && (
                        <div className="border border-border rounded-lg p-4 text-sm text-muted-foreground">No boards found.</div>
                    )}
                    {boards.map(board => {
                        const boardMembers = board.members;
                        const humanMembers = boardMembers.filter(m => !isAgentUser(m));
                        const agentConfigs = agentsByBoard[board.id] || [];
                        const activeAgentMembers = boardMembers.filter(m => isAgentUser(m));
                        // Use config data if available (shows all agents incl. disabled), fall back to membership data
                        const hasAgentConfigs = agentConfigs.length > 0;
                        const hasAnyMembers = humanMembers.length > 0 || activeAgentMembers.length > 0 || agentConfigs.length > 0;
                        return (
                            <div key={board.id} className="border border-border rounded-lg">
                                {/* Board header */}
                                <div className="flex items-center justify-between p-4 border-b border-border">
                                    <div className="flex items-center gap-3">
                                        <div>
                                            <div className="flex items-center gap-2">
                                                <span className="font-medium text-sm">{board.name}</span>
                                                {board.isTrial && (
                                                    <Badge variant="outline" className="text-[10px] gap-1 text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600">
                                                        <FlaskConical className="size-3" />
                                                        Trial
                                                    </Badge>
                                                )}
                                                {board.odinInitialized ? (
                                                    <Badge variant="outline" className="text-[10px] gap-1 text-emerald-600 border-emerald-300 dark:text-emerald-400 dark:border-emerald-600">
                                                        <CheckCircle2 className="size-3" />
                                                        Initialized
                                                    </Badge>
                                                ) : board.workingDir ? (
                                                    <Badge variant="outline" className="text-[10px] gap-1 text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600">
                                                        <AlertCircle className="size-3" />
                                                        Not Initialized
                                                    </Badge>
                                                ) : null}
                                            </div>
                                            <div className="text-xs text-muted-foreground mt-0.5">
                                                {board.tasks.length} task{board.tasks.length !== 1 ? 's' : ''}
                                                <span className="mx-1.5 text-border">|</span>
                                                {boardMembers.length} member{boardMembers.length !== 1 ? 's' : ''}
                                                <span className="mx-1.5 text-border">|</span>
                                                ID: {board.id}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        {board.workingDir && !board.odinInitialized && (
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                className="gap-1.5"
                                                disabled={initializingBoard === board.id}
                                                onClick={() => handleInitOdin(board)}
                                            >
                                                <Zap className="size-3.5" />
                                                {initializingBoard === board.id ? 'Initializing...' : 'Initialize Odin'}
                                            </Button>
                                        )}
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="gap-1.5"
                                            onClick={() => navigate(`/reflections?board=${board.id}`)}
                                        >
                                            <Sparkles className="size-3.5" />
                                            Reflections
                                        </Button>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="gap-1.5 text-destructive hover:text-destructive hover:bg-destructive/10"
                                            onClick={() => setBoardToClear(board)}
                                        >
                                            <Trash2 className="size-3.5" />
                                            Clear
                                        </Button>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="gap-1.5 text-destructive hover:text-destructive hover:bg-destructive/10"
                                            onClick={() => { setBoardToDelete(board); setDeleteConfirmStep(1); setDeleteConfirmName(''); }}
                                        >
                                            <Trash2 className="size-3.5" />
                                            Delete
                                        </Button>
                                    </div>
                                </div>

                                {/* Project directory (read-only after creation) */}
                                <div className="px-4 py-3 border-b border-border bg-muted/20">
                                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                                        <FolderOpen className="size-3.5" />
                                        <span className="font-medium">Project Directory</span>
                                    </div>
                                    {board.workingDir ? (
                                        <div className="text-xs font-mono text-foreground/80">
                                            {board.workingDir}
                                        </div>
                                    ) : (
                                        <div className="text-xs text-muted-foreground/60">
                                            No project directory set
                                        </div>
                                    )}
                                    <p className="text-[10px] text-muted-foreground/50 mt-1">
                                        Cannot be changed after creation
                                    </p>
                                </div>

                                {/* Members summary */}
                                <div className="px-4 py-3 space-y-2.5">
                                    {!hasAnyMembers ? (
                                        <div className="flex items-center justify-between">
                                            <span className="text-xs text-muted-foreground">No members assigned</span>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                className="gap-1.5"
                                                onClick={() => setManagingBoard(board)}
                                            >
                                                <Users className="size-3.5" />
                                                Manage
                                            </Button>
                                        </div>
                                    ) : (
                                        <>
                                            {/* People row */}
                                            <div className="flex items-center gap-2">
                                                <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider w-14 shrink-0">People</span>
                                                {humanMembers.length === 0 ? (
                                                    <span className="text-xs text-muted-foreground/60">None</span>
                                                ) : (
                                                    <div className="flex flex-wrap gap-1.5">
                                                        {humanMembers.map(member => (
                                                            <div
                                                                key={member.id}
                                                                className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-muted/40 border border-border/50"
                                                            >
                                                                <div
                                                                    className="size-4 rounded-full flex items-center justify-center text-[8px] font-medium text-white shrink-0"
                                                                    style={{ backgroundColor: member.color }}
                                                                >
                                                                    {member.initials}
                                                                </div>
                                                                <span className="text-xs">{member.fullName}</span>
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>

                                            {/* Agents row — from config (all agents) or from membership (enabled only) */}
                                            <div className="flex items-center gap-2">
                                                <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider w-14 shrink-0">Agents</span>
                                                {hasAgentConfigs ? (
                                                    <div className="flex flex-wrap gap-1.5">
                                                        {agentConfigs.map(agent => (
                                                            <div
                                                                key={agent.name}
                                                                className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border ${
                                                                    agent.enabled
                                                                        ? 'bg-blue-500/10 border-blue-300/30 dark:border-blue-600/30'
                                                                        : 'bg-muted/20 border-border/40 opacity-50'
                                                                }`}
                                                            >
                                                                <Bot className={`size-3 ${agent.enabled ? 'text-blue-500' : 'text-muted-foreground/50'}`} />
                                                                <span className={`text-xs ${agent.enabled ? '' : 'text-muted-foreground line-through'}`}>
                                                                    {agent.name}
                                                                </span>
                                                            </div>
                                                        ))}
                                                    </div>
                                                ) : activeAgentMembers.length === 0 ? (
                                                    <span className="text-xs text-muted-foreground/60">None</span>
                                                ) : (
                                                    <div className="flex flex-wrap gap-1.5">
                                                        {activeAgentMembers.map(member => (
                                                            <div
                                                                key={member.id}
                                                                className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-blue-500/10 border border-blue-300/30 dark:border-blue-600/30"
                                                            >
                                                                <Bot className="size-3 text-blue-500" />
                                                                <span className="text-xs">{member.fullName}</span>
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>

                                            {/* Manage button */}
                                            <div className="flex items-center justify-between pt-1 border-t border-border/50">
                                                <span className="text-xs text-muted-foreground">
                                                    {(() => {
                                                        const enabledAgents = hasAgentConfigs ? agentConfigs.filter(a => a.enabled).length : activeAgentMembers.length;
                                                        const disabledAgents = hasAgentConfigs ? agentConfigs.filter(a => !a.enabled).length : 0;
                                                        const activeCount = humanMembers.length + enabledAgents;
                                                        return (
                                                            <>
                                                                {activeCount} active{' '}
                                                                {disabledAgents > 0 && (
                                                                    <span className="text-muted-foreground/50">
                                                                        · {disabledAgents} disabled
                                                                    </span>
                                                                )}
                                                            </>
                                                        );
                                                    })()}
                                                </span>
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    className="gap-1.5"
                                                    onClick={() => setManagingBoard(board)}
                                                >
                                                    <Users className="size-3.5" />
                                                    Manage
                                                </Button>
                                            </div>
                                        </>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* Clear Board Confirmation */}
            <AlertDialog open={!!boardToClear} onOpenChange={(open) => !open && setBoardToClear(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Clear board</AlertDialogTitle>
                        <AlertDialogDescription>
                            This will permanently delete <strong>all tasks and specs</strong> from
                            "{boardToClear?.name}". The board itself will not be deleted. This action
                            cannot be undone.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={clearing}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={handleClear}
                            disabled={clearing}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {clearing ? 'Clearing...' : 'Clear all data'}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            {/* Delete Board — Double Confirm */}
            <AlertDialog
                open={!!boardToDelete}
                onOpenChange={(open) => {
                    if (!open) {
                        setBoardToDelete(null);
                        setDeleteConfirmStep(1);
                        setDeleteConfirmName('');
                    }
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            {deleteConfirmStep === 1 ? 'Delete board' : 'Confirm deletion'}
                        </AlertDialogTitle>
                        <AlertDialogDescription asChild>
                            {deleteConfirmStep === 1 ? (
                                <p>
                                    This will permanently delete <strong>"{boardToDelete?.name}"</strong> and
                                    all its tasks, specs, and data. This action cannot be undone.
                                </p>
                            ) : (
                                <div className="space-y-3">
                                    <p>
                                        Type <strong>{boardToDelete?.name}</strong> to confirm deletion.
                                    </p>
                                    <Input
                                        value={deleteConfirmName}
                                        onChange={e => setDeleteConfirmName(e.target.value)}
                                        placeholder={boardToDelete?.name}
                                        className="font-mono"
                                        autoFocus
                                        onKeyDown={e => {
                                            if (e.key === 'Enter' && deleteConfirmName === boardToDelete?.name) {
                                                handleDeleteBoard();
                                            }
                                        }}
                                    />
                                </div>
                            )}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        {deleteConfirmStep === 1 ? (
                            <AlertDialogAction
                                onClick={(e) => { e.preventDefault(); setDeleteConfirmStep(2); }}
                                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            >
                                Continue
                            </AlertDialogAction>
                        ) : (
                            <AlertDialogAction
                                onClick={(e) => { e.preventDefault(); handleDeleteBoard(); }}
                                disabled={deleting || deleteConfirmName !== boardToDelete?.name}
                                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            >
                                {deleting ? 'Deleting...' : 'Delete permanently'}
                            </AlertDialogAction>
                        )}
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            {/* Manage Members Modal */}
            {managingBoard && (
                <ManageMembersModal
                    board={managingBoard}
                    members={members}
                    onClose={() => setManagingBoard(null)}
                    onDataChange={() => {
                        onDataChange();
                        // Reload agent configs to refresh enabled/disabled status in summary
                        if (managingBoard.odinInitialized) loadBoardAgents(managingBoard);
                    }}
                />
            )}
        </div>
    );
}
