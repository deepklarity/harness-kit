import { useState, useEffect, useCallback, useMemo } from 'react';
import { Routes, Route, useNavigate, useLocation, useParams, Navigate, useSearchParams } from 'react-router-dom';
import type { Board, DashboardStats, Label, Member, ReflectionReport, Spec, Task, ViewMode } from './types';
import { useService } from './contexts/ServiceContext';
import { useAuth } from './contexts/AuthContext';
import { useToast } from '@/hooks/use-toast';
import { LoginPage } from './components/LoginPage';
import { ChangePasswordPage } from './components/ChangePasswordPage';
import { AppHeader, ALL_BOARDS_ID, VIEW_ROUTES } from './components/AppHeader';
import { TaskDetailModal } from './components/TaskDetailModal';
import { CreateBoardModal } from './components/CreateBoardModal';
import { CreateTaskModal } from './components/CreateTaskModal';
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
import { BoardPage, SpecsPage } from './components/pages';

function pathToViewMode(pathname: string): ViewMode {
    const match = VIEW_ROUTES.find(r => r.path === pathname);
    if (match) return match.id;
    if (pathname.startsWith('/specs/')) return 'specs';
    if (pathname.includes('/debug')) return 'specs';
    if (pathname.startsWith('/reflections')) return 'reflections';
    if (pathname === '/settings') return 'settings';
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
    const [filteredMemberId, setFilteredMemberId] = useState<string | null>(null);
    const [selectedTask, setSelectedTask] = useState<Task | null>(null);
    const [taskDetailLoading, setTaskDetailLoading] = useState(false);
    const [showCreateBoard, setShowCreateBoard] = useState(false);
    const [showCreateTask, setShowCreateTask] = useState(false);
    const [overviewTasks, setOverviewTasks] = useState<Task[]>([]);
    const [overviewReflections, setOverviewReflections] = useState<ReflectionReport[]>([]);
    const [overviewLoading, setOverviewLoading] = useState(false);
    const [overviewError, setOverviewError] = useState<string | null>(null);
    const [processModalOpen, setProcessModalOpen] = useState(false);
    const [selectedUser, setSelectedUser] = useState<Member | null>(null);

    const needsMembers = viewMode === 'board'
        || viewMode === 'settings'
        || viewMode === 'overview'
        || showCreateTask
        || !!selectedTask;
    const needsSpecs = showCreateTask;
    const needsLabels = viewMode === 'board'
        || showCreateTask
        || !!selectedTask;

    const loadShellData = useCallback(async () => {
        setLoadingShell(true);
        setShellError(null);
        try {
            const [boardsResp, membersResp, specsResp, allLabels] = await Promise.all([
                service.fetchBoardsPage({ page: 1, page_size: 200, sort: 'name' }),
                needsMembers ? service.fetchMembersPage({ page: 1, page_size: 200, sort: 'name', board: boardFilter }) : Promise.resolve(null),
                needsSpecs ? service.fetchSpecsPage({ page: 1, page_size: 200, board: boardFilter }) : Promise.resolve(null),
                needsLabels ? service.getLabels() : Promise.resolve(null),
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
    const contextMembers = useMemo(
        () => selectedBoard === ALL_BOARDS_ID ? members : (currentBoard?.members || []),
        [members, selectedBoard, currentBoard],
    );
    const memberMap = useMemo(
        () => new Map(members.map(member => [member.id, member])),
        [members],
    );

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

    const updateSearchParam = useCallback((key: string, value?: string | null) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (!value) next.delete(key);
            else next.set(key, value);
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    // Auto-select first board when no board param is set
    useEffect(() => {
        if (!loadingShell && boards.length > 0 && selectedBoard === ALL_BOARDS_ID && !searchParams.get('board')) {
            updateSearchParam('board', boards[0].id);
        }
    }, [loadingShell, boards, selectedBoard, searchParams, updateSearchParam]);

    const markTaskSignalsSeen = useCallback((task: Task) => {
        markCommentsSeen(task.id, task.comments?.length ?? 0);
        const latestExecutionTransitionTs = getLatestExecutionTransitionTimestamp(task);
        if (latestExecutionTransitionTs > 0) {
            markExecutionTransitionSeen(task.id, latestExecutionTransitionTs);
        }
    }, []);

    const handleBoardChange = (value: string) => {
        updateSearchParam('board', value === ALL_BOARDS_ID ? null : value);
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

    const handleUpdateUser = async (id: string, name: string, email: string, color: string, availableModels?: Array<{name: string; description: string; is_default: boolean}>) => {
        await service.updateUser(id, name, email, color, availableModels);
        setRefreshKey(k => k + 1);
    };
    const handleCreateBoard = async (name: string, description: string, workingDir: string, disabledAgents?: string[]) => {
        const result = await service.createBoard(name, description, workingDir, disabledAgents) as { id?: number };
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
        assigneeId: number, modelName: string | undefined, devEta?: number, labelIds?: number[], workingDir?: string
    ) => {
        await service.createTask(
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
                workingDir,
            },
        );
        setRefreshKey(k => k + 1);
    };
    const handleUpdateAssignees = async (taskId: string, memberIds: string[]) => {
        try {
            await service.updateTaskAssignees(taskId, memberIds);
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
        try {
            await service.updateTask(taskId, updates as Parameters<typeof service.updateTask>[1]);
            setRefreshKey(k => k + 1);
            if (selectedTask?.id === taskId) {
                const detail = await service.fetchTaskDetail(taskId);
                setSelectedTask(detail);
            }
        } catch (e) {
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
            await service.stopExecution(taskId, targetStatus);
            if (selectedTask?.id === taskId) {
                const detail = await service.fetchTaskDetail(taskId);
                setSelectedTask(detail);
            }
            return true;
        } catch (e) {
            console.error('Failed to stop execution', e);
            toast({ title: 'Error', description: 'Failed to stop execution', variant: 'destructive' });
            return false;
        }
    };
    const handleDeleteTask = async (taskId: string) => {
        try {
            await service.deleteTask(taskId);
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

    const boardViewParam = searchParams.get('view');
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
                    dark={dark}
                    onBoardChange={handleBoardChange}
                    onNavChange={handleNavChange}
                    onToggleDark={() => setDark(d => !d)}
                    onCreateTask={() => setShowCreateTask(true)}
                    onCreateBoard={() => setShowCreateBoard(true)}
                    onNavigateHome={() => {
                        const board = searchParams.get('board');
                        navigate(board ? `/board?board=${board}` : '/board');
                    }}
                    onOpenProcessMonitor={() => setProcessModalOpen(true)}
                />

                <main className={`flex-1 w-full p-8 ${isFullWidthBoard ? 'max-w-none px-6 lg:px-8' : 'max-w-[90vw] mx-auto'}`}>
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
                                onTaskClick={handleTaskSelect}
                                onTaskMove={handleKanbanTaskMove}
                                onStopExecution={handleStopExecution}
                            />
                        } />
                        {/* Backwards-compat redirects */}
                        <Route path="/tasks" element={<Navigate to="/board?view=list" replace />} />
                        <Route path="/kanban" element={<Navigate to="/board?view=kanban" replace />} />
                        <Route path="/timeline" element={<Navigate to="/board?view=timeline" replace />} />
                        <Route path="/members" element={<Navigate to="/settings" replace />} />
                        <Route path="/specs" element={
                            <>
                                <SectionHeader title="Specs" />
                                <SpecsPage selectedBoard={boardFilter} refreshKey={refreshKey} currentBoard={currentBoard} onSpecClick={(s: Spec) => {
                                    const board = searchParams.get('board');
                                    navigate(board ? `/specs/${s.id}?board=${board}` : `/specs/${s.id}`);
                                }} />
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
                        <Route path="/settings" element={
                            <>
                                <SectionHeader title="Settings" />
                                <SettingsView
                                    boards={boards}
                                    members={members}
                                    onDataChange={() => setRefreshKey(k => k + 1)}
                                    onCreateBoard={() => setShowCreateBoard(true)}
                                    onDeleteBoard={handleDeleteBoard}
                                />
                            </>
                        } />
                        <Route path="*" element={<Navigate to="/board" replace />} />
                    </Routes>
                </main>

                {selectedTask && (
                    <TaskDetailModal
                        task={selectedTask}
                        onClose={handleTaskClose}
                        allMembers={members}
                        allTasks={[]}
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
                        defaultBoardId={selectedBoard === ALL_BOARDS_ID ? undefined : selectedBoard}
                        users={members.map(m => ({
                            id: Number(m.id),
                            name: m.fullName,
                            email: m.email,
                            role: m.role,
                            availableModels: m.availableModels,
                        }))}
                        onClose={() => setShowCreateTask(false)}
                        onCreate={handleCreateTask}
                        availableLabels={labels}
                    />
                )}

                <ProcessMonitorModal
                    open={processModalOpen}
                    onOpenChange={setProcessModalOpen}
                    boardId={boardFilter}
                    refreshKey={refreshKey}
                    onTaskStopped={() => setRefreshKey(k => k + 1)}
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
