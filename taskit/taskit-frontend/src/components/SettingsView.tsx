import React, { useState, useEffect, useMemo } from 'react';
import type { Board, Member } from '../types';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
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
import { Trash2, FlaskConical, Bot, FolderOpen, CheckCircle2, AlertCircle, Zap, Plus, Sparkles, Users, ChevronDown, ChevronUp, MoreVertical, Search, FileText, Layout, X } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { useNavigate } from 'react-router-dom';
import { ManageMembersModal } from './ManageMembersModal';

import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';

import { NotificationSettings } from './NotificationSettings';


interface SettingsViewProps {
    members: Member[];
    currentBoard: Board | null;
    onDataChange: () => void;
    onCreateBoard: () => void;
    onDeleteBoard: (boardId: string) => Promise<void>;
}

export function SettingsView({ members, currentBoard, onDataChange, onCreateBoard, onDeleteBoard }: SettingsViewProps) {
    const service = useService();
    const { toast } = useToast();
    const navigate = useNavigate();
    const [boardToClear, setBoardToClear] = useState<Board | null>(null);
    const [clearing, setClearing] = useState(false);
    const [forcedProvider, setForcedProvider] = useState<{ provider: string | null; model: string | null } | null>(null);

    // Manage members modal
    const [managingBoard, setManagingBoard] = useState<Board | null>(null);

    const [initializingBoard, setInitializingBoard] = useState<string | null>(null);

    // Board deletion — double confirmation
    const [boardToDelete, setBoardToDelete] = useState<Board | null>(null);
    const [deleteConfirmStep, setDeleteConfirmStep] = useState<1 | 2>(1);
    const [deleteConfirmName, setDeleteConfirmName] = useState('');
    const [deleting, setDeleting] = useState(false);

    // Agent configs per board (for showing enabled/disabled status in summary)
    const [membersByBoard, setMembersByBoard] = useState<Record<string, Member[]>>({});

    // Table UI state
    const [searchQuery, setSearchQuery] = useState('');
    const [currentPage, setCurrentPage] = useState(1);
    const [sortConfig, setSortConfig] = useState<{ key: 'id' | 'name' | 'tasks' | 'members', dir: 'asc' | 'desc' }>({ key: 'id', dir: 'asc' });
    const [expandedBoardId, setExpandedBoardId] = useState<string | null>(null);

    const [reflectionModelEdits, setReflectionModelEdits] = useState<Record<string, string>>({});

    // Current board reflection settings (top section)
    const [currentSkip, setCurrentSkip] = useState(currentBoard?.skipReflection ?? false);
    const [currentModel, setCurrentModel] = useState(currentBoard?.reflectionModel || '');
    useEffect(() => {
        setCurrentSkip(currentBoard?.skipReflection ?? false);
        setCurrentModel(currentBoard?.reflectionModel || '');
    }, [currentBoard?.id]);

    const allModels = useMemo(() => {
        const seen = new Set<string>();
        return members.flatMap(m => m.availableModels || []).filter(m => {
            if (seen.has(m.name)) return false;
            seen.add(m.name);
            return true;
        });
    }, [members]);

    // Backend-driven table data
    const [tableBoards, setTableBoards] = useState<Board[]>([]);
    const [totalFiltered, setTotalFiltered] = useState(0);
    const [isLoadingTable, setIsLoadingTable] = useState(false);

    const PAGE_SIZE = 10;

    useEffect(() => {
        if (!expandedBoardId) return;
        let active = true;
        service.fetchBoardMembers(expandedBoardId)
            .then(members => {
                if (active) setMembersByBoard(prev => ({ ...prev, [expandedBoardId]: members }));
            })
            .catch(() => {});
        return () => { active = false; };
    }, [expandedBoardId, service]);

    useEffect(() => {
        let active = true;
        setIsLoadingTable(true);

        const fetchTableData = async () => {
            const sortToken = sortConfig.dir === 'asc' ? sortConfig.key : `-${sortConfig.key}`;
            const mappedSortToken = sortToken.replace('tasks', 'task_count').replace('members', 'member_count');

            try {
                const response = await service.fetchBoardsPage({
                    page: currentPage,
                    page_size: PAGE_SIZE,
                    search: searchQuery || undefined,
                    sort: mappedSortToken
                });

                if (active) {
                    setTableBoards(response.results);
                    setTotalFiltered(response.count);
                    setIsLoadingTable(false);
                }
            } catch (error) {
                console.error('Failed to fetch boards page', error);
                if (active) setIsLoadingTable(false);
            }
        };

        fetchTableData();

        return () => { active = false; };
    }, [service, currentPage, searchQuery, sortConfig]);

    const totalPages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE));

    const handleSort = (key: 'id' | 'name' | 'tasks' | 'members') => {
        setSortConfig(prev => ({
            key,
            dir: prev.key === key && prev.dir === 'asc' ? 'desc' : 'asc'
        }));
    };

    useEffect(() => {
        setCurrentPage(1);
    }, [searchQuery]);

    useEffect(() => {
        let active = true;
        service.fetchForcedProviderStatus()
            .then(status => {
                if (!active) return;
                setForcedProvider(status.enabled ? { provider: status.provider, model: status.model } : null);
            })
            .catch(() => {
                if (!active) return;
                setForcedProvider(null);
            });
        return () => { active = false; };
    }, [service]);

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

    const handleUpdateBoardReflection = async (boardId: string, updates: { skip_reflection?: boolean; reflection_model?: string }) => {
        try {
            await service.updateBoard(boardId, updates);
            setTableBoards(prev => prev.map(b => b.id === boardId ? {
                ...b,
                ...(updates.skip_reflection !== undefined && { skipReflection: updates.skip_reflection }),
                ...(updates.reflection_model !== undefined && { reflectionModel: updates.reflection_model }),
            } : b));
            toast({ title: 'Board updated' });
        } catch {
            toast({ title: 'Error', description: 'Failed to update board settings.', variant: 'destructive' });
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

    const handleCurrentBoardReflection = async (updates: { skip_reflection?: boolean; reflection_model?: string }) => {
        if (!currentBoard) return;
        if (updates.skip_reflection !== undefined) setCurrentSkip(updates.skip_reflection);
        if (updates.reflection_model !== undefined) setCurrentModel(updates.reflection_model);
        try {
            await service.updateBoard(currentBoard.id, updates);
            toast({ title: 'Board updated' });
        } catch {
            // revert
            setCurrentSkip(currentBoard.skipReflection ?? false);
            setCurrentModel(currentBoard.reflectionModel || '');
            toast({ title: 'Error', description: 'Failed to update reflection settings.', variant: 'destructive' });
        }
    };

    const isAgentUser = (member: Member) => member.email.endsWith('@odin.agent');

    return (
        <div className="space-y-8">
            {currentBoard && (
                <div>
                    <h3 className="text-sm font-medium text-muted-foreground mb-3">Reflection Settings</h3>
                    <div className="border border-border rounded-md p-4 flex flex-col gap-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <div className="text-sm font-medium">{currentBoard.name}</div>
                                <div className="text-xs text-muted-foreground mt-0.5">Skip reflection for all tasks in this board</div>
                            </div>
                            <Switch
                                checked={currentSkip}
                                onCheckedChange={v => handleCurrentBoardReflection({ skip_reflection: v })}
                            />
                        </div>
                        <div className="flex items-center gap-3">
                            <span className="text-sm text-muted-foreground shrink-0">Reflection model</span>
                            <Select
                                value={currentModel || ''}
                                onValueChange={v => handleCurrentBoardReflection({ reflection_model: v })}
                            >
                                <SelectTrigger className="h-8 text-xs font-mono flex-1">
                                    <SelectValue placeholder="claude-sonnet-4-5-20250929" />
                                </SelectTrigger>
                                <SelectContent>
                                    {allModels.map(m => (
                                        <SelectItem key={m.name} value={m.name}>
                                            <span className="font-mono">{m.name}</span>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                </div>
            )}
            {forcedProvider?.provider && (
                <div className="rounded-lg border border-border bg-muted/30 px-4 py-3">
                    <div className="text-xs font-medium text-muted-foreground">Forced AI Provider</div>
                    <div className="mt-1 text-sm">
                        <span className="font-mono">{forcedProvider.provider}</span>
                        {forcedProvider.model && <span className="text-muted-foreground"> / <span className="font-mono">{forcedProvider.model}</span></span>}
                    </div>
                </div>
            )}
            {/* Boards Section */}
            <div>
                <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-medium text-muted-foreground">Boards</h3>
                    <div className="flex items-center gap-2">
                        <div className="relative w-64">
                            <form onSubmit={(e) => {
                                e.preventDefault();
                                setCurrentPage(1);
                            }}>
                                <Search className="absolute left-2 top-2 size-3.5 text-muted-foreground" />
                                <Input
                                    placeholder="Search boards by ID or name..."
                                    className="h-8 pl-8 pr-8 text-xs bg-background"
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                />
                                {searchQuery && (
                                    <button
                                        type="button"
                                        onClick={() => setSearchQuery('')}
                                        className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
                                    >
                                        <X className="size-3.5" />
                                    </button>
                                )}
                            </form>
                        </div>
                        <Button variant="outline" size="sm" className="gap-1.5" onClick={onCreateBoard}>
                            <Plus className="size-3.5" />
                            Create Board
                        </Button>
                    </div>
                </div>
                
                <div className="border border-border rounded-md overflow-hidden text-sm">
                    <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse">
                            <thead className="bg-muted/30 border-b border-border text-xs text-muted-foreground">
                                <tr>
                                    <th className="px-3 py-2 font-medium cursor-pointer hover:text-foreground" onClick={() => handleSort('id')}>
                                        <div className="flex items-center gap-1">ID {sortConfig.key === 'id' && (sortConfig.dir === 'asc' ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />)}</div>
                                    </th>
                                    <th className="px-3 py-2 font-medium cursor-pointer hover:text-foreground" onClick={() => handleSort('name')}>
                                        <div className="flex items-center gap-1">Board {sortConfig.key === 'name' && (sortConfig.dir === 'asc' ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />)}</div>
                                    </th>
                                    <th className="px-3 py-2 font-medium">Status</th>
                                    <th className="px-3 py-2 font-medium cursor-pointer hover:text-foreground" onClick={() => handleSort('tasks')}>
                                        <div className="flex items-center gap-1">Tasks {sortConfig.key === 'tasks' && (sortConfig.dir === 'asc' ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />)}</div>
                                    </th>
                                    <th className="px-3 py-2 font-medium cursor-pointer hover:text-foreground" onClick={() => handleSort('members')}>
                                        <div className="flex items-center gap-1">Members {sortConfig.key === 'members' && (sortConfig.dir === 'asc' ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />)}</div>
                                    </th>
                                    <th className="px-3 py-2 font-medium">Agents</th>
                                    <th className="px-3 py-2 font-medium text-center w-24">Manage</th>
                                    <th className="px-3 py-2 font-medium text-center w-24">Details</th>
                                    <th className="px-3 py-2 w-10"></th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-border/50">
                                {isLoadingTable ? (
                                    <tr>
                                        <td colSpan={9} className="px-6 py-12 text-center text-muted-foreground">
                                            <Layout className="h-8 w-8 text-muted-foreground mx-auto mb-3 animate-pulse" />
                                            Loading...
                                        </td>
                                    </tr>
                                ) : tableBoards.length === 0 ? (
                                    <tr>
                                        <td colSpan={9} className="px-6 py-12 text-center text-muted-foreground">
                                            <Layout className="h-8 w-8 text-muted-foreground mx-auto mb-3 opacity-50" />
                                            No boards found matching your search.
                                        </td>
                                    </tr>
                                ) : (
                                    tableBoards.map(board => {
                                        const boardMembers = membersByBoard[board.id] || board.members;
                                        const humanMembers = boardMembers.filter(m => !isAgentUser(m));
                                        const bAgents = board.agents || [];
                                        const activeAgentMembers = boardMembers.filter(m => isAgentUser(m));
                                        const hasAgentConfigs = bAgents.length > 0;
                                        const hasAnyMembers = humanMembers.length > 0 || activeAgentMembers.length > 0 || bAgents.length > 0;
                                        const isExpanded = expandedBoardId === board.id;

                                        const enabledAgents = hasAgentConfigs ? bAgents.filter(a => a.enabled).length : activeAgentMembers.length;
                                        
                                        return (
                                            <React.Fragment key={board.id}>
                                                <tr className={`hover:bg-muted/10 transition-colors ${isExpanded ? 'bg-muted/10' : ''}`}>
                                                    <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{board.id}</td>
                                                    <td className="px-3 py-2 font-medium">{board.name}</td>
                                                    <td className="px-3 py-2">
                                                        {board.isTrial && (
                                                            <Badge variant="outline" className="text-[10px] gap-1 px-1.5 py-0 text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600">
                                                                <FlaskConical className="size-2.5" /> Trial
                                                            </Badge>
                                                        )}
                                                        {board.odinInitialized ? (
                                                            <Badge variant="outline" className="text-[10px] gap-1 px-1.5 py-0 text-emerald-600 border-emerald-300 dark:text-emerald-400 dark:border-emerald-600 whitespace-nowrap">
                                                                <CheckCircle2 className="size-2.5" /> Initialized
                                                            </Badge>
                                                        ) : board.workingDir ? (
                                                            <Badge variant="outline" className="text-[10px] gap-1 px-1.5 py-0 text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600 whitespace-nowrap">
                                                                <AlertCircle className="size-2.5" /> Not Init
                                                            </Badge>
                                                        ) : null}
                                                    </td>
                                                    <td className="px-3 py-2 text-muted-foreground">{board.taskCount ?? 0}</td>
                                                    <td className="px-3 py-2 text-muted-foreground">{board.memberCount ?? boardMembers.length}</td>
                                                    <td className="px-3 py-2 text-muted-foreground">{enabledAgents}</td>
                                                    <td className="px-3 py-2 text-center">
                                                        <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => setManagingBoard(board)}>
                                                            <Users className="size-3.5 mr-1.5" /> Manage
                                                        </Button>
                                                    </td>
                                                    <td className="px-3 py-2 text-center">
                                                        <Button 
                                                            variant="ghost" 
                                                            size="sm" 
                                                            className={`h-7 px-2 ${isExpanded ? 'bg-muted' : ''}`}
                                                            onClick={() => setExpandedBoardId(isExpanded ? null : board.id)}
                                                        >
                                                            <FileText className="size-3.5 mr-1" /> View
                                                        </Button>
                                                    </td>
                                                    <td className="px-3 py-2">
                                                        <Popover>
                                                            <PopoverTrigger asChild>
                                                                <Button variant="ghost" size="sm" className="h-7 w-7 p-0 ml-auto flex">
                                                                    <MoreVertical className="size-4" />
                                                                </Button>
                                                            </PopoverTrigger>
                                                            <PopoverContent align="end" className="w-56 p-1">
                                                                {board.workingDir && !board.odinInitialized && (
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="sm"
                                                                        className="w-full justify-start gap-2"
                                                                        disabled={initializingBoard === board.id}
                                                                        onClick={() => handleInitOdin(board)}
                                                                    >
                                                                        <Zap className="size-4" />
                                                                        {initializingBoard === board.id ? 'Initializing...' : 'Initialize Odin'}
                                                                    </Button>
                                                                )}
                                                                <Button
                                                                    variant="ghost"
                                                                    size="sm"
                                                                    className="w-full justify-start gap-2"
                                                                    onClick={() => navigate(`/reflections?board=${board.id}`)}
                                                                >
                                                                    <Sparkles className="size-4" /> Reflections
                                                                </Button>
                                                                <div className="h-px bg-border my-1 mx-2" />
                                                                <Button
                                                                    variant="ghost"
                                                                    size="sm"
                                                                    className="w-full justify-start gap-2 text-destructive hover:text-destructive hover:bg-destructive/10"
                                                                    onClick={() => setBoardToClear(board)}
                                                                >
                                                                    <Trash2 className="size-4" /> Clear Board
                                                                </Button>
                                                                <Button
                                                                    variant="ghost"
                                                                    size="sm"
                                                                    className="w-full justify-start gap-2 text-destructive hover:text-destructive hover:bg-destructive/10"
                                                                    onClick={() => { setBoardToDelete(board); setDeleteConfirmStep(1); setDeleteConfirmName(''); }}
                                                                >
                                                                    <Trash2 className="size-4" /> Delete Board
                                                                </Button>
                                                            </PopoverContent>
                                                        </Popover>
                                                    </td>
                                                </tr>
                                                {isExpanded && (
                                                    <tr className="bg-muted/20 border-b border-border">
                                                        <td colSpan={9} className="p-0">
                                                            <div className="p-4 flex gap-6 text-sm flex-col">
                                                                <div className="flex gap-2">
                                                                    <FolderOpen className="size-4 text-muted-foreground shrink-0 mt-0.5" />
                                                                    <div>
                                                                        <div className="font-medium mb-0.5">Project Directory</div>
                                                                        {board.workingDir ? (
                                                                            <div className="text-xs font-mono text-foreground/80">{board.workingDir}</div>
                                                                        ) : (
                                                                            <div className="text-xs text-muted-foreground/60">No project directory set</div>
                                                                        )}
                                                                    </div>
                                                                </div>
                                                                
                                                                {hasAnyMembers && (
                                                                    <div className={"grid grid-cols-[100px_1fr] gap-4 items-start"}>
                                                                        <div className="font-medium text-xs text-muted-foreground uppercase tracking-wider mt-1">People</div>
                                                                        {humanMembers.length === 0 ? (
                                                                            <span className="text-xs text-muted-foreground/60 mt-1">None</span>
                                                                        ) : (
                                                                            <div className="flex flex-wrap gap-1.5">
                                                                                {humanMembers.map(member => (
                                                                                    <div key={member.id} className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-background border border-border">
                                                                                        <div className="size-4 rounded-full flex items-center justify-center text-[8px] font-medium text-white shrink-0" style={{ backgroundColor: member.color }}>
                                                                                            {member.initials}
                                                                                        </div>
                                                                                        <span className="text-xs">{member.fullName}</span>
                                                                                    </div>
                                                                                ))}
                                                                            </div>
                                                                        )}

                                                                        <div className="font-medium text-xs text-muted-foreground uppercase tracking-wider mt-1">Agents</div>
                                                                        {hasAgentConfigs ? (
                                                                            <div className="flex flex-wrap gap-1.5">
                                                                                {bAgents.map(agent => (
                                                                                    <div key={agent.name} className={`flex items-center gap-1.5 px-2 py-1 rounded-full border ${agent.enabled ? 'bg-blue-500/10 border-blue-300/30' : 'bg-background border-border opacity-50'}`}>
                                                                                        <Bot className={`size-3 ${agent.enabled ? 'text-blue-500' : 'text-muted-foreground/50'}`} />
                                                                                        <span className={`text-xs ${agent.enabled ? '' : 'text-muted-foreground line-through'}`}>{agent.name}</span>
                                                                                    </div>
                                                                                ))}
                                                                            </div>
                                                                        ) : activeAgentMembers.length > 0 ? (
                                                                            <div className="flex flex-wrap gap-1.5">
                                                                                {activeAgentMembers.map(member => (
                                                                                    <div key={member.id} className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-blue-500/10 border border-blue-300/30">
                                                                                        <Bot className="size-3 text-blue-500" />
                                                                                        <span className="text-xs">{member.fullName}</span>
                                                                                    </div>
                                                                                ))}
                                                                            </div>
                                                                        ) : (
                                                                            <span className="text-xs text-muted-foreground/60 mt-1">None</span>
                                                                        )}
                                                                    </div>
                                                                )}
                                                                <div className="flex gap-2">
                                                                    <Sparkles className="size-4 text-muted-foreground shrink-0 mt-0.5" />
                                                                    <div className="flex-1">
                                                                        <div className="font-medium mb-2">Reflection Settings</div>
                                                                        <div className="flex flex-col gap-3">
                                                                            <div className="flex items-center justify-between">
                                                                                <span className="text-xs text-muted-foreground">Skip reflection for all tasks in this board</span>
                                                                                <Switch
                                                                                    checked={!!board.skipReflection}
                                                                                    onCheckedChange={v => handleUpdateBoardReflection(board.id, { skip_reflection: v })}
                                                                                />
                                                                            </div>
                                                                            <div className="flex items-center gap-2">
                                                                                <Select
                                                                                    value={reflectionModelEdits[board.id] !== undefined ? reflectionModelEdits[board.id] : (board.reflectionModel || '')}
                                                                                    onValueChange={val => {
                                                                                        setReflectionModelEdits(prev => ({ ...prev, [board.id]: val }));
                                                                                        handleUpdateBoardReflection(board.id, { reflection_model: val });
                                                                                    }}
                                                                                >
                                                                                    <SelectTrigger className="h-7 text-xs font-mono flex-1">
                                                                                        <SelectValue placeholder="claude-sonnet-4-5-20250929" />
                                                                                    </SelectTrigger>
                                                                                    <SelectContent>
                                                                                        {allModels.map(m => (
                                                                                            <SelectItem key={m.name} value={m.name}>
                                                                                                <span className="font-mono">{m.name}</span>
                                                                                            </SelectItem>
                                                                                        ))}
                                                                                    </SelectContent>
                                                                                </Select>
                                                                                <span className="text-xs text-muted-foreground whitespace-nowrap">Reflection model</span>
                                                                            </div>
                                                                        </div>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        </td>
                                                    </tr>
                                                )}
                                            </React.Fragment>
                                        );
                                    })
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>

                {/* Pagination Controls */}
                {totalFiltered > PAGE_SIZE && (
                    <div className="flex items-center justify-between mt-4">
                        <div className="text-xs text-muted-foreground">
                            Showing {Math.min((currentPage - 1) * PAGE_SIZE + 1, totalFiltered)}–{Math.min(currentPage * PAGE_SIZE, totalFiltered)} of {totalFiltered} board{totalFiltered !== 1 ? 's' : ''}
                        </div>
                        <div className="flex items-center gap-1">
                            <Button 
                                variant="outline" 
                                size="sm" 
                                className="h-8" 
                                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                                disabled={currentPage === 1}
                            >
                                Previous
                            </Button>
                            <div className="flex items-center gap-1 px-2">
                                {Array.from({ length: totalPages }).map((_, i) => (
                                    <Button
                                        key={i + 1}
                                        variant={currentPage === i + 1 ? 'default' : 'ghost'}
                                        size="sm"
                                        className="h-8 w-8 p-0"
                                        onClick={() => setCurrentPage(i + 1)}
                                    >
                                        {i + 1}
                                    </Button>
                                ))}
                            </div>
                            <Button 
                                variant="outline" 
                                size="sm" 
                                className="h-8" 
                                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                                disabled={currentPage === totalPages}
                            >
                                Next
                            </Button>
                        </div>
                    </div>
                )}
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

            {/* Notification Settings */}
            <div id="notifications" className="mt-8">
                <h2 className="text-lg font-semibold mb-4">Notifications</h2>
                <NotificationSettings />
            </div>

            {/* Manage Members Modal */}
            {managingBoard && (
                <ManageMembersModal
                    board={managingBoard}
                    members={members}
                    onClose={() => setManagingBoard(null)}
                    onDataChange={() => {
                        onDataChange();
                    }}
                />
            )}
        </div>
    );
}
