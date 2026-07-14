import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import React from 'react';
import type { Board, DetectedIde, Member, RoutingConfig } from '../types';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useService } from '../contexts/ServiceContext';
import { useAuth } from '../contexts/AuthContext';
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
import { Trash2, FlaskConical, Bot, FolderOpen, CheckCircle2, AlertCircle, Zap, Plus, Sparkles, Users, ChevronDown, ChevronUp, MoreVertical, Search, FileText, Layout, X, SettingsIcon, Moon, Sun, Keyboard, Bell, GripVertical, ShieldAlert, UserCircle, LogOut, Cpu } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ManageMembersModal } from './ManageMembersModal';
import { IdeBadge, IdeSetupModal } from './IdeSetupModal';

import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';

import { NotificationSettings } from './NotificationSettings';
import { ExecutorCapacitySettings } from './ExecutorCapacitySettings';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
    DndContext,
    closestCenter,
    KeyboardSensor,
    PointerSensor,
    useSensor,
    useSensors,
} from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import {
    SortableContext,
    sortableKeyboardCoordinates,
    verticalListSortingStrategy,
    useSortable,
    arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

const isMac = () => navigator.platform.toUpperCase().includes('MAC');

function Kbd({ children }: { children: React.ReactNode }) {
    return (
        <kbd className="inline-flex h-5 items-center rounded border border-border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground pointer-events-none">
            {children}
        </kbd>
    );
}

type ShortcutRow = { label: string; keys: string[] };
type ShortcutGroup = { heading: string; rows: ShortcutRow[] };

function KeyboardShortcutsContent() {
    const modKey = isMac() ? '⌘' : 'Ctrl';

    const groups: ShortcutGroup[] = [
        {
            heading: 'Navigation',
            rows: [
                { label: 'Go to Board', keys: ['G', 'B'] },
                { label: 'Go to Specs', keys: ['G', 'S'] },
                { label: 'Go to Stats', keys: ['G', 'D'] },
                { label: 'Go to Notifications', keys: ['G', 'N'] },
                { label: 'Go to Settings', keys: ['G', 'T'] },
            ],
        },
        {
            heading: 'Tasks',
            rows: [
                { label: 'Create new task', keys: ['N'] },
            ],
        },
        {
            heading: 'System',
            rows: [
                { label: 'Open command palette', keys: [`${modKey}K`] },
                { label: 'Show keyboard shortcuts', keys: ['?'] },
            ],
        },
    ];

    return (
        <div className="space-y-4">
            {groups.map((group) => (
                <div key={group.heading}>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 mb-2">
                        {group.heading}
                    </div>
                    <div className="space-y-2">
                        {group.rows.map((row) => (
                            <div key={row.label} className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
                                <span className="text-sm text-foreground/80">{row.label}</span>
                                <div className="flex items-center gap-1">
                                    {row.keys.map((k, i) => (
                                        <Kbd key={i}>{k}</Kbd>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );
}


function SortableEscalationRow({ id, entry, index, highlight }: { id: string; entry: { agent_name: string; model_name: string }; index: number; highlight: boolean }) {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
        zIndex: isDragging ? 50 : undefined,
    };
    return (
        <div
            ref={setNodeRef}
            style={style}
            className={`flex items-center gap-2 rounded-md border px-2.5 py-2 text-sm cursor-grab active:cursor-grabbing transition-colors ${
                isDragging
                    ? 'border-primary/40 bg-primary/5 shadow-md'
                    : highlight
                        ? 'border-amber-400/50 bg-amber-500/10'
                        : 'border-border bg-muted/20'
            }`}
            {...attributes}
            {...listeners}
        >
            <GripVertical className="size-3.5 text-muted-foreground/30 shrink-0" />
            <span className="text-[10px] w-5 text-right font-mono shrink-0 text-muted-foreground/50">
                {index + 1}
            </span>
            <span className="font-mono text-xs flex-1 truncate">
                {entry.model_name}
            </span>
            <Badge variant="outline" className="text-[10px] h-4 px-1.5 shrink-0">
                {entry.agent_name}
            </Badge>
        </div>
    );
}

// task #328: human-readable label + color for each automatic failure
// action, keyed by the backend's `routing_policy.failure_actions[cls].action`.
// Kept alongside the component that renders them — not worth a shared
// module for three hardcoded strings.
const ROUTING_ACTION_META: Record<string, { label: string; className: string }> = {
    auto_requeue: {
        label: 'retry same agent, then same-tier peer (no tier jump)',
        className: 'text-blue-600 border-blue-300 dark:text-blue-400 dark:border-blue-600',
    },
    reassign: {
        label: 'reassign to fallback',
        className: 'text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600',
    },
    human: {
        label: 'hold for human review',
        className: 'text-rose-600 border-rose-300 dark:text-rose-400 dark:border-rose-600',
    },
};

interface SettingsViewProps {
    members: Member[];
    currentBoard: Board | null;
    onDataChange: () => void;
    onCreateBoard: () => void;
    onDeleteBoard: (boardId: string) => Promise<void>;
    dark: boolean;
    onToggleDark: () => void;
}

const TAB_IDS = ['board', 'boards', 'ide', 'appearance', 'notifications', 'account', 'shortcuts'] as const;
type TabId = typeof TAB_IDS[number];

export function SettingsView({ members, currentBoard, onDataChange, onCreateBoard, onDeleteBoard, dark, onToggleDark }: SettingsViewProps) {
    const service = useService();
    const { toast } = useToast();
    const navigate = useNavigate();
    const { user: authUser, authEnabled, logout, changePassword } = useAuth();
    const [pwOld, setPwOld] = useState('');
    const [pwNew, setPwNew] = useState('');
    const [pwConfirm, setPwConfirm] = useState('');
    const [pwSaving, setPwSaving] = useState(false);

    const handleChangePassword = async () => {
        if (pwNew !== pwConfirm) {
            toast({ title: 'Passwords do not match', variant: 'destructive' });
            return;
        }
        if (pwNew.length < 8) {
            toast({ title: 'New password must be at least 8 characters', variant: 'destructive' });
            return;
        }
        setPwSaving(true);
        try {
            await changePassword(pwOld, pwNew);
            setPwOld(''); setPwNew(''); setPwConfirm('');
            toast({ title: 'Password changed' });
        } catch {
            toast({ title: 'Error', description: 'Could not change password. Check your current password.', variant: 'destructive' });
        } finally {
            setPwSaving(false);
        }
    };
    const [searchParams, setSearchParams] = useSearchParams();

    // Tab state from URL
    const rawTab = searchParams.get('tab');
    let activeTab: TabId = (rawTab && TAB_IDS.includes(rawTab as TabId)) ? rawTab as TabId : 'board';
    if (activeTab === 'account' && !authEnabled) activeTab = 'board';

    const handleTabChange = useCallback((value: string) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (value === 'board') {
                next.delete('tab');
            } else {
                next.set('tab', value);
            }
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    const [boardToClear, setBoardToClear] = useState<Board | null>(null);
    const [clearing, setClearing] = useState(false);
    const [forcedProvider, setForcedProvider] = useState<{ provider: string | null; model: string | null } | null>(null);
    const [detectedIdes, setDetectedIdes] = useState<DetectedIde[]>([]);
    const [preferredIdeId, setPreferredIdeId] = useState<string | null>(null);
    const [loadingIdeOptions, setLoadingIdeOptions] = useState(false);
    const [showIdeSetup, setShowIdeSetup] = useState(false);
    const [savingIde, setSavingIde] = useState(false);

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


    // Current board reflection settings (top section)
    const [currentSkip, setCurrentSkip] = useState(currentBoard?.skipReflection ?? false);
    const [currentSkipProof, setCurrentSkipProof] = useState(currentBoard?.skipProof ?? false);
    const [currentAutoStart, setCurrentAutoStart] = useState(currentBoard?.autoStartPlannedTasks ?? false);
    const [claudeToken, setClaudeToken] = useState('');
    const [claudeTokenSaving, setClaudeTokenSaving] = useState(false);
    const [claudeTokenConfigured, setClaudeTokenConfigured] = useState(currentBoard?.claudeTokenConfigured ?? false);
    const [escalationPriority, setEscalationPriority] = useState<Array<{ agent_name: string; model_name: string }>>(
        currentBoard?.modelEscalationPriority || []
    );
    const [escalationEnabled, setEscalationEnabled] = useState(currentBoard?.escalationEnabled ?? true);
    const [failureMaxRetries, setFailureMaxRetries] = useState(currentBoard?.failureMaxRetries ?? 3);
    const [escalationSearch, setEscalationSearch] = useState('');
    const [escalationPrunedNote, setEscalationPrunedNote] = useState<string | null>(null);
    const [reviewerOrder, setReviewerOrder] = useState<Array<{ agent_name: string; model_name: string }>>(
        currentBoard?.reviewerOrder || []
    );
    const [reviewerOrderPrunedNote, setReviewerOrderPrunedNote] = useState<string | null>(null);
    const prunedForBoardRef = useRef<string | null>(null);

    // Routing (task #328) — effective policy for display, and the editable
    // standing preference order (board.routing_policy.preference_order).
    const [routingConfig, setRoutingConfig] = useState<RoutingConfig | null>(null);
    const [routingConfigLoading, setRoutingConfigLoading] = useState(false);
    const [preferenceOrder, setPreferenceOrder] = useState<string[]>([]);
    // The board's live routing_policy blob. Every routing edit merges onto
    // this ref (not the stale prop) so sequential edits across the different
    // controls in the Routing section don't clobber each other. Seeded from
    // the board on selection.
    const routingPolicyRef = useRef<Record<string, unknown>>({});
    const escalationSensors = useSensors(
        useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
    );
    const reviewerOrderSensors = useSensors(
        useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
    );

    // Enabled agents/models for the CURRENT board, sorted by output price
    // descending — the single source of truth for both Model Escalation's
    // and Reviewer order's default lists. Board membership (`members`) is
    // already board-scoped by the caller; here we additionally filter by
    // the board's per-agent enabled toggle (currentBoard.agents[].enabled),
    // which membership alone doesn't capture.
    const computeDefaultOrder = useCallback((): Array<{ agent_name: string; model_name: string }> => {
        const generated: Array<{ agent_name: string; model_name: string; price: number }> = [];
        for (const agent of currentBoard?.agents || []) {
            if (!agent.enabled) continue;
            for (const m of agent.models) {
                if (!m.enabled) continue;
                const info = members.flatMap(mb => mb.availableModels || []).find(am => am.name === m.name);
                generated.push({ agent_name: agent.name, model_name: m.name, price: info?.output_price_per_1m_tokens ?? 0 });
            }
        }
        generated.sort((a, b) => b.price - a.price);
        return generated.map(({ agent_name, model_name }) => ({ agent_name, model_name }));
    }, [currentBoard?.agents, members]);

    const hasAgentsData = !!currentBoard?.agents;

    // Registry of model names available to agents currently enabled on this
    // board — used to scope model pickers and to detect stale saved entries
    // (models retired from the curated registry, or agents no longer
    // enabled on the board).
    const enabledAgentModelNames = useMemo(() => {
        const boardAgents = currentBoard?.agents || [];
        const enabledAgentKeys = new Set(boardAgents.filter(a => a.enabled).map(a => a.name.toLowerCase()));
        const names = new Set<string>();
        for (const mb of members) {
            const key = (mb.email || '').split('@')[0].toLowerCase();
            if (boardAgents.length > 0 && !enabledAgentKeys.has(key)) continue;
            for (const am of mb.availableModels || []) names.add(am.name);
        }
        return names;
    }, [members, currentBoard?.agents]);

    useEffect(() => {
        setCurrentSkip(currentBoard?.skipReflection ?? false);
        setCurrentSkipProof(currentBoard?.skipProof ?? false);
        setClaudeTokenConfigured(currentBoard?.claudeTokenConfigured ?? false);
        setClaudeToken('');
        setCurrentAutoStart(currentBoard?.autoStartPlannedTasks ?? false);
        setEscalationEnabled(currentBoard?.escalationEnabled ?? true);
        setFailureMaxRetries(currentBoard?.failureMaxRetries ?? 3);

        const saved = currentBoard?.modelEscalationPriority;
        if (saved && saved.length > 0) {
            setEscalationPriority(saved);
        } else if (currentBoard?.agents) {
            // Auto-populate from enabled agents/models sorted by output price descending
            const defaultList = computeDefaultOrder();
            setEscalationPriority(defaultList);
            if (defaultList.length > 0 && currentBoard.id) {
                service.updateBoard(currentBoard.id, { model_escalation_priority: defaultList }).catch(() => {});
            }
        } else {
            setEscalationPriority([]);
        }

        setReviewerOrder(currentBoard?.reviewerOrder || []);
        setEscalationPrunedNote(null);
        setReviewerOrderPrunedNote(null);
        prunedForBoardRef.current = null;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentBoard?.id]);

    // Stale-entry cleanup: saved lists may reference models retired from the
    // curated registry or agents no longer enabled on the board. Runs once
    // per board load, after members/agents data has actually arrived —
    // waiting on that data avoids pruning against an empty registry before
    // it's fetched. The cleaned list is shown immediately; it is NOT written
    // back here — it persists on the next explicit save (drag, Reset,
    // Customize), same as any other edit to these lists. Also guards on a
    // non-empty registry: `members` for a newly-selected board can arrive
    // after `currentBoard` has already flipped, so an empty
    // `enabledAgentModelNames` almost always means "not loaded yet", not
    // "every model retired" — treating it as the latter would wipe a valid
    // list out from under the user during that window.
    useEffect(() => {
        if (!currentBoard?.id || !hasAgentsData || members.length === 0) return;
        if (enabledAgentModelNames.size === 0) return;
        if (prunedForBoardRef.current === currentBoard.id) return;
        prunedForBoardRef.current = currentBoard.id;

        const prune = (list: Array<{ agent_name: string; model_name: string }>) => {
            const kept = list.filter(e => enabledAgentModelNames.has(e.model_name));
            return { kept, removedCount: list.length - kept.length };
        };

        const escResult = prune(currentBoard.modelEscalationPriority || []);
        if (escResult.removedCount > 0) {
            setEscalationPriority(escResult.kept);
            setEscalationPrunedNote(`${escResult.removedCount} outdated entr${escResult.removedCount === 1 ? 'y' : 'ies'} removed`);
        }

        const revResult = prune(currentBoard.reviewerOrder || []);
        if (revResult.removedCount > 0) {
            setReviewerOrder(revResult.kept);
            setReviewerOrderPrunedNote(`${revResult.removedCount} outdated entr${revResult.removedCount === 1 ? 'y' : 'ies'} removed`);
        }
    }, [currentBoard, hasAgentsData, members, enabledAgentModelNames]);

    // Routing (task #328) — fetch the effective policy (defaults + board
    // overrides merged) whenever the selected board changes, and seed the
    // editable preference-order list from it.
    useEffect(() => {
        if (!currentBoard?.id) {
            setRoutingConfig(null);
            setPreferenceOrder([]);
            return;
        }
        let active = true;
        setRoutingConfigLoading(true);
        routingPolicyRef.current = (currentBoard.routingPolicy as Record<string, unknown>) || {};
        service.fetchRoutingConfig(currentBoard.id)
            .then(cfg => {
                if (!active) return;
                setRoutingConfig(cfg);
                setPreferenceOrder(cfg.preference_order || []);
            })
            .catch(() => {
                if (!active) return;
                setRoutingConfig(null);
                setPreferenceOrder([]);
            })
            .finally(() => {
                if (active) setRoutingConfigLoading(false);
            });
        return () => { active = false; };
    }, [currentBoard?.id, service]);


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

    useEffect(() => {
        let active = true;
        setLoadingIdeOptions(true);
        service.fetchIdeOptions()
            .then(options => {
                if (!active) return;
                setDetectedIdes(options.detected_ides || []);
                setPreferredIdeId(options.preferred_ide_id || null);
                // Keep localStorage in sync for EditorLink
                if (options.preferred_ide_id) {
                    localStorage.setItem('preferred-editor', options.preferred_ide_id);
                }
            })
            .catch(() => {
                if (!active) return;
                setDetectedIdes([]);
                setPreferredIdeId(null);
            })
            .finally(() => {
                if (!active) return;
                setLoadingIdeOptions(false);
            });
        return () => { active = false; };
    }, [service]);

    const preferredIde = detectedIdes.find(ide => ide.id === preferredIdeId) || null;

    const handleSaveIde = async (ideId: string) => {
        setSavingIde(true);
        try {
            const saved = await service.saveIdeSettings(ideId);
            setPreferredIdeId(saved.preferred_ide_id || null);
            // Sync to localStorage so EditorLink picks it up without an API call
            if (saved.preferred_ide_id) {
                localStorage.setItem('preferred-editor', saved.preferred_ide_id);
            } else {
                localStorage.removeItem('preferred-editor');
            }
            setShowIdeSetup(false);
            toast({ title: 'IDE saved', description: 'Open Project will use this IDE by default.' });
        } catch (e) {
            toast({
                title: 'Failed to save IDE',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        } finally {
            setSavingIde(false);
        }
    };

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

    const handleUpdateBoardReflection = async (boardId: string, updates: { skip_reflection?: boolean }) => {
        try {
            await service.updateBoard(boardId, updates);
            setTableBoards(prev => prev.map(b => b.id === boardId ? {
                ...b,
                ...(updates.skip_reflection !== undefined && { skipReflection: updates.skip_reflection }),
            } : b));
            toast({ title: 'Board updated' });
        } catch {
            toast({ title: 'Error', description: 'Failed to update board settings.', variant: 'destructive' });
        }
    };

    // W10.4: per-agent inline toggle so the operator doesn't have to
    // open the Manage modal to flip the switch — the same switch the
    // dispatch error message points at. Optimistic update + rollback
    // on failure: a network blip must NOT leave the UI showing a
    // phantom "enabled" state for an agent that's actually off.
    const handleToggleBoardAgent = async (
        boardId: string,
        agentName: string,
        nextEnabled: boolean,
    ) => {
        const previousBoards = tableBoards;
        setTableBoards(prev => prev.map(b => {
            if (b.id !== boardId || !b.agents) return b;
            return {
                ...b,
                agents: b.agents.map(a =>
                    a.name === agentName ? { ...a, enabled: nextEnabled } : a,
                ),
            };
        }));
        try {
            await service.toggleBoardAgent(boardId, agentName, nextEnabled);
        } catch (e) {
            setTableBoards(previousBoards);
            toast({
                title: 'Failed to update agent',
                description:
                    e instanceof Error
                        ? e.message
                        : `Could not toggle ${agentName} on /api/boards/${boardId}/agents/${agentName}/.`,
                variant: 'destructive',
            });
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

    const handleCurrentBoardReflection = async (updates: { skip_reflection?: boolean }) => {
        if (!currentBoard) return;
        if (updates.skip_reflection !== undefined) setCurrentSkip(updates.skip_reflection);
        try {
            await service.updateBoard(currentBoard.id, updates);
            toast({ title: 'Board updated' });
        } catch {
            setCurrentSkip(currentBoard.skipReflection ?? false);
            toast({ title: 'Error', description: 'Failed to update reflection settings.', variant: 'destructive' });
        }
    };

    const handleSaveClaudeToken = async () => {
        if (!currentBoard || !claudeToken.trim()) return;
        setClaudeTokenSaving(true);
        try {
            await service.setClaudeToken(currentBoard.id, claudeToken.trim());
            setClaudeTokenConfigured(true);
            setClaudeToken('');
            toast({ title: 'Claude Code token saved' });
        } catch {
            toast({ title: 'Error', description: 'Failed to save Claude Code token.', variant: 'destructive' });
        } finally {
            setClaudeTokenSaving(false);
        }
    };

    const handleCurrentBoardProof = async (value: boolean) => {
        if (!currentBoard) return;
        setCurrentSkipProof(value);
        try {
            await service.updateBoard(currentBoard.id, { skip_proof: value });
            toast({ title: 'Board updated' });
        } catch {
            setCurrentSkipProof(currentBoard.skipProof ?? false);
            toast({ title: 'Error', description: 'Failed to update proof settings.', variant: 'destructive' });
        }
    };

    const handleCurrentBoardAutoStart = async (value: boolean) => {
        if (!currentBoard) return;
        setCurrentAutoStart(value);
        try {
            await service.updateBoard(currentBoard.id, { auto_start_planned_tasks: value });
            toast({ title: 'Board updated' });
        } catch {
            setCurrentAutoStart(currentBoard.autoStartPlannedTasks ?? false);
            toast({ title: 'Error', description: 'Failed to update auto-start setting.', variant: 'destructive' });
        }
    };

    const saveEscalationPriority = async (newList: Array<{ agent_name: string; model_name: string }>) => {
        if (!currentBoard) return;
        const prev = escalationPriority;
        setEscalationPriority(newList);
        try {
            await service.updateBoard(currentBoard.id, { model_escalation_priority: newList });
            toast({ title: 'Escalation priority updated' });
        } catch {
            setEscalationPriority(prev);
            toast({ title: 'Error', description: 'Failed to update escalation priority.', variant: 'destructive' });
        }
    };

    const saveReviewerOrder = async (newList: Array<{ agent_name: string; model_name: string }>) => {
        if (!currentBoard) return;
        const prev = reviewerOrder;
        setReviewerOrder(newList);
        try {
            await service.updateBoard(currentBoard.id, { reviewer_order: newList });
            toast({ title: 'Reviewer order updated' });
        } catch {
            setReviewerOrder(prev);
            toast({ title: 'Error', description: 'Failed to update reviewer order.', variant: 'destructive' });
        }
    };

    const saveEscalationEnabled = async (value: boolean) => {
        if (!currentBoard) return;
        const prev = escalationEnabled;
        setEscalationEnabled(value);
        try {
            await service.updateBoard(currentBoard.id, { escalation_enabled: value });
            toast({ title: value ? 'Escalation enabled' : 'Escalation disabled' });
        } catch {
            setEscalationEnabled(prev);
            toast({ title: 'Error', description: 'Failed to update escalation setting.', variant: 'destructive' });
        }
    };

    const saveFailureMaxRetries = async (value: number) => {
        if (!currentBoard) return;
        const prev = failureMaxRetries;
        setFailureMaxRetries(value);
        try {
            await service.updateBoard(currentBoard.id, { failure_max_retries: value });
            toast({ title: 'Max retries updated' });
        } catch {
            setFailureMaxRetries(prev);
            toast({ title: 'Error', description: 'Failed to update max retries.', variant: 'destructive' });
        }
    };

    // Routing (task #328) — ONE write path for the whole policy. Every
    // control in the Routing section merges its patch onto the live
    // routing_policy blob and PATCHes the board. routing_policy is a single
    // JSON blob (empty `{}` means "use built-in defaults"), and the ref keeps
    // sequential edits from clobbering each other. Optimistically mutates the
    // displayed RoutingConfig, reverting both ref and view on failure.
    const persistRoutingPolicy = async (
        patch: Record<string, unknown>,
        optimistic?: (cfg: RoutingConfig) => RoutingConfig,
    ): Promise<boolean> => {
        if (!currentBoard) return false;
        const prevPolicy = routingPolicyRef.current;
        const prevCfg = routingConfig;
        const nextPolicy = { ...prevPolicy, ...patch };
        routingPolicyRef.current = nextPolicy;
        if (optimistic && routingConfig) setRoutingConfig(optimistic(routingConfig));
        try {
            await service.updateBoard(currentBoard.id, { routing_policy: nextPolicy });
            return true;
        } catch {
            routingPolicyRef.current = prevPolicy;
            if (optimistic) setRoutingConfig(prevCfg);
            toast({ title: 'Error', description: 'Failed to update routing policy.', variant: 'destructive' });
            return false;
        }
    };

    // Persist a reordered standing preference list.
    const savePreferenceOrder = async (newOrder: string[]) => {
        const prev = preferenceOrder;
        setPreferenceOrder(newOrder);
        const ok = await persistRoutingPolicy(
            { preference_order: newOrder },
            cfg => ({ ...cfg, preference_order: newOrder }),
        );
        if (ok) toast({ title: 'Routing preference order updated' });
        else setPreferenceOrder(prev);
    };

    // Toggle whether the deliberate one-tier capability escalation may fire.
    // Writes routing_policy.capability_escalation_enabled — the single
    // authority the backend policy engine consults (Default First → legacy
    // escalation_enabled). So this switch governs real dispatch, no restart.
    const saveCapabilityEnabled = async (value: boolean) => {
        const ok = await persistRoutingPolicy(
            { capability_escalation_enabled: value },
            cfg => ({ ...cfg, capability_escalation_enabled: value }),
        );
        if (ok) toast({ title: value ? 'Capability escalation enabled' : 'Capability escalation disabled' });
    };

    // Persist the number of review rejections before the one-tier jump fires.
    const saveCapabilityEscalateAfter = async (value: number) => {
        const n = Math.max(1, Math.round(value) || 1);
        const ok = await persistRoutingPolicy(
            { capability_escalate_after: n },
            cfg => ({ ...cfg, capability_escalate_after: n }),
        );
        if (ok) toast({ title: 'Escalation threshold updated' });
    };

    // Persist a per-failure-class policy edit (action and/or same-agent retry
    // cap). Merges the patch onto the existing failure_actions map so other
    // classes' overrides — and the same class's other fields — survive. This
    // is the single write path for the FULL per-class policy: the action
    // Select and the retries input both flow through it.
    const saveFailureAction = async (
        failureClass: string,
        patch: Record<string, unknown>,
        toastTitle: string,
    ) => {
        const existingActions = (routingPolicyRef.current.failure_actions as Record<string, Record<string, unknown>>) || {};
        const nextActions = {
            ...existingActions,
            [failureClass]: { ...(existingActions[failureClass] || {}), ...patch },
        };
        const ok = await persistRoutingPolicy(
            { failure_actions: nextActions },
            cfg => ({
                ...cfg,
                failure_actions: {
                    ...cfg.failure_actions,
                    [failureClass]: { ...cfg.failure_actions[failureClass], ...patch },
                },
            }),
        );
        if (ok) toast({ title: toastTitle });
    };

    const saveFailureActionRetries = (failureClass: string, value: number) =>
        saveFailureAction(failureClass, { max_retries: Math.max(0, Math.round(value) || 0) }, 'Retry limit updated');

    const saveFailureActionAction = (failureClass: string, action: string) =>
        saveFailureAction(failureClass, { action }, 'Failure action updated');

    const handleUpdateBoardProof = async (boardId: string, value: boolean) => {
        try {
            await service.updateBoard(boardId, { skip_proof: value });
            setTableBoards(prev => prev.map(b => b.id === boardId ? { ...b, skipProof: value } : b));
            toast({ title: 'Board updated' });
        } catch {
            toast({ title: 'Error', description: 'Failed to update board settings.', variant: 'destructive' });
        }
    };

    const isAgentUser = (member: Member) => member.email.endsWith('@odin.agent');

    return (
        <div className="space-y-6">
            <div>
                <h2 className="text-lg font-semibold tracking-tight">Settings</h2>
                <p className="text-sm text-muted-foreground mt-1">Manage your boards, appearance, and preferences.</p>
            </div>

            <Tabs value={activeTab} onValueChange={handleTabChange} orientation="vertical" className="min-h-[500px]">
                <TabsList variant="line" className="min-w-[180px] border-r border-border pr-4 shrink-0 self-start sticky top-6">
                    <TabsTrigger value="board" className="gap-2 justify-start px-3 py-2">
                        <Sparkles className="size-4" />
                        <span>Current Board</span>
                        {currentBoard && (
                            <span className="ml-auto size-1.5 rounded-full bg-primary" />
                        )}
                    </TabsTrigger>
                    <TabsTrigger value="boards" className="gap-2 justify-start px-3 py-2">
                        <Layout className="size-4" />
                        <span>All Boards</span>
                    </TabsTrigger>
                    <TabsTrigger value="ide" className="gap-2 justify-start px-3 py-2">
                        <SettingsIcon className="size-4" />
                        <span>IDE & Tools</span>
                    </TabsTrigger>
                    <TabsTrigger value="sandbox" className="gap-2 justify-start px-3 py-2">
                        <Cpu className="size-4" />
                        <span>Sandbox</span>
                    </TabsTrigger>
                    <TabsTrigger value="appearance" className="gap-2 justify-start px-3 py-2">
                        {dark ? <Moon className="size-4" /> : <Sun className="size-4" />}
                        <span>Appearance</span>
                    </TabsTrigger>
                    <TabsTrigger value="notifications" className="gap-2 justify-start px-3 py-2">
                        <Bell className="size-4" />
                        <span>Notifications</span>
                    </TabsTrigger>
                    {authEnabled && (
                        <TabsTrigger value="account" className="gap-2 justify-start px-3 py-2">
                            <UserCircle className="size-4" />
                            <span>Account</span>
                        </TabsTrigger>
                    )}
                    <TabsTrigger value="shortcuts" className="gap-2 justify-start px-3 py-2">
                        <Keyboard className="size-4" />
                        <span>Shortcuts</span>
                    </TabsTrigger>
                </TabsList>

                {/* ── Board Tab ── */}
                <TabsContent value="board" className="mt-0">
                    {currentBoard ? (
                        <div className="space-y-6">
                            <Card>
                                <CardHeader className="pb-3">
                                    <div className="flex items-center gap-3">
                                        <Sparkles className="size-4 text-muted-foreground" />
                                        <div>
                                            <CardTitle className="text-base">{currentBoard.name}</CardTitle>
                                            <p className="text-xs text-muted-foreground mt-0.5">Settings for the currently selected board</p>
                                        </div>
                                        {currentBoard.isTrial && (
                                            <Badge variant="outline" className="text-[10px] gap-1 px-1.5 py-0 text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600 ml-auto">
                                                <FlaskConical className="size-2.5" /> Trial
                                            </Badge>
                                        )}
                                    </div>
                                </CardHeader>
                                <CardContent className="space-y-6">
                                    {/* Reflection & Review sub-section */}
                                    <div>
                                        <div className="text-sm font-medium mb-1">Reflection &amp; Review</div>
                                        <div className="text-xs text-muted-foreground mb-1">Topmost available model with available quota is used.</div>
                                        <div className="text-xs text-muted-foreground mb-3">After a task's work is done, a reviewer model checks it before the task can be marked complete.</div>
                                        <div className="space-y-3">
                                            <div className="flex items-center justify-between">
                                                <div>
                                                    <div className="text-sm">Skip reflection</div>
                                                    <div className="text-xs text-muted-foreground mt-0.5">Turn off the review step for every task in this board — work is accepted without a reviewer.</div>
                                                </div>
                                                <Switch
                                                    checked={currentSkip}
                                                    onCheckedChange={v => handleCurrentBoardReflection({ skip_reflection: v })}
                                                />
                                            </div>
                                            {/* Reviewer order — deterministic quota-aware walk (board.reviewer_order) */}
                                            {(() => {
                                                const defaultOrder = computeDefaultOrder();
                                                const isCustomized = reviewerOrder.length > 0;
                                                const displayOrder = isCustomized ? reviewerOrder : defaultOrder;
                                                return (
                                                    <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2.5">
                                                        <div className="flex items-center justify-between mb-2">
                                                            <div className="flex items-center gap-2">
                                                                <div className="text-sm">Reviewer order</div>
                                                                {!isCustomized && (
                                                                    <Badge variant="outline" className="text-[10px] h-4 px-1.5 text-muted-foreground">
                                                                        default order
                                                                    </Badge>
                                                                )}
                                                            </div>
                                                            {!isCustomized && defaultOrder.length > 0 && (
                                                                <Button
                                                                    variant="outline"
                                                                    size="sm"
                                                                    className="h-6 text-[11px] gap-1 px-2"
                                                                    onClick={() => saveReviewerOrder(defaultOrder)}
                                                                >
                                                                    Customize
                                                                </Button>
                                                            )}
                                                        </div>
                                                        <div className="text-xs text-muted-foreground mb-2">
                                                            Deterministic, quota-aware walk. Index 0 is tried first{isCustomized ? ' — drag to reorder.' : '.'}
                                                        </div>
                                                        {reviewerOrderPrunedNote && (
                                                            <div className="text-[11px] text-amber-500/80 mb-2">{reviewerOrderPrunedNote}</div>
                                                        )}
                                                        {displayOrder.length > 0 ? (
                                                            <DndContext
                                                                sensors={reviewerOrderSensors}
                                                                collisionDetection={closestCenter}
                                                                onDragEnd={(event: DragEndEvent) => {
                                                                    if (!isCustomized) return;
                                                                    const { active, over } = event;
                                                                    if (over && active.id !== over.id) {
                                                                        const ids = displayOrder.map(e => `${e.agent_name}:${e.model_name}`);
                                                                        const oldIndex = ids.indexOf(String(active.id));
                                                                        const newIndex = ids.indexOf(String(over.id));
                                                                        saveReviewerOrder(arrayMove(displayOrder, oldIndex, newIndex));
                                                                    }
                                                                }}
                                                            >
                                                                <SortableContext
                                                                    items={displayOrder.map(e => `${e.agent_name}:${e.model_name}`)}
                                                                    strategy={verticalListSortingStrategy}
                                                                >
                                                                    <div className={`space-y-1 max-h-[280px] overflow-y-auto ${!isCustomized ? 'opacity-50 pointer-events-none' : ''}`}>
                                                                        {displayOrder.map((entry, idx) => (
                                                                            <SortableEscalationRow
                                                                                key={`${entry.agent_name}-${entry.model_name}`}
                                                                                id={`${entry.agent_name}:${entry.model_name}`}
                                                                                entry={entry}
                                                                                index={idx}
                                                                                highlight={false}
                                                                            />
                                                                        ))}
                                                                    </div>
                                                                </SortableContext>
                                                            </DndContext>
                                                        ) : (
                                                            <div className="text-xs text-muted-foreground/60">No enabled agent models available.</div>
                                                        )}
                                                    </div>
                                                );
                                            })()}
                                        </div>
                                    </div>

                                    <div className="h-px bg-border" />

                                    {/* Proof of Work sub-section */}
                                    <div>
                                        <div className="text-sm font-medium mb-3">Proof of Work</div>
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <div className="text-sm">Skip proof</div>
                                                <div className="text-xs text-muted-foreground mt-0.5">Don't require agents to attach evidence (logs, output, screenshots) before a task can complete.</div>
                                            </div>
                                            <Switch
                                                checked={currentSkipProof}
                                                onCheckedChange={v => handleCurrentBoardProof(v)}
                                            />
                                        </div>
                                    </div>

                                    <div className="h-px bg-border" />

                                    {/* Planning sub-section */}
                                    <div>
                                        <div className="text-sm font-medium mb-3">Planning &amp; Task Defaults</div>
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <div className="text-sm">Auto-start planned tasks</div>
                                                <div className="text-xs text-muted-foreground mt-0.5">Tasks created by spec planning move straight to In Progress instead of waiting in To Do.</div>
                                            </div>
                                            <Switch
                                                checked={currentAutoStart}
                                                onCheckedChange={v => handleCurrentBoardAutoStart(v)}
                                            />
                                        </div>
                                        <div className="mt-3 text-xs text-muted-foreground rounded-md border border-dashed border-border/60 px-3 py-2">
                                            Task presets (reusable task templates) are chosen in the <span className="font-medium text-foreground">New → Task</span> dialog; they are defined server-side, not edited here.
                                        </div>
                                        <div className="mt-2 text-xs text-muted-foreground rounded-md border border-dashed border-border/60 px-3 py-2">
                                            Execution concurrency (how many tasks run at once) is set by the server environment (<span className="font-mono">DAG_EXECUTOR_MAX_CONCURRENCY</span>), not per board.
                                        </div>
                                        <SandboxMemoryInfo />
                                    </div>

                                    <div className="h-px bg-border" />

                                    {/* Agent credentials sub-section — sensitive */}
                                    <div className="rounded-md border border-amber-400/40 bg-amber-500/5 px-3 py-3">
                                        <div className="flex items-center gap-2 mb-3">
                                            <ShieldAlert className="size-4 text-amber-600 dark:text-amber-400" />
                                            <div className="text-sm font-medium">Agent credentials</div>
                                            <Badge variant="outline" className="text-[10px] h-4 px-1.5 text-amber-600 border-amber-400/50 dark:text-amber-400">Sensitive</Badge>
                                        </div>
                                        <div className="space-y-2">
                                            <div className="text-sm">Claude Code OAuth token</div>
                                            <div className="text-xs text-muted-foreground">
                                                Run <span className="font-mono">claude setup-token</span> on the host, then paste the token here. Stored write-only as <span className="font-mono">.claude-token</span> in the board's working directory for the sandboxed Claude agent — never shown again and never committed.
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <Input
                                                    type="password"
                                                    placeholder={claudeTokenConfigured ? '•••••••• (configured — paste to replace)' : 'sk-ant-oat-...'}
                                                    value={claudeToken}
                                                    onChange={e => setClaudeToken(e.target.value)}
                                                    className="flex-1 h-8 text-xs font-mono"
                                                />
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    disabled={claudeTokenSaving || !claudeToken.trim()}
                                                    onClick={handleSaveClaudeToken}
                                                >
                                                    {claudeTokenSaving ? 'Saving…' : 'Save'}
                                                </Button>
                                            </div>
                                            {claudeTokenConfigured && (
                                                <div className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400">
                                                    <CheckCircle2 className="size-3.5" /> Token configured
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    {/* Forced provider sub-section */}
                                    {forcedProvider?.provider && (
                                        <>
                                            <div className="h-px bg-border" />
                                            <div>
                                                <div className="text-sm font-medium mb-3">AI Provider</div>
                                                <div className="rounded-md border border-border bg-muted/30 px-4 py-3">
                                                    <div className="text-xs font-medium text-muted-foreground">Forced Provider</div>
                                                    <div className="mt-1 text-sm">
                                                        <span className="font-mono">{forcedProvider.provider}</span>
                                                        {forcedProvider.model && <span className="text-muted-foreground"> / <span className="font-mono">{forcedProvider.model}</span></span>}
                                                    </div>
                                                </div>
                                            </div>
                                        </>
                                    )}

                                    {/* Model Escalation Priority */}
                                    <div className="h-px bg-border" />
                                    <div>
                                        <div className="flex items-center justify-between mb-1">
                                            <div className="flex items-center gap-2">
                                                <div className="text-sm font-medium">Model Escalation</div>
                                                {escalationEnabled && escalationPriority.length > 0 && (
                                                    <Badge variant="outline" className="text-[10px] h-4 px-1.5 font-mono">
                                                        {escalationPriority.length} model{escalationPriority.length !== 1 ? 's' : ''}
                                                    </Badge>
                                                )}
                                            </div>
                                            <Switch
                                                checked={escalationEnabled}
                                                onCheckedChange={v => saveEscalationEnabled(v)}
                                            />
                                        </div>
                                        <div className="text-xs text-muted-foreground mb-3">
                                            When a task fails, it automatically retries with the next model, escalating upward from rank {escalationPriority.length || 'N'} toward rank 1.
                                        </div>

                                        {escalationEnabled && (
                                            <>
                                                <div className="flex items-center justify-between mb-3">
                                                    <div>
                                                        <div className="text-sm">Max retries</div>
                                                    </div>
                                                    <Select value={String(failureMaxRetries)} onValueChange={v => saveFailureMaxRetries(Number(v))}>
                                                        <SelectTrigger className="w-16 h-7 text-xs">
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {[1, 2, 3, 4, 5, 10].map(n => (
                                                                <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                </div>

                                                {escalationPrunedNote && (
                                                    <div className="text-[11px] text-amber-500/80 mb-2">{escalationPrunedNote}</div>
                                                )}

                                                {escalationPriority.length > 0 ? (
                                                    <>
                                                        <div className="flex items-center justify-between mb-2">
                                                            <div className="text-xs text-muted-foreground">Priority order <span className="text-muted-foreground/50">(drag to reorder)</span></div>
                                                            <Button
                                                                variant="ghost"
                                                                size="sm"
                                                                className="h-6 text-[11px] gap-1 px-2 text-muted-foreground hover:text-foreground"
                                                                onClick={() => saveEscalationPriority(computeDefaultOrder())}
                                                            >
                                                                <Sparkles className="size-2.5" /> Reset
                                                            </Button>
                                                        </div>

                                                        {escalationPriority.length > 5 && (
                                                            <div className="relative mb-2">
                                                                <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground/50" />
                                                                <Input
                                                                    placeholder="Filter models..."
                                                                    value={escalationSearch}
                                                                    onChange={e => setEscalationSearch(e.target.value)}
                                                                    className="h-7 text-xs pl-7 bg-muted/20 border-border/50"
                                                                />
                                                                {escalationSearch && (
                                                                    <button
                                                                        onClick={() => setEscalationSearch('')}
                                                                        className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/50 hover:text-foreground"
                                                                    >
                                                                        <X className="size-3" />
                                                                    </button>
                                                                )}
                                                            </div>
                                                        )}

                                                        <DndContext
                                                            sensors={escalationSensors}
                                                            collisionDetection={closestCenter}
                                                            onDragEnd={(event: DragEndEvent) => {
                                                                const { active, over } = event;
                                                                if (over && active.id !== over.id) {
                                                                    const ids = escalationPriority.map(e => `${e.agent_name}:${e.model_name}`);
                                                                    const oldIndex = ids.indexOf(String(active.id));
                                                                    const newIndex = ids.indexOf(String(over.id));
                                                                    saveEscalationPriority(arrayMove(escalationPriority, oldIndex, newIndex));
                                                                }
                                                            }}
                                                        >
                                                            <SortableContext
                                                                items={escalationPriority.map(e => `${e.agent_name}:${e.model_name}`)}
                                                                strategy={verticalListSortingStrategy}
                                                            >
                                                                <div className="space-y-1 max-h-[320px] overflow-y-auto">
                                                                    {escalationPriority.map((entry, idx) => {
                                                                        const matchesSearch = escalationSearch !== '' && (
                                                                            entry.model_name.toLowerCase().includes(escalationSearch.toLowerCase()) ||
                                                                            entry.agent_name.toLowerCase().includes(escalationSearch.toLowerCase())
                                                                        );
                                                                        return (
                                                                            <SortableEscalationRow
                                                                                key={`${entry.agent_name}-${entry.model_name}`}
                                                                                id={`${entry.agent_name}:${entry.model_name}`}
                                                                                entry={entry}
                                                                                index={idx}
                                                                                highlight={matchesSearch}
                                                                            />
                                                                        );
                                                                    })}
                                                                </div>
                                                            </SortableContext>
                                                        </DndContext>

                                                    </>
                                                ) : (
                                                    <div className="rounded-md border border-dashed border-border/50 px-4 py-6 text-center">
                                                        <div className="text-xs text-muted-foreground/60 mb-2">No escalation priority configured.</div>
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            className="h-7 text-xs gap-1"
                                                            onClick={() => saveEscalationPriority(computeDefaultOrder())}
                                                        >
                                                            <Sparkles className="size-3" /> Generate from board agents
                                                        </Button>
                                                    </div>
                                                )}
                                            </>
                                        )}
                                    </div>

                                    {/* Routing sub-section (task #328) */}
                                    <div className="h-px bg-border" />
                                    <div>
                                        <div className="text-sm font-medium mb-1">Routing</div>
                                        <div className="text-xs text-muted-foreground mb-3">
                                            How work is routed to a peer when a task needs a same-tier reassignment, and what happens automatically when a task fails.
                                        </div>

                                        {/* Standing preference order — editable */}
                                        <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2.5 mb-3">
                                            <div className="flex items-center justify-between mb-2">
                                                <div className="text-sm">Standing preference order</div>
                                                {routingConfig && preferenceOrder.join(',') === (routingConfig.default_preference_order || []).join(',') && (
                                                    <Badge variant="outline" className="text-[10px] h-4 px-1.5 text-muted-foreground">
                                                        default order
                                                    </Badge>
                                                )}
                                            </div>
                                            <div className="text-xs text-muted-foreground mb-2">
                                                Order agents are tried for a routing peer. Index 0 is tried first — use the arrows to reorder.
                                            </div>
                                            {routingConfigLoading ? (
                                                <div className="text-xs text-muted-foreground/60">Loading…</div>
                                            ) : preferenceOrder.length > 0 ? (
                                                <div className="space-y-1" aria-label="Routing preference order">
                                                    {preferenceOrder.map((agentName, idx) => (
                                                        <div
                                                            key={agentName}
                                                            className="flex items-center gap-2 rounded-md border border-border bg-muted/20 px-2.5 py-2 text-sm"
                                                        >
                                                            <span className="text-[10px] w-5 text-right font-mono shrink-0 text-muted-foreground/50">
                                                                {idx + 1}
                                                            </span>
                                                            <span className="font-mono text-xs flex-1 truncate">{agentName}</span>
                                                            <div className="flex items-center gap-0.5 shrink-0">
                                                                <Button
                                                                    variant="ghost"
                                                                    size="icon-xs"
                                                                    disabled={idx === 0}
                                                                    aria-label={`Move ${agentName} up`}
                                                                    onClick={() => {
                                                                        const next = [...preferenceOrder];
                                                                        [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
                                                                        savePreferenceOrder(next);
                                                                    }}
                                                                >
                                                                    <ChevronUp className="size-3.5" />
                                                                </Button>
                                                                <Button
                                                                    variant="ghost"
                                                                    size="icon-xs"
                                                                    disabled={idx === preferenceOrder.length - 1}
                                                                    aria-label={`Move ${agentName} down`}
                                                                    onClick={() => {
                                                                        const next = [...preferenceOrder];
                                                                        [next[idx + 1], next[idx]] = [next[idx], next[idx + 1]];
                                                                        savePreferenceOrder(next);
                                                                    }}
                                                                >
                                                                    <ChevronDown className="size-3.5" />
                                                                </Button>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div className="text-xs text-muted-foreground/60">No preference order configured.</div>
                                            )}
                                        </div>

                                        {/* Per-failure-class actions — the FULL per-class policy is
                                            editable: the action itself (via Select) and, for the
                                            auto_requeue action, the same-agent retry cap. Every edit
                                            writes routing_policy.failure_actions and alters real dispatch. */}
                                        <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2.5 mb-3">
                                            <div className="text-sm mb-2">Failure actions</div>
                                            <div className="text-xs text-muted-foreground mb-2">
                                                What happens automatically when a task fails, by failure class. Change the action per class; the auto_requeue action also lets you set the same-agent retry cap before a routing peer takes over.
                                            </div>
                                            {routingConfigLoading ? (
                                                <div className="text-xs text-muted-foreground/60">Loading…</div>
                                            ) : routingConfig && Object.keys(routingConfig.failure_actions || {}).length > 0 ? (
                                                <div className="space-y-1 max-h-[280px] overflow-y-auto">
                                                    {Object.entries(routingConfig.failure_actions).map(([failureClass, cfg]) => (
                                                        <div
                                                            key={failureClass}
                                                            className="flex items-center justify-between gap-2 rounded-md border border-border/50 px-2.5 py-1.5 text-xs"
                                                        >
                                                            <span className="font-mono">{failureClass}</span>
                                                            <div className="flex items-center gap-2 shrink-0">
                                                                {cfg.action === 'auto_requeue' && (
                                                                    <label className="flex items-center gap-1 text-[10px] text-muted-foreground">
                                                                        retries
                                                                        <Input
                                                                            type="number"
                                                                            min={0}
                                                                            max={10}
                                                                            aria-label={`${failureClass} max retries`}
                                                                            value={cfg.max_retries}
                                                                            onChange={(e) => saveFailureActionRetries(failureClass, Number(e.target.value))}
                                                                            className="h-6 w-14 px-1.5 text-xs"
                                                                        />
                                                                    </label>
                                                                )}
                                                                <Select value={cfg.action} onValueChange={(v) => saveFailureActionAction(failureClass, v)}>
                                                                    <SelectTrigger
                                                                        aria-label={`${failureClass} action`}
                                                                        className={`h-6 w-[240px] text-[10px] px-2 ${ROUTING_ACTION_META[cfg.action]?.className ?? ''}`}
                                                                    >
                                                                        <SelectValue />
                                                                    </SelectTrigger>
                                                                    <SelectContent>
                                                                        {Object.entries(ROUTING_ACTION_META).map(([action, m]) => (
                                                                            <SelectItem key={action} value={action} className="text-xs">
                                                                                {m.label}
                                                                            </SelectItem>
                                                                        ))}
                                                                    </SelectContent>
                                                                </Select>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div className="text-xs text-muted-foreground/60">No failure actions available.</div>
                                            )}
                                        </div>

                                        {/* Capability escalation — editable enabled + threshold. This is
                                            the ONE sanctioned tier jump; the controls write routing_policy,
                                            the single authority the backend policy engine consults. */}
                                        <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2.5 mb-3">
                                            <div className="flex items-center justify-between mb-1">
                                                <div className="text-sm">Capability escalation</div>
                                                <Switch
                                                    aria-label="Capability escalation enabled"
                                                    checked={routingConfig?.capability_escalation_enabled ?? true}
                                                    disabled={routingConfigLoading || !routingConfig}
                                                    onCheckedChange={saveCapabilityEnabled}
                                                />
                                            </div>
                                            <div className="text-xs text-muted-foreground mb-2">
                                                On repeated review rejection, escalate exactly one deliberate tier. This is the only sanctioned tier jump.
                                            </div>
                                            {routingConfig && (
                                                <label className="flex items-center justify-between gap-2 text-xs">
                                                    <span className="text-muted-foreground">Rejections before escalating</span>
                                                    <Input
                                                        type="number"
                                                        min={1}
                                                        max={10}
                                                        aria-label="Rejections before escalating"
                                                        value={routingConfig.capability_escalate_after}
                                                        disabled={!routingConfig.capability_escalation_enabled}
                                                        onChange={(e) => saveCapabilityEscalateAfter(Number(e.target.value))}
                                                        className="h-6 w-16 px-1.5 text-xs"
                                                    />
                                                </label>
                                            )}
                                        </div>

                                        {/* Escalation tiers — read-only */}
                                        <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2.5">
                                            <div className="text-sm mb-2">Capability escalation ladder</div>
                                            <div className="text-xs text-muted-foreground mb-2">
                                                The deliberate one-tier capability-escalation ladder — same as Model Escalation above.
                                            </div>
                                            {routingConfigLoading ? (
                                                <div className="text-xs text-muted-foreground/60">Loading…</div>
                                            ) : routingConfig && routingConfig.escalation_tiers.length > 0 ? (
                                                <div className="space-y-1">
                                                    {routingConfig.escalation_tiers.map((tier, idx) => (
                                                        <div
                                                            key={`${tier.agent_name}-${tier.model_name}`}
                                                            className="flex items-center gap-2 rounded-md border border-border/50 bg-muted/10 px-2.5 py-1.5 text-xs"
                                                        >
                                                            <span className="text-muted-foreground/50 font-mono w-5 text-right shrink-0">{idx + 1}</span>
                                                            <span className="font-mono flex-1 truncate">{tier.model_name}</span>
                                                            <Badge variant="outline" className="text-[10px] h-4 px-1.5 shrink-0">{tier.agent_name}</Badge>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div className="text-xs text-muted-foreground/60">No escalation tiers configured.</div>
                                            )}
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        </div>
                    ) : (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <Sparkles className="size-8 text-muted-foreground mx-auto mb-3 opacity-50" />
                                <p className="text-sm text-muted-foreground">Select a board to configure its settings.</p>
                            </CardContent>
                        </Card>
                    )}
                </TabsContent>

                {/* ── Boards Tab ── */}
                <TabsContent value="boards" className="mt-0">
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <h3 className="text-base font-medium">Board Management</h3>
                                <p className="text-xs text-muted-foreground mt-0.5">Create, configure, and manage your boards.</p>
                            </div>
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
                                                            <td className="px-3 py-2 font-medium">
                                                                <button
                                                                    type="button"
                                                                    onClick={() => navigate(`/board?board=${board.id}`)}
                                                                    className="text-left hover:text-primary hover:underline transition-colors"
                                                                    title="Open kanban for this board"
                                                                >
                                                                    {board.name}
                                                                </button>
                                                            </td>
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
                                                                                    <div className="flex flex-col gap-1">
                                                                                        {bAgents.map(agent => (
                                                                                            <div key={agent.name} className={`flex items-center gap-2 px-2 py-1 rounded-md border ${agent.enabled ? 'bg-blue-500/10 border-blue-300/30' : 'bg-background border-border'}`}>
                                                                                                <Bot className={`size-3 ${agent.enabled ? 'text-blue-500' : 'text-muted-foreground/50'}`} />
                                                                                                <span className={`text-xs font-medium min-w-0 flex-1 ${agent.enabled ? '' : 'text-muted-foreground line-through'}`}>{agent.name}</span>
                                                                                                <Switch
                                                                                                    checked={agent.enabled}
                                                                                                    onCheckedChange={v => handleToggleBoardAgent(board.id, agent.name, v)}
                                                                                                    aria-label={`${agent.enabled ? 'Disable' : 'Enable'} ${agent.name} on this board`}
                                                                                                />
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
                                                                                </div>
                                                                            </div>
                                                                        </div>
                                                                        <div className="flex gap-2">
                                                                            <FileText className="size-4 text-muted-foreground shrink-0 mt-0.5" />
                                                                            <div className="flex-1">
                                                                                <div className="font-medium mb-2">Proof Settings</div>
                                                                                <div className="flex items-center justify-between">
                                                                                    <span className="text-xs text-muted-foreground">Skip proof for all tasks in this board</span>
                                                                                    <Switch
                                                                                        checked={!!(board as Board & { skipProof?: boolean }).skipProof}
                                                                                        onCheckedChange={v => handleUpdateBoardProof(board.id, v)}
                                                                                    />
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
                </TabsContent>

                {/* ── IDE & Tools Tab ── */}
                <TabsContent value="ide" className="mt-0">
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <SettingsIcon className="size-4 text-muted-foreground" />
                                    <div>
                                        <CardTitle className="text-base">IDE Configuration</CardTitle>
                                        <p className="text-xs text-muted-foreground mt-0.5">
                                            Choose the IDE used by Open Project for Odin-initialized board roots.
                                        </p>
                                    </div>
                                </div>
                                <Button variant="outline" size="sm" onClick={() => setShowIdeSetup(true)}>
                                    {preferredIde ? 'Change IDE' : 'Configure IDE'}
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent>
                            {loadingIdeOptions ? (
                                <div className="text-xs text-muted-foreground">Detecting supported IDEs...</div>
                            ) : detectedIdes.length === 0 ? (
                                <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                                    No supported IDEs detected. Install or expose one of these CLIs on this machine: Cursor, VS Code, Zed.
                                </div>
                            ) : preferredIde ? (
                                <div className="flex items-center gap-3">
                                    <IdeBadge ide={preferredIde} />
                                    <span className="text-xs text-muted-foreground">Configured for Open Project</span>
                                </div>
                            ) : (
                                <div className="flex flex-wrap items-center gap-2">
                                    {detectedIdes.map(ide => <IdeBadge key={ide.id} ide={ide} muted />)}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── Sandbox Tab ── */}
                <TabsContent value="sandbox" className="mt-0">
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center gap-3">
                                <Cpu className="size-4 text-muted-foreground" />
                                <div>
                                    <CardTitle className="text-base">Sandbox Capacity</CardTitle>
                                    <p className="text-xs text-muted-foreground mt-0.5">
                                        Tune how many sandbox sessions the DAG executor can run at once.
                                    </p>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <ExecutorCapacitySettings service={service} />
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── Appearance Tab ── */}
                <TabsContent value="appearance" className="mt-0">
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base">Theme</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <div className="size-10 rounded-md bg-muted flex items-center justify-center">
                                        {dark ? <Sun className="size-5" /> : <Moon className="size-5" />}
                                    </div>
                                    <div>
                                        <div className="font-medium">Theme</div>
                                        <div className="text-sm text-muted-foreground">
                                            Toggle between light and dark mode
                                        </div>
                                    </div>
                                </div>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="gap-2"
                                    onClick={onToggleDark}
                                >
                                    {dark ? (
                                        <>
                                            <Sun className="size-4" />
                                            <span>Light Mode</span>
                                        </>
                                    ) : (
                                        <>
                                            <Moon className="size-4" />
                                            <span>Dark Mode</span>
                                        </>
                                    )}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── Notifications Tab ── */}
                <TabsContent value="notifications" className="mt-0">
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center gap-2">
                                <Bell className="size-4 text-muted-foreground" />
                                <CardTitle className="text-base">Notification Preferences</CardTitle>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <NotificationSettings />
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── Account Tab ── */}
                {authEnabled && (
                    <TabsContent value="account" className="mt-0">
                        <Card>
                            <CardHeader className="pb-3">
                                <div className="flex items-center gap-2">
                                    <UserCircle className="size-4 text-muted-foreground" />
                                    <CardTitle className="text-base">Account</CardTitle>
                                </div>
                            </CardHeader>
                            <CardContent className="space-y-6">
                                <div>
                                    <div className="text-sm font-medium mb-1">Signed in as</div>
                                    <div className="text-sm text-muted-foreground">{authUser?.displayName || authUser?.email || '—'}</div>
                                    {authUser?.displayName && authUser?.email && (
                                        <div className="text-xs text-muted-foreground mt-0.5">{authUser.email}</div>
                                    )}
                                </div>

                                <div className="h-px bg-border" />

                                <div>
                                    <div className="text-sm font-medium mb-3">Change password</div>
                                    <div className="space-y-2 max-w-sm">
                                        <Input type="password" placeholder="Current password" value={pwOld} onChange={e => setPwOld(e.target.value)} className="h-8 text-sm" aria-label="Current password" />
                                        <Input type="password" placeholder="New password (min 8 chars)" value={pwNew} onChange={e => setPwNew(e.target.value)} className="h-8 text-sm" aria-label="New password" />
                                        <Input type="password" placeholder="Confirm new password" value={pwConfirm} onChange={e => setPwConfirm(e.target.value)} className="h-8 text-sm" aria-label="Confirm new password" />
                                        <Button size="sm" disabled={pwSaving || !pwOld || !pwNew} onClick={handleChangePassword}>
                                            {pwSaving ? 'Saving…' : 'Update password'}
                                        </Button>
                                    </div>
                                </div>

                                <div className="h-px bg-border" />

                                <div className="flex items-center justify-between">
                                    <div>
                                        <div className="text-sm">Sign out</div>
                                        <div className="text-xs text-muted-foreground mt-0.5">End your session on this device.</div>
                                    </div>
                                    <Button variant="outline" size="sm" className="gap-1.5" onClick={logout}>
                                        <LogOut className="size-3.5" /> Log out
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    </TabsContent>
                )}

                {/* ── Shortcuts Tab ── */}
                <TabsContent value="shortcuts" className="mt-0">
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center gap-2">
                                <Keyboard className="size-4 text-muted-foreground" />
                                <CardTitle className="text-base">Available Shortcuts</CardTitle>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <KeyboardShortcutsContent />
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>

            {/* ── Dialogs (rendered outside tabs) ── */}

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
                    }}
                />
            )}

            <IdeSetupModal
                open={showIdeSetup}
                onOpenChange={setShowIdeSetup}
                detectedIdes={detectedIdes}
                initialIdeId={preferredIdeId}
                saving={savingIde}
                onSave={handleSaveIde}
            />
        </div>
    );
}

function SandboxMemoryInfo() {
    const [cfg, setCfg] = useState<{ agents_vm_mem_mib: Record<string, number>; memory_budget_mib: number | null; source: string } | null>(null);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        fetch(`${import.meta.env.VITE_HARNESS_TIME_API_URL || 'http://localhost:9100'}/api/system/sandbox-config/`)
            .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
            .then(setCfg)
            .catch(() => setFailed(true));
    }, []);
    return (
        <div className="mt-2 text-xs text-muted-foreground rounded-md border border-dashed border-border/60 px-3 py-2">
            <div className="font-medium text-foreground mb-1">Sandbox memory (per agent VM)</div>
            {failed && <span>—</span>}
            {!failed && !cfg && <span>Loading…</span>}
            {cfg && (
                <>
                    <div className="flex flex-wrap gap-x-4 gap-y-0.5 font-mono">
                        {Object.entries(cfg.agents_vm_mem_mib).map(([a, m]) => (
                            <span key={a}>{a}: {m} MiB</span>
                        ))}
                        {Object.keys(cfg.agents_vm_mem_mib).length === 0 && <span>—</span>}
                    </div>
                    <div className="mt-1">
                        Total budget: <span className="font-mono">{cfg.memory_budget_mib ?? '—'}{cfg.memory_budget_mib ? ' MiB' : ''}</span> · edit in <span className="font-mono">.odin/config.yaml</span>; opencode agents (glm, minimax) need 4096 (bun OOMs below ~4G), others run at 2048.
                    </div>
                </>
            )}
        </div>
    );
}
