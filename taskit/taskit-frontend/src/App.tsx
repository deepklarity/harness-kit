import { useState, useEffect, useCallback, useMemo } from 'react';
import { Routes, Route, useNavigate, useLocation, useParams, Navigate, useSearchParams } from 'react-router-dom';
import type { Board, Label, Member, ReflectionReport, Spec, Task, ViewMode } from './types';
import { useService } from './contexts/ServiceContext';
import { useAuth } from './contexts/AuthContext';
import { useToast } from '@/hooks/use-toast';
import { LoginPage } from './components/LoginPage';
import { ChangePasswordPage } from './components/ChangePasswordPage';
import { AppHeader, ALL_BOARDS_ID, VIEW_ROUTES } from './components/AppHeader';
import { TaskDetailModal } from './components/TaskDetailModal';
import { CreateBoardModal } from './components/CreateBoardModal';
import { CreateTaskModal } from './components/CreateTaskModal';
import { CreateSpecModal } from './components/CreateSpecModal';
import { KPICards } from './components/KPICards';
import { DashboardCharts } from './components/DashboardCharts';
import { SettingsView } from './components/SettingsView';
import { SpecDetailView } from './components/SpecDetailView';
import { SpecDebugView } from './components/SpecDebugView';
import { ReflectionListView } from './components/ReflectionListView';
import { ReflectionDetailView } from './components/ReflectionDetailView';
import { ProcessMonitorModal } from './components/ProcessMonitorModal';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Button } from '@/components/ui/button';
import { markCommentsSeen } from './utils/unseenComments';
import { getLatestExecutionTransitionTimestamp, markExecutionTransitionSeen } from './utils/unseenStatusTransitions';
import { EditUserModal } from './components/EditUserModal';
import { BoardPage, SchedulingPage, SpecsPage } from './components/pages';
import { NotificationsPage } from './components/NotificationsPage';
import { ProvidersPage } from './components/pages/ProvidersPage';
import { CommandPalette } from './components/CommandPalette';
import { AnalyticsPage } from './components/analytics';
import { useGlobalShortcuts } from '@/hooks/useGlobalShortcuts';
import { useTaskCardAutoRefresh } from '@/hooks/useTaskCardAutoRefresh';

function pathToViewMode(pathname: string): ViewMode {
    const match = VIEW_ROUTES.find(r => r.path === pathname);
    if (match) return match.id;
    if (pathname.startsWith('/specs/')) return 'specs';
    if (pathname.startsWith('/scheduling')) return 'scheduling';
    if (pathname.includes('/debug')) return 'specs';
    if (pathname.startsWith('/reflections')) return 'reflections';
    if (pathname === '/settings') return 'settings';
    if (pathname === '/notifications') return 'notifications';
    if (pathname === '/providers') return 'providers';
    if (pathname === '/analytics') return 'analytics';
    return 'board';
}

function App() {
    const navigate = useNavigate();
    const location = useLocation();
    const [searchParams, setSearchParams] = useSearchParams();
    const viewMode = pathToViewMode(location.pathname);
    const { user: authUser, loading: authLoading, authEnabled, getIdToken } = useAuth();
    const service = useService();
    const { toast } = useToast();
    const parseApiDetail = (err: unknown): string | null => {
        if (!err || typeof err !== 'object') return null;
        const maybeErr = err as { status?: number; body?: unknown };
        if (!maybeErr.body || typeof maybeErr.body !== 'object') return null;
        const body = maybeErr.body as { detail?: unknown; code?: unknown };
        if (body.code === 'task_executing_locked') {
            return 'Task is executing. Stop it from Process before changing status, assignee, or model.';
        }
        if (typeof body.detail === 'string' && body.detail.trim()) return body.detail;
        return maybeErr.status === 409 ? 'This task cannot be changed right now.' : null;
    };

    const [dark, setDark] = useState(() => {
        const saved = localStorage.getItem('taskit-theme');
        if (saved) return saved === 'dark';
        return window.matchMedia('(prefers-color-scheme: dark)').matches;
    });

    useEffect(() => {
        document.documentElement.classList.toggle('dark', dark);
        localStorage.setItem('taskit-theme', dark ? 'dark' : 'light');
    }, [dark]);

    useEffect(() => {
        const instance = import.meta.env.VITE_INSTANCE;
        if (instance) {
            document.documentElement.dataset.instance = instance;
            document.title = `[${instance.toUpperCase()}] ${document.title.replace(/^\[[^\]]+\]\s*/, '')}`;
            const link = document.querySelector("link[rel~='icon']") as HTMLLinkElement
                || Object.assign(document.createElement('link'), { rel: 'icon' });
            link.href = `data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔧</text></svg>`;
            document.head.appendChild(link);
        }
    }, []);

    useEffect(() => {
        service.setTokenProvider(getIdToken);
    }, [getIdToken, service]);

    const selectedBoard = searchParams.get('board') || ALL_BOARDS_ID;
    const boardFilter = selectedBoard === ALL_BOARDS_ID ? undefined : selectedBoard;

    const [boards, setBoards] = useState<Board[]>([]);
    const [members, setMembers] = useState<Member[]>([]);
    const [specs, setSpecs] = useState<Spec[]>([]);
    const [labels, setLabels] = useState<Label[]>([]);
    const [loadingShell, setLoadingShell] = useState(true);
    const [shellError, setShellError] = useState<string | null>(null);

    const [refreshKey, setRefreshKey] = useState(0);
    const [filteredMemberId] = useState<string | null>(null);
    const [selectedTask, setSelectedTask] = useState<Task | null>(null);
    const [taskDetailLoading, setTaskDetailLoading] = useState(false);
    const [showCreateBoard, setShowCreateBoard] = useState(false);
    const [createModal, setCreateModal] = useState<'task' | 'spec' | null>(() => {
        const p = new URLSearchParams(window.location.search).get('create');
        return p === 'task' || p === 'spec' ? p : null;
    });
    const showCreateTask = createModal === 'task';
    const showCreateSpec = createModal === 'spec';
    const [overviewTasks, setOverviewTasks] = useState<Task[]>([]);
    const [overviewReflections, setOverviewReflections] = useState<ReflectionReport[]>([]);
    const [overviewLoading, setOverviewLoading] = useState(false);
    const [overviewError, setOverviewError] = useState<string | null>(null);
    const [dependencyTasksByBoardId, setDependencyTasksByBoardId] = useState<Record<string, Task[]>>({});
    const [dependencyTasksLoadingByBoardId, setDependencyTasksLoadingByBoardId] = useState<Record<string, boolean>>({});
    const [processModalOpen, setProcessModalOpen] = useState(false);
    const [selectedUser, setSelectedUser] = useState<Member | null>(null);
    const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
    const [commandPaletteInitialQuery, setCommandPaletteInitialQuery] = useState<string | undefined>();

    const openCreateModal = useCallback((type: 'task' | 'spec') => {
        setCreateModal(type);
        const url = new URL(window.location.href);
        url.searchParams.set('create', type);
        window.history.replaceState(null, '', url.toString());
    }, []);
    const closeCreateModal = useCallback(() => {
        setCreateModal(null);
        const url = new URL(window.location.href);
        url.searchParams.delete('create');
        window.history.replaceState(null, '', url.toString());
    }, []);

    const suppressSingleKeys = showCreateTask || showCreateSpec || showCreateBoard || !!selectedTask
        || processModalOpen || !!selectedUser || commandPaletteOpen;

    const globalShortcutActions = useCallback(() => ({
        openCommandPalette: (initialQuery?: string) => {
            setCommandPaletteInitialQuery(initialQuery);
            setCommandPaletteOpen(true);
        },
        createTask: () => openCreateModal('task'),
        navigateTo: (path: string) => {
            const board = searchParams.get('board');
            navigate(board ? `${path}?board=${board}` : path);
        },
    }), [navigate, searchParams]);

    useGlobalShortcuts(globalShortcutActions(), suppressSingleKeys);
    useTaskCardAutoRefresh(selectedTask, setSelectedTask, service);

    const handleOpenCommandPalette = useCallback(() => {
        setCommandPaletteInitialQuery(undefined);
        setCommandPaletteOpen(true);
    }, []);

    const needsMembers = viewMode === 'board'
        || viewMode === 'settings'
        || viewMode === 'overview'
        || !!selectedTask;
    const needsSpecs = false;
    const needsLabels = viewMode === 'board'
        || !!selectedTask;

    const loadShellData = useCallback(async () => {
        setLoadingShell(true);
        setShellError(null);
        try {
            const [boardsResp, membersResp, specsResp, allLabels] = await Promise.all([
                service.fetchBoardsPage({ page: 1, page_size: 200, sort: 'name' }),
                needsMembers ? service.fetchMembersPage({ page: 1, page_size: 200, sort: 'name', board: boardFilter }) : Promise.resolve(null),
                needsSpecs ? service.fetchSpecsPage({ page: 1, page_size: 200, board: boardFilter }) : Promise.resolve(null),
                needsLabels ? service.getLabels(boardFilter) : Promise.resolve(null),
            ]);
            const loadedMembers = membersResp?.results || [];
            const loadedBoards = boardsResp.results.map(board => ({
                ...board,
                members: loadedMembers.filter(member => board.memberIds.includes(member.id)),
            }));
            setBoards(loadedBoards);
            if (membersResp) {
                setMembers(loadedMembers);
            }
            if (specsResp) {
                setSpecs(specsResp.results);
            }
            if (allLabels) {
                setLabels(allLabels);
            }
        } catch (err) {
            setShellError(err instanceof Error ? err.message : 'Unknown error');
        } finally {
            setLoadingShell(false);
        }
    }, [service, boardFilter, needsMembers, needsSpecs, needsLabels]);

    useEffect(() => {
        if (authLoading) return;
        if (authEnabled && !authUser) return;
        loadShellData();
    }, [authLoading, authEnabled, authUser, loadShellData, refreshKey]);

    // Auto-open create board modal on fresh install (no boards)
    useEffect(() => {
        if (!loadingShell && boards.length === 0 && !shellError) {
            setShowCreateBoard(true);
        }
    }, [loadingShell, boards.length, shellError]);

    const loadOverviewData = useCallback(async () => {
        setOverviewLoading(true);
        setOverviewError(null);
        try {
            const fetchTasks = async () => {
                const pageSize = 200;
                let page = 1;
                let allTasks: Task[] = [];
                let total = 0;

                while (page <= 1000) {
                    const resp = await service.fetchTimelinePage({
                        board: boardFilter,
                        page,
                        page_size: pageSize,
                        sort: '-created_at',
                    });
                    allTasks = allTasks.concat(resp.results);
                    total = resp.count;
                    if (!resp.next || allTasks.length >= total) break;
                    page += 1;
                }
                return allTasks;
            };

            const fetchReflections = async () => {
                try {
                    return await service.fetchAllReflections(boardFilter ? { board: boardFilter } : undefined);
                } catch {
                    return [];
                }
            };

            const [allTasks, allReflections] = await Promise.all([fetchTasks(), fetchReflections()]);
            setOverviewTasks(allTasks);
            setOverviewReflections(allReflections);
        } catch (err) {
            setOverviewError(err instanceof Error ? err.message : 'Failed to load overview data');
        } finally {
            setOverviewLoading(false);
        }
    }, [service, boardFilter]);

    useEffect(() => {
        if (authLoading) return;
        if (authEnabled && !authUser) return;
        if (viewMode !== 'overview') return;
        loadOverviewData();
    }, [authLoading, authEnabled, authUser, viewMode, loadOverviewData, refreshKey]);

    const currentBoard = useMemo(
        () => selectedBoard !== ALL_BOARDS_ID ? boards.find(b => b.id === selectedBoard) || null : null,
        [boards, selectedBoard],
    );
    /*
    const contextMembers = useMemo(
        () => selectedBoard === ALL_BOARDS_ID ? members : (currentBoard?.members || []),
        [members, selectedBoard, currentBoard],
    );
    */
    const memberMap = useMemo(
        () => new Map(members.map(member => [member.id, member])),
        [members],
    );
    const ensureDependencyTasksForBoard = useCallback(async (boardId: string, force = false) => {
        if (!boardId) return;
        if (!force && Object.prototype.hasOwnProperty.call(dependencyTasksByBoardId, boardId)) return;
        if (dependencyTasksLoadingByBoardId[boardId]) return;

        setDependencyTasksLoadingByBoardId(prev => ({ ...prev, [boardId]: true }));
        try {
            const pageSize = 200;
            let page = 1;
            let total = 0;
            let allTasks: Task[] = [];

            while (page <= 1000) {
                const resp = await service.fetchTasksPage({
                    board: boardId,
                    page,
                    page_size: pageSize,
                    sort: '-created_at',
                });
                allTasks = allTasks.concat(resp.results);
                total = resp.count;
                if (!resp.next || allTasks.length >= total) break;
                page += 1;
            }

            setDependencyTasksByBoardId(prev => ({ ...prev, [boardId]: allTasks }));
        } catch (err) {
            console.error(`Failed to load dependency candidates for board ${boardId}:`, err);
        } finally {
            setDependencyTasksLoadingByBoardId(prev => ({ ...prev, [boardId]: false }));
        }
    }, [dependencyTasksByBoardId, dependencyTasksLoadingByBoardId, service]);

    const refreshDependencyTasksForBoard = useCallback(async (boardId: string) => {
        if (!boardId) return;
        await ensureDependencyTasksForBoard(boardId, true);
    }, [ensureDependencyTasksForBoard]);

    const handleDependenciesChanged = useCallback(async () => {
        if (!selectedTask?.boardId) return;
        await refreshDependencyTasksForBoard(selectedTask.boardId);
        setRefreshKey(k => k + 1);
    }, [refreshDependencyTasksForBoard, selectedTask?.boardId]);


    /*
    const contextStats = useMemo((): DashboardStats => {
        const tasks = overviewTasks;
        const completedTasks = tasks.filter(t => t.currentStatus === 'DONE');
        const avgTimeToCompletionMs = completedTasks.length > 0
            ? completedTasks.reduce((sum, task) => sum + task.workTimeMs, 0) / completedTasks.length
            : 0;
        const totalMutations = tasks.reduce((sum, task) => sum + task.mutations.length, 0);

        const mutationsByMember = new Map<string, number>();
        tasks.forEach(task => {
            task.mutations.forEach(mutation => {
                mutationsByMember.set(mutation.actor, (mutationsByMember.get(mutation.actor) ?? 0) + 1);
            });
        });
        const mostActiveMember = [...mutationsByMember.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
            || members[0]?.fullName
            || '';

        let mostActiveBoard = currentBoard?.name || boards[0]?.name || '';
        if (selectedBoard === ALL_BOARDS_ID) {
            const boardCounts = new Map<string, number>();
            tasks.forEach(task => boardCounts.set(task.boardId, (boardCounts.get(task.boardId) ?? 0) + 1));
            const topBoardId = [...boardCounts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
            const topBoard = boards.find(board => board.id === topBoardId);
            mostActiveBoard = topBoard?.name || mostActiveBoard;
        }

        return {
            totalTasks: tasks.length,
            totalMembers: members.length,
            totalBoards: selectedBoard === ALL_BOARDS_ID ? boards.length : 1,
            completedTasks: completedTasks.length,
            inProgressTasks: tasks.filter(t => t.currentStatus === 'IN_PROGRESS').length,
            todoTasks: tasks.filter(t => t.currentStatus === 'TODO' || t.currentStatus === 'BACKLOG').length,
            avgTimeToCompletionMs,
            totalMutations,
            mostActiveBoard,
            mostActiveMember,
        };
    }, [overviewTasks, members, boards, currentBoard, selectedBoard]);
    */

    useEffect(() => {
        if (!selectedTask?.boardId) return;
        void ensureDependencyTasksForBoard(selectedTask.boardId);
    }, [selectedTask?.boardId, ensureDependencyTasksForBoard]);

    useEffect(() => {
        if (!showCreateTask || !boardFilter) return;
        void ensureDependencyTasksForBoard(boardFilter);
    }, [showCreateTask, boardFilter, ensureDependencyTasksForBoard]);

    const updateSearchParam = useCallback((key: string, value?: string | null) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (!value) next.delete(key);
            else next.set(key, value);
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    // Auto-select board when on board view and no board param is set
    // Prefer last-visited board from localStorage, fall back to first board
    useEffect(() => {
        if (viewMode === 'board' && !loadingShell && boards.length > 0 && selectedBoard === ALL_BOARDS_ID && !searchParams.get('board')) {
            const lastBoard = localStorage.getItem('taskit-last-board');
            const targetBoard = lastBoard && boards.some(b => b.id === lastBoard) ? lastBoard : boards[0].id;
            updateSearchParam('board', targetBoard);
        }
    }, [viewMode, loadingShell, boards, selectedBoard, searchParams, updateSearchParam]);

    // Persist selected board to localStorage
    useEffect(() => {
        if (selectedBoard !== ALL_BOARDS_ID) {
            localStorage.setItem('taskit-last-board', selectedBoard);
        }
    }, [selectedBoard]);

    const markTaskSignalsSeen = useCallback((task: Task) => {
        markCommentsSeen(task.id, task.comments?.length ?? 0);
        const latestExecutionTransitionTs = getLatestExecutionTransitionTimestamp(task);
        if (latestExecutionTransitionTs > 0) {
            markExecutionTransitionSeen(task.id, latestExecutionTransitionTs);
        }
    }, []);

    const handleBoardChange = (value: string) => {
        if (value === ALL_BOARDS_ID) {
            updateSearchParam('board', null);
            navigate('/settings?tab=boards');
        } else {
            updateSearchParam('board', value);
        }
    };

    const handleNavChange = (value: string) => {
        const route = VIEW_ROUTES.find(r => r.id === value);
        if (route) {
            const board = searchParams.get('board');
            navigate(board ? `${route.path}?board=${board}` : route.path);
        }
    };

    const handleTaskSelect = (taskOrId: Task | string) => {
        const taskId = typeof taskOrId === 'string' ? taskOrId : taskOrId.id;
        if (typeof taskOrId !== 'string') setSelectedTask(taskOrId);
        updateSearchParam('taskId', taskId);
        setTaskDetailLoading(true);
        service.fetchTaskDetail(taskId)
            .then(detail => {
                setSelectedTask(detail);
                markTaskSignalsSeen(detail);
            })
            .catch(err => console.error('Failed to fetch task detail:', err))
            .finally(() => setTaskDetailLoading(false));
    };

    const handleTaskClose = () => {
        updateSearchParam('taskId', null);
        setSelectedTask(null);
    };

    useEffect(() => {
        const taskId = searchParams.get('taskId');
        if (!taskId) {
            setSelectedTask(null);
            return;
        }
        if (selectedTask?.id === taskId) return;
        setTaskDetailLoading(true);
        service.fetchTaskDetail(taskId)
            .then(detail => {
                setSelectedTask(detail);
                markTaskSignalsSeen(detail);
            })
            .catch(err => console.error('Failed to fetch task detail from URL:', err))
            .finally(() => setTaskDetailLoading(false));
    }, [searchParams, selectedTask?.id, service, markTaskSignalsSeen]);

    const handleUpdateUser = async (id: string, name: string, email: string, color: string, availableModels?: Array<{ name: string; description: string; is_default: boolean }>) => {
        await service.updateUser(id, name, email, color, availableModels);
        setRefreshKey(k => k + 1);
    };
    const handleCreateBoard = async (
        input: {
            name: string;
            description: string;
            disabledAgents?: string[];
        } & (
                { directoryMode: 'existing'; workingDir: string }
                | { directoryMode: 'create'; parentDirectory: string; directoryName: string }
            )
    ) => {
        const result = await service.createBoard(input) as { id?: number };
        if (result?.id) {
            updateSearchParam('board', String(result.id));
        }
        setRefreshKey(k => k + 1);
    };
    const handleDeleteBoard = async (boardId: string) => {
        await service.deleteBoard(boardId);
        if (selectedBoard === boardId) {
            const remaining = boards.filter(b => b.id !== boardId);
            updateSearchParam('board', remaining.length > 0 ? remaining[0].id : null);
        }
        setRefreshKey(k => k + 1);
    };
    const handleCreateTask = async (
        boardId: string, title: string, description: string, priority: string,
        assigneeId: number, modelName: string | undefined, devEta?: number,
        labelIds?: number[], dependsOn?: string[], workingDir?: string,
        skipReflection?: boolean
    ): Promise<string> => {
        const result = await service.createTask(
            boardId,
            title,
            description,
            priority,
            authUser?.email || undefined,
            devEta,
            {
                assigneeId,
                modelName,
                labelIds,
                dependsOn,
                workingDir,
                skipReflection,
            },
        ) as { id: number };
        await refreshDependencyTasksForBoard(boardId);
        setRefreshKey(k => k + 1);
        return String(result.id);
    };
    const handleCreateSchedule = async (payload: Record<string, unknown>) => {
        await service.createSchedule(payload);
        setRefreshKey(k => k + 1);
    };
    const handleUpdateAssignees = async (taskId: string, memberIds: string[], defaultModel?: string) => {
        try {
            await service.updateTaskAssignees(taskId, memberIds);
            // Update model atomically so the re-fetch below picks up both changes
            if (defaultModel) {
                await service.updateTask(taskId, { modelName: defaultModel } as Parameters<typeof service.updateTask>[1]);
            }
            setRefreshKey(k => k + 1);
            if (selectedTask?.id === taskId) {
                const detail = await service.fetchTaskDetail(taskId);
                setSelectedTask(detail);
            }
        } catch (e) {
            console.error('Failed to update assignees', e);
            toast({
                title: 'Error',
                description: parseApiDetail(e) || 'Failed to update assignees',
                variant: 'destructive',
            });
        }
    };
    const handleUpdateTask = async (taskId: string, updates: Record<string, unknown>) => {
        // Optimistically update the open modal so it never closes/flashes
        if (selectedTask?.id === taskId) {
            setSelectedTask(prev => prev ? { ...prev, ...updates } : prev);
        }
        try {
            await service.updateTask(taskId, updates as Parameters<typeof service.updateTask>[1]);
            // Refresh dependency tasks if dependencies changed
            const boardId = selectedTask?.id === taskId ? selectedTask.boardId : null;
            if (boardId && 'dependsOn' in updates) {
                await refreshDependencyTasksForBoard(boardId);
            }
            // Only refresh the board when the update affects kanban-visible fields
            const boardVisibleFields = new Set(['status', 'priority', 'assigneeId', 'title', 'labelIds', 'kanbanTargetIndex', 'kanbanTargetStatus']);
            const affectsBoard = Object.keys(updates).some(k => boardVisibleFields.has(k));
            if (affectsBoard) {
                setRefreshKey(k => k + 1);
            }
            // Re-sync the modal with server truth after the API call
            if (selectedTask?.id === taskId) {
                const detail = await service.fetchTaskDetail(taskId);
                setSelectedTask(detail);
            }
        } catch (e) {
            // Revert optimistic update on failure
            if (selectedTask?.id === taskId) {
                const detail = await service.fetchTaskDetail(taskId).catch(() => null);
                if (detail) setSelectedTask(detail);
            }
            console.error('Failed to update task', e);
            toast({
                title: 'Error',
                description: parseApiDetail(e) || 'Failed to update task',
                variant: 'destructive',
            });
        }
    };
    const handleKanbanTaskMove = async (
        taskId: string,
        move: { status: string; targetIndex?: number }
    ): Promise<boolean> => {
        try {
            await service.updateTask(taskId, {
                status: move.status,
                kanbanTargetIndex: move.targetIndex,
            });
            if (selectedTask?.id === taskId) {
                const detail = await service.fetchTaskDetail(taskId);
                setSelectedTask(detail);
            }
            return true;
        } catch (e) {
            console.error('Failed to move kanban task', e);
            toast({
                title: 'Error',
                description: parseApiDetail(e) || 'Failed to update task status',
                variant: 'destructive',
            });
            return false;
        }
    };
    const handleStopExecution = async (taskId: string, targetStatus: string): Promise<boolean> => {
        try {
            const result = await service.stopExecution(taskId, targetStatus);
            if (selectedTask?.id === taskId) {
                const detail = await service.fetchTaskDetail(taskId);
                setSelectedTask(detail);
            }
            // The stop command reported an error, but the backend applied the
            // transition anyway (the signal likely landed — this is recoverable).
            // Surface it as a non-blocking info toast, not a destructive one.
            if (result?.stop_warning) {
                toast({
                    title: 'Task moved',
                    description: `Stop signal warning: ${result.stop_warning}`,
                });
            }
            return true;
        } catch (e) {
            console.error('Failed to stop execution', e);
            // With the new backend flow, a thrown error here means a genuine
            // 409 (unexpected terminal transition during stop) or network/5xx.
            // Prefer the backend's detail message if present.
            const detail = parseApiDetail(e);
            toast({
                title: 'Could not move task',
                description: detail || 'The task transitioned to an unexpected state while stopping. Refresh to see its current status.',
                variant: 'destructive',
            });
            return false;
        }
    };
    const handleDeleteTask = async (taskId: string) => {
        try {
            const boardId = selectedTask?.id === taskId ? selectedTask.boardId : null;
            await service.deleteTask(taskId);
            if (boardId) {
                await refreshDependencyTasksForBoard(boardId);
            }
            setSelectedTask(null);
            updateSearchParam('taskId', null);
            setRefreshKey(k => k + 1);
        } catch (e) {
            console.error('Failed to delete task', e);
            toast({ title: 'Error', description: 'Failed to delete task', variant: 'destructive' });
        }
    };
    const handleDeleteSpec = async (specId: string) => {
        try {
            await service.deleteSpec(specId);
            const board = searchParams.get('board');
            navigate(board ? `/specs?board=${board}` : '/specs');
            setRefreshKey(k => k + 1);
        } catch (e) {
            console.error('Failed to delete spec', e);
            toast({ title: 'Error', description: 'Failed to delete spec', variant: 'destructive' });
        }
    };

    // Re-fetch task detail (comments, mutations) for an open modal.
    // Called after posting a comment or reply so the new data appears immediately.
    const refreshTaskDetail = useCallback(async (taskId: string) => {
        try {
            const detail = await service.fetchTaskDetail(taskId);
            setSelectedTask(prev => {
                if (!prev || prev.id !== taskId) return prev;
                detail.boardName = prev.boardName;
                return detail;
            });
        } catch (err) {
            console.error('Failed to refresh task detail:', err);
        }
    }, [service]);

    if (authLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div className="size-10 rounded-full border-2 border-border border-t-primary animate-spin" />
            </div>
        );
    }
    if (authEnabled && !authUser) return <LoginPage />;
    if (authEnabled && authUser?.mustChangePassword) return <ChangePasswordPage />;
    if (loadingShell) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div className="size-10 rounded-full border-2 border-border border-t-primary animate-spin" />
            </div>
        );
    }
    if (shellError) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div className="text-center max-w-md bg-card p-8 rounded-xl border border-border">
                    <h2 className="text-lg font-bold mb-2">Failed to load shell data</h2>
                    <p className="text-muted-foreground text-sm leading-relaxed mb-6">{shellError}</p>
                    <Button onClick={() => setRefreshKey(k => k + 1)}>Retry</Button>
                </div>
            </div>
        );
    }

    const isFullWidthBoard = viewMode === 'board';

    return (
        <TooltipProvider>
            <div className="flex flex-col min-h-screen">
                <AppHeader
                    boards={boards}
                    selectedBoard={selectedBoard}
                    currentBoard={currentBoard}
                    isAllBoards={selectedBoard === ALL_BOARDS_ID}
                    viewMode={viewMode}
                    onBoardChange={handleBoardChange}
                    onNavChange={handleNavChange}
                    onCreateTask={() => openCreateModal('task')}
                    onCreateSpec={() => openCreateModal('spec')}
                    onCreateBoard={() => setShowCreateBoard(true)}
                    onNavigateHome={() => {
                        const board = searchParams.get('board');
                        navigate(board ? `/board?board=${board}` : '/board');
                    }}
                    onOpenProcessMonitor={() => setProcessModalOpen(true)}
                    onOpenCommandPalette={handleOpenCommandPalette}
                />

                <main className={`flex-1 w-full py-6 px-4 sm:px-6 lg:px-8 ${isFullWidthBoard ? 'max-w-none' : 'max-w-[90vw] mx-auto'}`}>
                    <Routes>
                        <Route path="/" element={<Navigate to="/board" replace />} />
                        <Route path="/stats" element={
                            <>
                                <SectionHeader title="Stats" />
                                <KPICards tasks={overviewTasks} reflections={overviewReflections} />
                                {overviewError && (
                                    <div className="text-sm text-destructive mb-4">{overviewError}</div>
                                )}
                                {overviewLoading && overviewTasks.length === 0 ? (
                                    <div className="text-sm text-muted-foreground py-8">Loading overview charts...</div>
                                ) : (
                                    <DashboardCharts tasks={overviewTasks} members={members} reflections={overviewReflections} />
                                )}
                            </>
                        } />
                        <Route path="/board" element={
                            <BoardPage
                                selectedBoard={boardFilter}
                                refreshKey={refreshKey}
                                filteredMemberId={filteredMemberId}
                                memberMap={memberMap}
                                members={members}
                                labels={labels}
                                currentBoard={currentBoard}
                                dependencyTasks={boardFilter ? (dependencyTasksByBoardId[boardFilter] || []) : []}
                                dependencyTasksLoading={boardFilter ? dependencyTasksLoadingByBoardId[boardFilter] : false}
                                onEnsureDependencyTasks={ensureDependencyTasksForBoard}
                                onDependenciesSaved={handleDependenciesChanged}
                                onTaskClick={handleTaskSelect}
                                onTaskMove={handleKanbanTaskMove}
                                onStopExecution={handleStopExecution}
                                onDeleteTask={handleDeleteTask}
                            />
                        } />
                        {/* Backwards-compat redirects */}
                        <Route path="/tasks" element={<Navigate to="/board?view=list" replace />} />
                        <Route path="/kanban" element={<Navigate to="/board?view=kanban" replace />} />
                        <Route path="/timeline" element={<Navigate to="/board?view=timeline" replace />} />
                        <Route path="/members" element={<Navigate to="/settings" replace />} />
                        <Route path="/scheduling" element={
                            <SchedulingPage selectedBoard={boardFilter} refreshKey={refreshKey} onTaskClick={(taskId) => handleTaskSelect(taskId)} />
                        } />
                        <Route path="/specs" element={
                            <>
                                <SectionHeader title="Specs" />
                                <SpecsPage selectedBoard={boardFilter} refreshKey={refreshKey} currentBoard={currentBoard} onSpecClick={(s: Spec) => {
                                    const board = searchParams.get('board');
                                    navigate(board ? `/specs/${s.id}?board=${board}` : `/specs/${s.id}`);
                                }} onDataChange={() => setRefreshKey(k => k + 1)} />
                            </>
                        } />
                        <Route path="/specs/:specId" element={
                            <SpecDetailRoute onTaskClick={handleTaskSelect} onDeleteSpec={handleDeleteSpec} specs={specs} />
                        } />
                        <Route path="/specs/:specId/debug" element={
                            <SpecDebugRoute onTaskClick={handleTaskSelect} />
                        } />
                        <Route path="/reflections" element={
                            <ReflectionListRoute boards={boards} onTaskClick={handleTaskSelect} />
                        } />
                        <Route path="/reflections/:reportId" element={
                            <ReflectionDetailRoute onTaskClick={handleTaskSelect} />
                        } />
                        <Route path="/notifications" element={<NotificationsPage />} />
                        <Route path="/providers" element={<ProvidersPage />} />
                        <Route path="/analytics" element={
                            <>
                                <SectionHeader title="Analytics" />
                                <AnalyticsPage boards={boards} onTaskClick={handleTaskSelect} />
                            </>
                        } />
                        <Route path="/settings" element={
                            <SettingsView
                                members={members}
                                currentBoard={currentBoard}
                                onDataChange={() => setRefreshKey(k => k + 1)}
                                onCreateBoard={() => setShowCreateBoard(true)}
                                onDeleteBoard={handleDeleteBoard}
                                dark={dark}
                                onToggleDark={() => setDark(d => !d)}
                            />
                        } />
                        <Route path="*" element={<Navigate to="/board" replace />} />
                    </Routes>
                </main>

                {selectedTask && (
                    <TaskDetailModal
                        task={selectedTask}
                        onClose={handleTaskClose}
                        allMembers={members}
                        allTasks={dependencyTasksByBoardId[selectedTask.boardId] || []}
                        memberMap={memberMap}
                        onUpdateAssignees={handleUpdateAssignees}
                        onUpdateTask={handleUpdateTask}
                        onSelectTask={(taskId: string) => handleTaskSelect(taskId)}
                        availableStatuses={service.getAvailableStatuses()}
                        onDeleteTask={handleDeleteTask}
                        availableLabels={labels}
                        detailLoading={taskDetailLoading}
                        onRefresh={refreshTaskDetail}
                    />
                )}

                {selectedUser && <EditUserModal user={selectedUser} onClose={() => setSelectedUser(null)} onUpdate={handleUpdateUser} />}
                {showCreateBoard && <CreateBoardModal onClose={() => setShowCreateBoard(false)} onCreate={handleCreateBoard} />}
                {showCreateTask && (
                    <CreateTaskModal
                        boards={boards}
                        dependencyTasksByBoard={dependencyTasksByBoardId}
                        dependencyTasksLoadingByBoard={dependencyTasksLoadingByBoardId}
                        onEnsureDependencyTasks={ensureDependencyTasksForBoard}
                        defaultBoardId={selectedBoard === ALL_BOARDS_ID ? undefined : selectedBoard}
                        users={members.map(m => ({
                            id: Number(m.id),
                            name: m.fullName,
                            email: m.email,
                            role: m.role,
                            availableModels: m.availableModels,
                        }))}
                        onClose={closeCreateModal}
                        onCreate={handleCreateTask}
                        onCreateSchedule={handleCreateSchedule}
                        availableLabels={labels}
                    />
                )}

                <CreateSpecModal
                    open={showCreateSpec}
                    onOpenChange={(open) => { if (!open) closeCreateModal(); }}
                    boardId={selectedBoard === ALL_BOARDS_ID ? '' : (selectedBoard ?? '')}
                    board={currentBoard ?? null}
                    onCreated={(specId) => {
                        closeCreateModal();
                        const board = searchParams.get('board');
                        navigate(board ? `/specs/${specId}?board=${board}` : `/specs/${specId}`);
                    }}
                    onClose={closeCreateModal}
                />

                <ProcessMonitorModal
                    open={processModalOpen}
                    onOpenChange={setProcessModalOpen}
                    boardId={boardFilter}
                    refreshKey={refreshKey}
                    onTaskStopped={() => setRefreshKey(k => k + 1)}
                />

                <CommandPalette
                    open={commandPaletteOpen}
                    onClose={() => { setCommandPaletteOpen(false); setCommandPaletteInitialQuery(undefined); }}
                    boards={boards}
                    navigate={(path) => { const b = searchParams.get('board'); navigate(b ? `${path}?board=${b}` : path); }}
                    onCreateTask={() => { setCommandPaletteOpen(false); openCreateModal('task'); }}
                    onCreateBoard={() => { setCommandPaletteOpen(false); setShowCreateBoard(true); }}
                    onTaskSelect={(taskId) => { setCommandPaletteOpen(false); handleTaskSelect(taskId); }}
                    onBoardChange={(id) => { setCommandPaletteOpen(false); handleBoardChange(id); }}
                    onOpenProcessMonitor={() => { setCommandPaletteOpen(false); setProcessModalOpen(true); }}
                    initialQuery={commandPaletteInitialQuery}
                />
            </div>
            <Toaster />
        </TooltipProvider>
    );
}

function SpecDetailRoute({ onTaskClick, onDeleteSpec, specs }: {
    onTaskClick: (taskId: string) => void;
    onDeleteSpec: (specId: string) => void;
    specs?: Spec[];
}) {
    const { specId } = useParams();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    if (!specId) return <Navigate to="/specs" replace />;
    const cachedSpec = specs?.find(s => s.id === specId);
    const board = searchParams.get('board');
    return (
        <>
            <SectionHeader title="Specs" />
            <SpecDetailView
                specId={specId}
                spec={cachedSpec}
                onBack={() => navigate(board ? `/specs?board=${board}` : '/specs')}
                onTaskClick={onTaskClick}
                onDeleteSpec={onDeleteSpec}
            />
        </>
    );
}

function ReflectionListRoute({ boards, onTaskClick }: { boards: Board[]; onTaskClick: (taskId: string) => void }) {
    const [searchParams] = useSearchParams();
    const boardId = searchParams.get('board');
    const boardName = boardId ? boards.find(b => b.id === boardId)?.name : undefined;
    return (
        <>
            <SectionHeader title="Reflections" />
            <ReflectionListView onTaskClick={onTaskClick} boardName={boardName} />
        </>
    );
}

function ReflectionDetailRoute({ onTaskClick }: { onTaskClick: (taskId: string) => void }) {
    const { reportId } = useParams();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    if (!reportId) return <Navigate to="/reflections" replace />;
    const board = searchParams.get('board');
    return (
        <>
            <SectionHeader title="Reflection Detail" />
            <ReflectionDetailView
                reportId={reportId}
                onBack={() => navigate(board ? `/reflections?board=${board}` : '/reflections')}
                onTaskClick={onTaskClick}
            />
        </>
    );
}

function SpecDebugRoute({ onTaskClick }: { onTaskClick: (taskId: string) => void }) {
    const { specId } = useParams();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    if (!specId) return <Navigate to="/specs" replace />;
    const board = searchParams.get('board');
    return (
        <>
            <SectionHeader title="Spec Debug" />
            <SpecDebugView
                specId={specId}
                onBack={() => navigate(board ? `/specs/${specId}?board=${board}` : `/specs/${specId}`)}
                onTaskClick={onTaskClick}
            />
        </>
    );
}

function SectionHeader({ title }: { title: string }) {
    return (
        <h2 className="text-lg font-semibold tracking-tight mb-5">{title}</h2>
    );
}

export default App;
