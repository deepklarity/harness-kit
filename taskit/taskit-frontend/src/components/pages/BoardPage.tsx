import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { format, subDays } from 'date-fns';
import type { Board, Label, Member, Task } from '@/types';
import { useService } from '@/contexts/ServiceContext';
import { usePolling } from '@/hooks/usePolling';
import { DependencyBoardView } from '@/components/DependencyBoardView';
import { KanbanBoard } from '@/components/KanbanBoard';
import { KanbanTaskSearch } from '@/components/KanbanTaskSearch';
import { TimelineView } from '@/components/TimelineView';
import { OdinGuideModal, OdinGuideContent } from '@/components/OdinGuideModal';
import { FilterBar, MultiSelectFilter, PaginationControls, SearchBar, SortControl, DateRangeFilter } from '@/components/filters';
import { TaskList } from '@/components/TaskCard';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ClipboardList, Columns3, Calendar, GitBranch, Plus, Terminal } from 'lucide-react';
import type { TaskSearchResult } from '@/services/integration/IntegrationService';
import { didLeaveProgressStatus, markExecutionTransitionUnseen } from '@/utils/unseenStatusTransitions';

// ─── Types ──────────────────────────────────────────────────

type BoardView = 'list' | 'kanban' | 'timeline' | 'dependencies';

interface BoardPageProps {
    selectedBoard?: string;
    refreshKey?: number;
    filteredMemberId?: string | null;
    memberMap: Map<string, Member>;
    members: Member[];
    labels: Label[];
    currentBoard?: Board | null;
    dependencyTasks?: Task[];
    dependencyTasksLoading?: boolean;
    onEnsureDependencyTasks?: (boardId: string, force?: boolean) => Promise<void> | void;
    onDependenciesSaved?: (taskIds: string[]) => Promise<void> | void;
    onTaskClick: (task: Task) => void;
    onTaskMove: (taskId: string, move: { status: string; targetIndex?: number }) => Promise<boolean>;
    onStopExecution: (taskId: string, targetStatus: string) => Promise<boolean>;
    onDeleteTask?: (taskId: string) => void;
}

// ─── View Toggle ────────────────────────────────────────────

const VIEW_OPTIONS: { value: BoardView; label: string; icon: typeof ClipboardList }[] = [
    { value: 'list', label: 'List', icon: ClipboardList },
    { value: 'kanban', label: 'Kanban', icon: Columns3 },
    { value: 'timeline', label: 'Timeline', icon: Calendar },
    { value: 'dependencies', label: 'DAG', icon: GitBranch },
];

function ViewToggle({ value, onChange }: { value: BoardView; onChange: (v: BoardView) => void }) {
    return (
        <ToggleGroup type="single" value={value} onValueChange={(v) => v && onChange(v as BoardView)} size="sm">
            {VIEW_OPTIONS.map(opt => {
                const Icon = opt.icon;
                return (
                    <ToggleGroupItem key={opt.value} value={opt.value} aria-label={opt.label} className="gap-1.5 text-xs px-2 sm:px-3">
                        <Icon className="size-3.5" />
                        <span className="hidden sm:inline">{opt.label}</span>
                    </ToggleGroupItem>
                );
            })}
        </ToggleGroup>
    );
}

// ─── List View ──────────────────────────────────────────────

const STATUS_OPTIONS = ['BACKLOG', 'TODO', 'IN_PROGRESS', 'EXECUTING', 'REVIEW', 'TESTING', 'DONE', 'FAILED'];
const PRIORITY_OPTIONS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

function splitParam(value: string | null): string[] {
    if (!value) return [];
    return value.split(',').map(v => v.trim()).filter(Boolean);
}

function ListView({ selectedBoard, refreshKey = 0, memberMap, members, labels, onTaskClick, onDeleteTask }: {
    selectedBoard?: string;
    refreshKey?: number;
    memberMap: Map<string, Member>;
    members: Member[];
    labels: Label[];
    onTaskClick: (task: Task) => void;
    onDeleteTask?: (taskId: string) => void;
}) {
    const service = useService();
    const [searchParams, setSearchParams] = useSearchParams();
    const [tasks, setTasks] = useState<Task[]>([]);
    const [count, setCount] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const qParam = searchParams.get('q');
    const statusParam = searchParams.get('status');
    const assigneeParam = searchParams.get('assignee');
    const priorityParam = searchParams.get('priority');
    const specParam = searchParams.get('spec');
    const labelsParam = searchParams.get('labels');
    const sortParam = searchParams.get('sort');
    const createdFromParam = searchParams.get('created_from');
    const createdToParam = searchParams.get('created_to');
    const pageParam = searchParams.get('page');
    const pageSizeParam = searchParams.get('page_size');

    const query = useMemo(() => {
        const page = Number(pageParam || '1');
        const pageSize = Number(pageSizeParam || '20');
        return {
            q: qParam || '',
            status: splitParam(statusParam),
            assignee: splitParam(assigneeParam),
            priority: splitParam(priorityParam),
            spec: splitParam(specParam),
            labels: splitParam(labelsParam),
            sort: sortParam || undefined,
            created_from: createdFromParam || undefined,
            created_to: createdToParam || undefined,
            page: Number.isNaN(page) ? 1 : page,
            page_size: Number.isNaN(pageSize) ? 20 : pageSize,
        };
    }, [
        qParam, statusParam, assigneeParam, priorityParam, specParam,
        labelsParam, sortParam, createdFromParam, createdToParam,
        pageParam, pageSizeParam
    ]);

    const setParam = useCallback((key: string, value?: string) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            const current = next.get(key) || '';
            const nextValue = value || '';
            const changed = current !== nextValue;
            if (!value) next.delete(key);
            else next.set(key, value);
            if (key !== 'page' && changed) next.set('page', '1');
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const resp = await service.fetchTasksPage({
                ...query,
                board: selectedBoard,
            });
            setTasks(resp.results);
            setCount(resp.count);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load tasks');
        } finally {
            setLoading(false);
        }
    }, [service, query, selectedBoard]);

    useEffect(() => {
        load();
    }, [load, refreshKey]);

    const hasFilters = useMemo(() => {
        const filterKeys = ['q', 'status', 'assignee', 'priority', 'spec', 'labels', 'sort', 'created_from', 'created_to'];
        return filterKeys.some(key => searchParams.has(key));
    }, [searchParams]);

    return (
        <div>
            <FilterBar 
                onClearAll={() => setSearchParams(prev => {
                    const next = new URLSearchParams();
                    const board = prev.get('board');
                    if (board) next.set('board', board);
                    const view = prev.get('view');
                    if (view) next.set('view', view);
                    const pageSize = prev.get('page_size');
                    if (pageSize) next.set('page_size', pageSize);
                    return next;
                }, { replace: true })}
                showClearAll={hasFilters}
                trailing={(
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground whitespace-nowrap">Cards per page</span>
                        <Select value={String(query.page_size)} onValueChange={(v) => setParam('page_size', v)}>
                            <SelectTrigger className="w-[70px] h-8" aria-label="Cards per page">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {[8, 20, 40, 100].map(size => (
                                    <SelectItem key={size} value={String(size)}>{size}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                )}
            >
                <SearchBar
                    value={query.q}
                    onSearchChange={(value) => setParam('q', value || undefined)}
                    placeholder="Search task title or description..."
                    ariaLabel="Search tasks"
                />
                <MultiSelectFilter
                    label="Status"
                    options={STATUS_OPTIONS.map(s => ({ label: s, value: s }))}
                    selected={query.status}
                    onChange={(next) => setParam('status', next.length ? next.join(',') : undefined)}
                />
                <MultiSelectFilter
                    label="Assignee"
                    options={members.map(m => ({ label: m.fullName, value: m.id }))}
                    selected={query.assignee}
                    onChange={(next) => setParam('assignee', next.length ? next.join(',') : undefined)}
                />
                <MultiSelectFilter
                    label="Priority"
                    options={PRIORITY_OPTIONS.map(s => ({ label: s, value: s }))}
                    selected={query.priority}
                    onChange={(next) => setParam('priority', next.length ? next.join(',') : undefined)}
                />
                <MultiSelectFilter
                    label="Labels"
                    options={labels.map(l => ({ label: l.name, value: String(l.id) }))}
                    selected={query.labels}
                    onChange={(next) => setParam('labels', next.length ? next.join(',') : undefined)}
                />
                <DateRangeFilter
                    label="Created"
                    from={query.created_from}
                    to={query.created_to}
                    onChange={(fromVal, toVal) => {
                        setSearchParams(prev => {
                            const next = new URLSearchParams(prev);
                            if (fromVal) next.set('created_from', fromVal); else next.delete('created_from');
                            if (toVal) next.set('created_to', toVal); else next.delete('created_to');
                            next.set('page', '1');
                            return next;
                        }, { replace: true });
                    }}
                />
                <SortControl
                    value={query.sort}
                    onChange={(value) => setParam('sort', value)}
                    options={[
                        { label: 'Created date', value: 'created_at' },
                        { label: 'Title', value: 'title' },
                        { label: 'Priority', value: 'priority' },
                        { label: 'Status', value: 'status' },
                    ]}
                />
            </FilterBar>

            {error && <div className="text-sm text-destructive mb-3">{error}</div>}
            {loading ? (
                <div className="text-sm text-muted-foreground py-8">Loading tasks...</div>
            ) : (
                <>
                    <TaskList tasks={tasks} onTaskClick={onTaskClick} onDelete={onDeleteTask} memberMap={memberMap} allTasks={tasks} />
                    <PaginationControls
                        count={count}
                        page={query.page}
                        pageSize={query.page_size}
                        onPageChange={(page) => setParam('page', String(page))}
                        onPageSizeChange={(size) => setParam('page_size', String(size))}
                        hideRowsSelector
                    />
                </>
            )}
        </div>
    );
}

// ─── Kanban View ────────────────────────────────────────────

function KanbanView({ selectedBoard, refreshKey = 0, filteredMemberId, memberMap, members, labels, currentBoard, onTaskClick, onTaskMove, onStopExecution, onDeleteTask }: {
    selectedBoard?: string;
    refreshKey?: number;
    filteredMemberId?: string | null;
    memberMap: Map<string, Member>;
    members: Member[];
    labels: Label[];
    currentBoard?: Board | null;
    onTaskClick: (task: Task) => void;
    onTaskMove: (taskId: string, move: { status: string; targetIndex?: number }) => Promise<boolean>;
    onStopExecution: (taskId: string, targetStatus: string) => Promise<boolean>;
    onDeleteTask?: (taskId: string) => void;
}) {
    const service = useService();
    const [searchParams, setSearchParams] = useSearchParams();
    const [tasks, setTasks] = useState<Task[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [pollingEnabled, setPollingEnabled] = useState(false);
    const [guideOpen, setGuideOpen] = useState(false);
    const defaultsApplied = useRef(false);
    const hasLoadedOnce = useRef(false);

    const filterAssignee = useMemo(() => splitParam(searchParams.get('assignee')), [searchParams]);
    const filterPriority = useMemo(() => splitParam(searchParams.get('priority')), [searchParams]);
    const filterLabels = useMemo(() => splitParam(searchParams.get('labels')), [searchParams]);
    const hasActiveFilters = filterAssignee.length > 0 || filterPriority.length > 0 || filterLabels.length > 0;

    const setParam = useCallback((key: string, value?: string) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (!value) next.delete(key); else next.set(key, value);
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    useEffect(() => {
        if (defaultsApplied.current) return;
        defaultsApplied.current = true;
        if (searchParams.has('created_from') || searchParams.has('created_to')) return;
        const today = new Date();
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            next.set('created_from', format(subDays(today, 29), 'yyyy-MM-dd'));
            next.set('created_to', format(today, 'yyyy-MM-dd'));
            return next;
        }, { replace: true });
    }, [searchParams, setSearchParams]);

    const dateFrom = searchParams.get('created_from') || undefined;
    const dateTo = searchParams.get('created_to') || undefined;

    const load = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
        if (!silent) setLoading(true);
        setError(null);
        try {
            const results = await service.fetchKanban(selectedBoard, {
                date_from: dateFrom,
                date_to: dateTo,
            });
            setTasks(prev => {
                const previousStatusById = new Map(prev.map(task => [task.id, task.currentStatus]));
                const transitionTs = Date.now();
                for (const task of results) {
                    const previousStatus = previousStatusById.get(task.id);
                    if (!previousStatus) continue;
                    if (didLeaveProgressStatus(previousStatus, task.currentStatus)) {
                        markExecutionTransitionUnseen(task.id, transitionTs);
                    }
                }
                return results;
            });
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load kanban');
        } finally {
            if (!silent) setLoading(false);
        }
    }, [service, selectedBoard, dateFrom, dateTo]);

    useEffect(() => {
        let active = true;
        const silent = hasLoadedOnce.current;
        void load({ silent }).finally(() => {
            if (active) {
                setPollingEnabled(true);
                hasLoadedOnce.current = true;
            }
        });
        return () => {
            active = false;
        };
    }, [load, refreshKey]);

    usePolling(() => load({ silent: true }), {
        enabled: pollingEnabled,
        intervalMs: Number(import.meta.env.VITE_POLL_INTERVAL_MS || 15000),
        immediate: false,
    });

    const handleTaskMove = useCallback(async (
        taskId: string,
        move: { status: string; targetIndex?: number }
    ) => {
        const ok = await onTaskMove(taskId, move);
        if (ok) {
            void load({ silent: true });
        }
        return ok;
    }, [onTaskMove, load]);

    const handleStopExecution = useCallback(async (taskId: string, targetStatus: string) => {
        const ok = await onStopExecution(taskId, targetStatus);
        if (ok) {
            void load({ silent: true });
        }
        return ok;
    }, [onStopExecution, load]);

    const visibleTasks = useMemo(() => {
        let result = filteredMemberId ? tasks.filter(t => t.assigneeIds.includes(filteredMemberId)) : tasks;
        if (filterAssignee.length > 0) result = result.filter(t => t.assigneeIds.some(id => filterAssignee.includes(id)));
        if (filterPriority.length > 0) result = result.filter(t => t.priority && filterPriority.includes(t.priority));
        if (filterLabels.length > 0) result = result.filter(t => t.labels?.some(l => filterLabels.includes(String(l.id))));
        return result;
    }, [tasks, filteredMemberId, filterAssignee, filterPriority, filterLabels]);

    const handleDateChange = useMemo(() => (from: string, to: string) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (from) next.set('created_from', from); else next.delete('created_from');
            if (to) next.set('created_to', to); else next.delete('created_to');
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    const isEmpty = !loading && visibleTasks.length === 0;
    const handleSearchSelect = useCallback((result: TaskSearchResult, scope: 'board' | 'global') => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (scope === 'global') {
                next.set('board', result.boardId);
            }
            next.set('taskId', result.taskId);
            return next;
        }, { replace: false });
    }, [setSearchParams]);

    return (
        <div>

            <div className="flex flex-wrap items-center gap-2 mb-4">
                <span className="text-xs text-muted-foreground tabular-nums shrink-0">
                    {!loading && (
                        <>{visibleTasks.length} {visibleTasks.length === 1 ? 'task' : 'tasks'}</>
                    )}
                </span>
                <KanbanTaskSearch
                    selectedBoard={selectedBoard}
                    onSelect={handleSearchSelect}
                    className="w-96"
                />
                <MultiSelectFilter
                    label="Assignee"
                    options={members.map(m => ({ label: m.fullName, value: m.id }))}
                    selected={filterAssignee}
                    onChange={(next) => setParam('assignee', next.length ? next.join(',') : undefined)}
                />
                <MultiSelectFilter
                    label="Priority"
                    options={PRIORITY_OPTIONS.map(s => ({ label: s, value: s }))}
                    selected={filterPriority}
                    onChange={(next) => setParam('priority', next.length ? next.join(',') : undefined)}
                />
                <MultiSelectFilter
                    label="Labels"
                    options={labels.map(l => ({ label: l.name, value: String(l.id) }))}
                    selected={filterLabels}
                    onChange={(next) => setParam('labels', next.length ? next.join(',') : undefined)}
                />
                <DateRangeFilter
                    label="Created"
                    from={dateFrom}
                    to={dateTo}
                    onChange={handleDateChange}
                />
                {hasActiveFilters && (
                    <Button size="sm" variant="ghost" className="text-xs text-muted-foreground h-7 px-2" onClick={() => {
                        setSearchParams(prev => {
                            const next = new URLSearchParams(prev);
                            next.delete('assignee');
                            next.delete('priority');
                            next.delete('labels');
                            return next;
                        }, { replace: true });
                    }}>
                        Clear filters
                    </Button>
                )}
                <div className="ml-auto flex items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => setGuideOpen(true)}>
                        <Plus className="size-3.5 mr-1" />
                        Spec
                    </Button>
                </div>
            </div>
            {error && <div className="text-sm text-destructive mb-3">{error}</div>}
            {loading ? (
                <div className="text-sm text-muted-foreground py-8">Loading kanban...</div>
            ) : isEmpty ? (
                <Card className="max-w-lg mx-auto mt-8">
                    <CardContent className="p-6">
                        <div className="flex items-center gap-2 mb-4">
                            <Terminal className="size-5 text-muted-foreground" />
                            <h3 className="text-base font-semibold">Get started with Odin</h3>
                        </div>
                        <p className="text-sm text-muted-foreground mb-4">
                            Create a spec from your terminal and tasks will appear here automatically.
                        </p>
                        <OdinGuideContent
                            workingDir={currentBoard?.workingDir}
                            needsInit={!!currentBoard && !currentBoard.odinInitialized}
                        />
                    </CardContent>
                </Card>
            ) : (
                <KanbanBoard
                    tasks={visibleTasks}
                    allTasks={tasks}
                    onTaskClick={onTaskClick}
                    onTaskMove={handleTaskMove}
                    onStopExecution={handleStopExecution}
                    onDeleteTask={onDeleteTask}
                    memberMap={memberMap}
                />
            )}

            <OdinGuideModal open={guideOpen} onOpenChange={setGuideOpen} board={currentBoard ?? null} />
        </div>
    );
}

// ─── Timeline View Wrapper ──────────────────────────────────

function TimelineViewWrapper({ selectedBoard, refreshKey = 0, members, onTaskClick, onDeleteTask }: {
    selectedBoard?: string;
    refreshKey?: number;
    members: Member[];
    onTaskClick: (task: Task) => void;
    onDeleteTask?: (taskId: string) => void;
}) {
    const service = useService();
    const [searchParams, setSearchParams] = useSearchParams();
    const [tasks, setTasks] = useState<Task[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const defaultsApplied = useRef(false);

    if (!defaultsApplied.current && !searchParams.has('created_from') && !searchParams.has('created_to')) {
        const today = new Date();
        searchParams.set('created_from', format(subDays(today, 29), 'yyyy-MM-dd'));
        searchParams.set('created_to', format(today, 'yyyy-MM-dd'));
        defaultsApplied.current = true;
        setSearchParams(searchParams, { replace: true });
    }
    defaultsApplied.current = true;

    const dateFrom = searchParams.get('created_from') || undefined;
    const dateTo = searchParams.get('created_to') || undefined;

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const resp = await service.fetchTimelinePage({
                board: selectedBoard,
                date_from: dateFrom,
                date_to: dateTo,
            });
            setTasks(resp.results);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load timeline');
        } finally {
            setLoading(false);
        }
    }, [service, selectedBoard, dateFrom, dateTo]);

    useEffect(() => { load(); }, [load, refreshKey]);

    const handleDateChange = useMemo(() => (from: string, to: string) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (from) next.set('created_from', from); else next.delete('created_from');
            if (to) next.set('created_to', to); else next.delete('created_to');
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    return (
        <div>
            <div className="flex flex-wrap items-center justify-end gap-2 mb-4">
                <span className="text-xs text-muted-foreground tabular-nums mr-auto">
                    {!loading && <>{tasks.length} {tasks.length === 1 ? 'task' : 'tasks'}</>}
                </span>
                <DateRangeFilter
                    label="Created"
                    from={dateFrom}
                    to={dateTo}
                    onChange={handleDateChange}
                />
            </div>

            {error && <div className="text-sm text-destructive mb-3">{error}</div>}
            {loading ? (
                <div className="text-sm text-muted-foreground py-8">Loading timeline...</div>
            ) : (
                <TimelineView tasks={tasks} allTasks={tasks} members={members} onTaskClick={onTaskClick} onDelete={onDeleteTask} />
            )}
        </div>
    );
}

// ─── Board Page ─────────────────────────────────────────────

export function BoardPage({
    selectedBoard,
    refreshKey = 0,
    filteredMemberId,
    memberMap,
    members,
    labels,
    currentBoard,
    dependencyTasks,
    dependencyTasksLoading,
    onEnsureDependencyTasks,
    onDependenciesSaved,
    onTaskClick,
    onTaskMove,
    onStopExecution,
    onDeleteTask,
}: BoardPageProps) {
    const [searchParams, setSearchParams] = useSearchParams();
    const rawView = searchParams.get('view');
    const view: BoardView = rawView === 'list' || rawView === 'timeline' || rawView === 'dependencies' ? rawView : 'kanban';

    const setView = useCallback((v: BoardView) => {
        setSearchParams(prev => {
            const next = new URLSearchParams();
            // Preserve board and taskId across view switches
            const board = prev.get('board');
            if (board) next.set('board', board);
            const taskId = prev.get('taskId');
            if (taskId) next.set('taskId', taskId);
            next.set('view', v);
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    return (
        <div>
            <div className="flex items-center gap-4 mb-5">
                <h2 className="text-lg font-semibold tracking-tight">Board</h2>
                <ViewToggle value={view} onChange={setView} />
            </div>

            {view === 'list' && (
                <ListView
                    selectedBoard={selectedBoard}
                    refreshKey={refreshKey}
                    memberMap={memberMap}
                    members={members}
                    labels={labels}
                    onTaskClick={onTaskClick}
                    onDeleteTask={onDeleteTask}
                />
            )}
            {view === 'kanban' && (
                <KanbanView
                    selectedBoard={selectedBoard}
                    refreshKey={refreshKey}
                    filteredMemberId={filteredMemberId}
                    memberMap={memberMap}
                    members={members}
                    labels={labels}
                    currentBoard={currentBoard}
                    onTaskClick={onTaskClick}
                    onTaskMove={onTaskMove}
                    onStopExecution={onStopExecution}
                    onDeleteTask={onDeleteTask}
                />
            )}
            {view === 'timeline' && (
                <TimelineViewWrapper
                    selectedBoard={selectedBoard}
                    refreshKey={refreshKey}
                    members={members}
                    onTaskClick={onTaskClick}
                    onDeleteTask={onDeleteTask}
                />
            )}
            {view === 'dependencies' && (
                <DependencyBoardView
                    boardId={selectedBoard}
                    allTasks={dependencyTasks || []}
                    loading={dependencyTasksLoading}
                    onEnsureTasks={onEnsureDependencyTasks}
                    onSaved={onDependenciesSaved}
                />
            )}
        </div>
    );
}
