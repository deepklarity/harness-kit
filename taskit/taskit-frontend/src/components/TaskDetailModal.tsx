import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { Task, Member, Label, TaskComment, TaskIdeOptions } from '../types';
import { collectDownstreamTaskIds } from '../utils/dagUtils';
import { classifyStatus, formatDate, formatDuration, getStatusColor, formatMergeStatus, formatBranchDisplay, CopyButton } from '../utils/transformer';
import { parseActor } from '../services/harness/HarnessTimeService';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { Switch } from '@/components/ui/switch';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { LabelPicker } from './LabelPicker';
import { MarkdownRenderer } from './MarkdownRenderer';
import { MarkdownEditor } from './MarkdownEditor';
import { TraceViewer } from './TraceViewer';
import {
    Pencil, Search, Trash2, Eye, Code, FileText, FolderOpen,
    GitBranch, Package, Terminal, User, ChevronRight, ChevronDown,
    HelpCircle, CornerDownRight, Send, ShieldCheck, Sparkles, Loader2,
    ZoomIn, ZoomOut, RotateCcw,
    Bot,
} from 'lucide-react';
import { useService } from '../contexts/ServiceContext';
import { useAuth } from '../contexts/AuthContext';
import { formatCost as formatCostDisplay } from '../utils/costEstimation';
import type { ReflectionReport } from '../types';
import { ReflectionModal } from './ReflectionModal';
import { ReflectionReportViewer } from './ReflectionReportViewer';
import { useToast } from '@/hooks/use-toast';
import { parseCommentBody } from '../utils/commentParser';
import { parseFailureDetails } from '../utils/failureParser';
import { IdeBadge, IdeSetupModal } from './IdeSetupModal';

interface TaskDetailModalProps {
    task: Task;
    onClose: () => void;
    allMembers: Member[];
    allTasks?: Task[];
    memberMap?: Map<string, Member>;
    onUpdateAssignees: (taskId: string, memberIds: string[], defaultModel?: string) => void;
    onUpdateTask: (taskId: string, updates: Record<string, unknown>) => Promise<void> | void;
    onSelectTask?: (taskId: string) => void;
    availableStatuses: string[];
    onDeleteTask?: (taskId: string) => void;
    availableLabels?: Label[];
    detailLoading?: boolean;
    onRefresh?: (taskId: string) => void;
}

const PRIORITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];
type BudgetUnit = 'seconds' | 'minutes' | 'hours' | 'days';

// unused LABEL_COLORS removed

const PRIORITY_COLORS: Record<string, string> = {
    CRITICAL: 'text-red-400',
    HIGH: 'text-orange-400',
    MEDIUM: 'text-blue-400',
    LOW: 'text-muted-foreground',
};

const BUDGET_UNIT_TO_HOURS: Record<BudgetUnit, number> = {
    seconds: 1 / 3600,
    minutes: 1 / 60,
    hours: 1,
    days: 24,
};

function formatBudgetValue(hours: number | undefined): string {
    if (hours === undefined) return '—';
    const totalSeconds = Math.max(0, Math.round(hours * 3600));
    return formatDuration(totalSeconds * 1000);
}

export function TaskDetailModal({
    task, onClose, allMembers, allTasks, memberMap, onUpdateAssignees, onUpdateTask, onSelectTask, availableStatuses, onDeleteTask, availableLabels, detailLoading, onRefresh
}: TaskDetailModalProps) {
    const service = useService();
    const { user: authUser } = useAuth();
    console.log('TaskDetailModal loaded, version 2 with zoomScale');
    const { toast } = useToast();
    const [searchParams] = useSearchParams();
    const isExecuting = task.currentStatus === 'EXECUTING';
    const [isEditingAssignees, setIsEditingAssignees] = useState(false);
    const [editingField, setEditingField] = useState<string | null>(null);
    const [editValue, setEditValue] = useState<string>('');
    const [budgetUnit, setBudgetUnit] = useState<BudgetUnit>('hours');
    const [selectedAssignee, setSelectedAssignee] = useState<string | null>(null);
    const [assigneeSearch, setAssigneeSearch] = useState('');
    const [showRawJson, setShowRawJson] = useState(false);
    const [showDependencyPicker, setShowDependencyPicker] = useState(false);
    const [dependencyDraft, setDependencyDraft] = useState<string[]>([]);
    const [savingDependencies, setSavingDependencies] = useState(false);
    const [ideOptions, setIdeOptions] = useState<TaskIdeOptions | null>(null);
    const [loadingIdeOptions, setLoadingIdeOptions] = useState(false);
    const [showIdeSetup, setShowIdeSetup] = useState(false);
    const [savingIde, setSavingIde] = useState(false);
    const [openingProject, setOpeningProject] = useState(false);
    const [openEditorDropdown, setOpenEditorDropdown] = useState(false);

    const [showAllHistory, setShowAllHistory] = useState(false);
    const [showAllComments, setShowAllComments] = useState(false);
    const [showDebugComments, setShowDebugComments] = useState(false);
    const [commentText, setCommentText] = useState('');
    const [isPostingComment, setIsPostingComment] = useState(false);

    const [replyingTo, setReplyingTo] = useState<string | null>(null);
    const [replyText, setReplyText] = useState('');
    const [isPostingReply, setIsPostingReply] = useState(false);
    const [isSummarizing, setIsSummarizing] = useState(false);
    const [summarizeError, setSummarizeError] = useState<string | null>(null);
    const [allLabels, setAllLabels] = useState<Label[]>([]);
    const [selectedLabels, setSelectedLabels] = useState<number[]>([]);
    const [budgetTickStart, setBudgetTickStart] = useState(() => Date.now());
    const [budgetTickNow, setBudgetTickNow] = useState(() => Date.now());

    useEffect(() => {
        setSelectedLabels(task.labels?.map(l => l.id) || []);
    }, [task.labels]);

    useEffect(() => {
        setBudgetTickStart(Date.now());
        setBudgetTickNow(Date.now());
    }, [task.id, task.executingTimeMs, task.remainingTimeMs, task.isTimerRunning]);

    useEffect(() => {
        if (!task.isTimerRunning) return;
        const interval = window.setInterval(() => setBudgetTickNow(Date.now()), 1000);
        return () => window.clearInterval(interval);
    }, [task.isTimerRunning]);

    useEffect(() => {
        if (!availableLabels?.length && task.boardId) {
            service.getLabels(task.boardId).then(setAllLabels).catch(() => {});
        } else if (availableLabels) {
            setAllLabels(availableLabels);
        }
    }, [task.boardId, availableLabels, service]);

    const handleLabelsChange = (newLabels: Label[]) => {
        setAllLabels(newLabels);
    };

    const handleSelectionChange = (newSelected: number[]) => {
        setSelectedLabels(newSelected);
        onUpdateTask(task.id, { labelIds: newSelected });
    };
    const [refImageLightbox, setRefImageLightbox] = useState<{ url: string; filename: string } | null>(null);

    // Reflection state
    const [showReflectionModal, setShowReflectionModal] = useState(false);
    const [reflections, setReflections] = useState<ReflectionReport[]>([]);

    // replaced

    // Fetch reflections on mount and when task changes
    useEffect(() => {
        const fetchReflections = async () => {
            try {
                const reports = await service.fetchReflections(task.id);
                setReflections(reports || []);
            } catch {
                // Silently fail — reflections are optional
            }
        };
        fetchReflections();
        // Poll for pending/running reflections
        const hasPending = reflections.some(r => r.status === 'PENDING' || r.status === 'RUNNING');
        if (hasPending) {
            const interval = setInterval(fetchReflections, 15000);
            return () => clearInterval(interval);
        }
    }, [task.id, service, reflections.some(r => r.status === 'PENDING' || r.status === 'RUNNING')]);

    useEffect(() => {
        let active = true;
        setLoadingIdeOptions(true);
        service.fetchTaskIdeOptions(task.id)
            .then(options => {
                if (!active) return;
                setIdeOptions(options);
            })
            .catch(() => {
                if (!active) return;
                setIdeOptions(null);
            })
            .finally(() => {
                if (!active) return;
                setLoadingIdeOptions(false);
            });
        return () => { active = false; };
    }, [task.id, service]);

    const isJson = useMemo(() => {
        if (!task.description) return false;
        const trimmed = task.description.trim();
        return (trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'));
    }, [task.description]);

    const descriptionData = useMemo(() => {
        if (!isJson || !task.description) return null;
        try {
            return JSON.parse(task.description);
        } catch {
            return null;
        }
    }, [task.description, isJson]);

    // Extract execution context from metadata
    const execContext = useMemo(() => {
        const md = task.metadata || {};
        return {
            model: task.modelName || (md.model ?? md.selected_model) as string | undefined,
            cwd: (md.working_dir ?? md.cwd) as string | undefined,
            worktreePath: md.worktree_path as string | undefined,
            harness: md.harness as string | undefined,
            branch: md.branch as string | undefined,
            mergeStatus: md.merge_status as string | undefined,
            mergeError: md.merge_error as string | undefined,
            diffStat: md.diff_stat as string | undefined,
        };
    }, [task.metadata, task.modelName]);

    const taskMap = useMemo(() => {
        const map = new Map<string, Task>();
        allTasks?.forEach(t => {
            map.set(t.id, t);
            if (t.idShort) map.set(String(t.idShort), t);
        });
        return map;
    }, [allTasks]);

    // Compute tasks that this task blocks
    const blockedTasks = useMemo(() => {
        return allTasks?.filter(t =>
            t.dependsOn?.includes(String(task.idShort)) || t.dependsOn?.includes(task.id)
        ) || [];
    }, [allTasks, task.idShort, task.id]);

    const boardTasks = useMemo(
        () => (allTasks || []).filter(t => t.boardId === task.boardId),
        [allTasks, task.boardId],
    );
    const downstreamTaskIds = useMemo(
        () => collectDownstreamTaskIds(boardTasks, task.id),
        [boardTasks, task.id],
    );
    const activeDependencyCandidates = useMemo(
        () => boardTasks.filter(t =>
            t.id !== task.id
            && !downstreamTaskIds.has(t.id)
            && (t.currentStatus === 'TODO' || t.currentStatus === 'IN_PROGRESS')
        ),
        [boardTasks, downstreamTaskIds, task.id],
    );
    const currentDependencyIds = useMemo(() => task.dependsOn || [], [task.dependsOn]);

    // Filter mutations for the activity feed
    const VISIBLE_FIELDS = new Set(['created', 'status', 'assignee_id']);
    const MAX_VISIBLE_MUTATIONS = 20;
    const visibleMutations = useMemo(() => {
        if (showAllHistory) return task.mutations;
        const keyEvents = task.mutations.filter(m => VISIBLE_FIELDS.has(m.fieldName || ''));
        return keyEvents.slice(-MAX_VISIBLE_MUTATIONS);
    }, [task.mutations, showAllHistory]);

    const hiddenCount = showAllHistory
        ? 0
        : task.mutations.length - visibleMutations.length;

    const handlePostComment = async () => {
        if (!commentText.trim() || isPostingComment) return;
        setIsPostingComment(true);
        try {
            await service.addComment(task.id, commentText.trim());
            setCommentText('');
            // Refresh detail so the new comment appears immediately
            onRefresh?.(task.id);
        } catch (e) {
            console.error('Failed to post comment:', e);
        } finally {
            setIsPostingComment(false);
        }
    };

    const handlePostReply = async () => {
        if (!replyText.trim() || isPostingReply || !replyingTo) return;
        setIsPostingReply(true);
        try {
            await service.replyToQuestion(task.id, replyingTo, replyText.trim());
            setReplyText('');
            setReplyingTo(null);
            // Refresh detail so the reply appears immediately
            onRefresh?.(task.id);
        } catch (e) {
            console.error('Failed to post reply:', e);
        } finally {
            setIsPostingReply(false);
        }
    };

    const handleSummarize = async () => {
        setIsSummarizing(true);
        setSummarizeError(null);

        const initialCommentCount = task.comments?.length ?? 0;

        try {
            await (service as any).summarizeTask(task.id);
        } catch {
            setSummarizeError('Failed to dispatch summarize.');
            setIsSummarizing(false);
            return;
        }

        // Poll for the summary comment to appear
        const pollInterval = 3000;
        const maxWait = 60000;
        const startTime = Date.now();

        const poll = setInterval(async () => {
            try {
                const detail = await service.fetchTaskDetail(task.id);
                const newComments = detail.comments ?? [];
                const hasSummary = newComments.length > initialCommentCount &&
                    newComments.some((c: TaskComment) => c.commentType === 'summary' &&
                        !task.comments?.some((old: TaskComment) => old.id === c.id));
                const flagCleared = !(detail.metadata as Record<string, unknown>)?.summarize_in_progress;

                if (hasSummary || (flagCleared && Date.now() - startTime > pollInterval * 2)) {
                    clearInterval(poll);
                    setIsSummarizing(false);
                    onRefresh?.(task.id);
                }
            } catch {
                // Silently retry on poll failure
            }

            if (Date.now() - startTime > maxWait) {
                clearInterval(poll);
                setIsSummarizing(false);
                setSummarizeError('Summarize timed out. Check odin logs.');
                onRefresh?.(task.id);
            }
        }, pollInterval);
    };

    // Build a map of question_id -> reply comment for inline display
    const replyMap = useMemo(() => {
        const map = new Map<string, TaskComment>();
        for (const c of task.comments) {
            if (c.commentType === 'reply' || (c.attachments as Array<Record<string, unknown>>)?.some(a => a?.type === 'reply')) {
                const replyTo = (c.attachments as Array<Record<string, unknown>>)?.find(a => a?.type === 'reply');
                if (replyTo?.reply_to) {
                    map.set(String(replyTo.reply_to), c);
                }
            }
        }
        return map;
    }, [task.comments]);

    // Execution metrics from task.metadata
    const execMetrics = useMemo(() => {
        const md = task.metadata || {};
        return {
            summary: md.last_execution_summary as string | undefined,
            success: md.last_execution_success as boolean | undefined,
            agent: md.last_execution_agent as string | undefined,
            durationMs: md.last_duration_ms as number | undefined,
            usage: task.usage || md.last_usage as { total_tokens?: number; input_tokens?: number; output_tokens?: number } | undefined,
        };
    }, [task.metadata, task.usage]);

    // Get assignee's available models for the model selector
    const assigneeModels = useMemo(() => {
        const assigneeId = task.assigneeIds?.[0];
        if (!assigneeId) return [];
        const assignee = allMembers.find(m => m.id === assigneeId);
        return assignee?.availableModels || [];
    }, [task.assigneeIds, allMembers]);

    // Cost comes from the backend — single source of truth
    const estimatedCost = task.estimatedCostUsd ?? null;
    const preferredIde = ideOptions?.detected_ides.find(ide => ide.id === ideOptions.preferred_ide_id) || null;
    const routingReasoning = typeof task.metadata?.routing_reasoning === 'string' ? task.metadata.routing_reasoning : null;

    const hasExecContext = !!(execContext.cwd || execContext.harness || execContext.branch || task.complexity || task.dependsOn?.length || task.currentStatus === 'TODO');

    const canReflect = ['REVIEW', 'DONE', 'FAILED'].includes(task.currentStatus);

    const handleTriggerReflection = async (params: import('../types').ReflectionRequest) => {
        const payload = { ...params, requested_by: authUser?.email || undefined };
        await service.triggerReflection(task.id, payload);
        // Refresh reflections after triggering
        const reports = await service.fetchReflections(task.id);
        setReflections(reports || []);
    };

    const handleCancelReflection = async (reportId: number) => {
        try {
            await service.cancelReflection(reportId);
            const reports = await service.fetchReflections(task.id);
            setReflections(reports || []);
        } catch {
            // ignore
        }
    };

    const handleDeleteReflection = async (reportId: number) => {
        try {
            await service.deleteReflection(reportId);
            const reports = await service.fetchReflections(task.id);
            setReflections(reports || []);
        } catch {
            // ignore
        }
    };

    const refreshIdeOptions = async () => {
        const options = await service.fetchTaskIdeOptions(task.id);
        setIdeOptions(options);
        return options;
    };

    const handleSaveIde = async (ideId: string) => {
        setSavingIde(true);
        try {
            await service.saveIdeSettings(ideId);
            await refreshIdeOptions();
            setShowIdeSetup(false);
            toast({ title: 'IDE saved', description: 'Open Project will use this IDE for this board root.' });
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

    const handleOpenProject = async () => {
        if (!ideOptions?.preferred_ide_id || !preferredIde) {
            setShowIdeSetup(true);
            return;
        }
        setOpeningProject(true);
        try {
            await service.openTaskProject(task.id);
            toast({ title: 'Project opened', description: `Opened ${ideOptions.project_root || 'project root'} in ${preferredIde.label}.` });
        } catch (e) {
            const body = (e as { body?: { code?: string; detail?: string } })?.body;
            const code = body?.code;
            if (code === 'no_preferred_ide' || code === 'preferred_ide_not_detected') {
                setShowIdeSetup(true);
            } else {
                toast({
                    title: 'Failed to open project',
                    description: body?.detail || (e instanceof Error ? e.message : 'Unknown error'),
                    variant: 'destructive',
                });
            }
            try {
                await refreshIdeOptions();
            } catch {
                // Ignore refresh failures after open error
            }
        } finally {
            setOpeningProject(false);
        }
    };

    const handleOpenWorktree = () => {
        const path = execContext.worktreePath || ideOptions?.project_root;
        if (!path) return;
        if (!ideOptions?.preferred_ide_id || !preferredIde) {
            setShowIdeSetup(true);
            return;
        }
        const uri = `${ideOptions.preferred_ide_id}://file${path}`;
        const link = document.createElement('a');
        link.href = uri;
        link.click();
        toast({ title: 'Editor opened', description: `Opened ${path} in ${preferredIde.label}.` });
    };

    const startEditingAssignees = () => {
        if (isExecuting) {
            toast({ title: 'Task is executing', description: 'Stop the task before changing assignee.' });
            return;
        }
        setSelectedAssignee(task.assigneeIds?.[0] || null);
        setIsEditingAssignees(true);
    };

    const handleSaveAssignees = () => {
        if (isExecuting) {
            toast({ title: 'Task is executing', description: 'Stop the task before changing assignee.' });
            return;
        }
        // Determine the new agent's default model so both updates happen atomically
        let defaultModelName: string | undefined;
        if (selectedAssignee) {
            const newAssignee = allMembers.find(m => m.id === selectedAssignee);
            const models = newAssignee?.availableModels || [];
            if (models.length > 0) {
                const defaultModel = models.find(m => m.is_default) || models[0];
                defaultModelName = defaultModel.name;
            }
        }
        onUpdateAssignees(task.id, selectedAssignee ? [selectedAssignee] : [], defaultModelName);
        setIsEditingAssignees(false);
    };

    const startEditingField = (field: string, value: string) => {
        if (isExecuting && field === 'status') {
            toast({ title: 'Task is executing', description: 'Stop the task before changing status.' });
            return;
        }
        setEditingField(field);
        setEditValue(value || '');
    };

    const startEditingBudget = () => {
        setBudgetUnit('hours');
        setEditingField('devEta');
        setEditValue(task.devEta !== undefined ? String(task.devEta) : '');
    };

    const handleSaveField = () => {
        if (!editingField) return;
        const updateKey = editingField === 'title' ? 'title' :
            editingField === 'description' ? 'description' :
                editingField === 'status' ? 'status' :
                    editingField === 'priority' ? 'priority' :
                        editingField === 'devEta' ? 'devEta' : null;
        if (updateKey) {
            const finalValue = updateKey === 'devEta'
                ? Number(editValue) * BUDGET_UNIT_TO_HOURS[budgetUnit]
                : editValue;
            onUpdateTask(task.id, { [updateKey]: finalValue });
        }
        setEditingField(null);
    };

    const openDependencyPicker = () => {
        setDependencyDraft(currentDependencyIds);
        setShowDependencyPicker(true);
    };

    const handleSaveDependencies = async () => {
        setSavingDependencies(true);
        try {
            await onUpdateTask(task.id, { dependsOn: dependencyDraft });
            setShowDependencyPicker(false);
            await onRefresh?.(task.id);
        } finally {
            setSavingDependencies(false);
        }
    };

    const filteredMembers = allMembers.filter(m =>
        m.fullName.toLowerCase().includes(assigneeSearch.toLowerCase()) ||
        m.username.toLowerCase().includes(assigneeSearch.toLowerCase())
    );

    const statusCategory = classifyStatus(task.currentStatus);
    const showTimeBudget = ['backlog', 'todo', 'doing', 'review', 'testing', 'failed', 'done'].includes(statusCategory);
    const canEditTimeBudget = statusCategory !== 'done';
    const budgetMs = task.devEta !== undefined ? task.devEta * 3600 * 1000 : undefined;
    const liveTimerDeltaMs = task.isTimerRunning ? Math.max(0, budgetTickNow - budgetTickStart) : 0;
    const usedExecutionMs = useMemo(() => {
        if (task.isTimerRunning && budgetMs !== undefined && task.remainingTimeMs !== undefined) {
            return Math.max(0, budgetMs - (task.remainingTimeMs - liveTimerDeltaMs));
        }
        return Math.max(0, task.executingTimeMs);
    }, [budgetMs, liveTimerDeltaMs, task.executingTimeMs, task.isTimerRunning, task.remainingTimeMs]);
    const isOverBudget = budgetMs !== undefined && usedExecutionMs > budgetMs;
    const canSaveBudget = editValue.trim() !== '' && Number.isFinite(Number(editValue)) && Number(editValue) >= 0;
    const statusColor = getStatusColor(task.currentStatus);
    const statusEntries = Object.entries(task.timeInStatuses);
    const totalStatusTime = statusEntries.reduce((sum, [, ms]) => sum + ms, 0);

    return (
        <Dialog open onOpenChange={onClose}>
            <DialogContent className="sm:max-w-[90vw] w-[90vw] h-[90vh] flex flex-col p-0 overflow-hidden gap-0">
                {/* FIXED HEADER */}
                <DialogHeader className="shrink-0 px-6 pt-6 pb-2">
                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-3">
                        <span className="font-semibold font-mono">{task.boardName}</span>
                        {task.specName && (
                            <>
                                <span className="opacity-40">/</span>
                                <span className="flex items-center gap-1">
                                    <FileText className="size-3" />
                                    {task.specName}
                                </span>
                            </>
                        )}
                        <span className="opacity-40">/</span>
                        <span className="font-mono">#{task.idShort}</span>
                        <CopyButton text={String(task.idShort)} />
                        <div className="flex-1" />
                    </div>
                    {(ideOptions?.project_root || execContext.worktreePath) && (
                        <div className="mb-3 flex items-center">
                            <div className="flex">
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className={`h-8 gap-2 text-xs font-medium ${ideOptions?.project_root && execContext.worktreePath ? 'rounded-r-none border-r-0' : ''}`}
                                    onClick={handleOpenWorktree}
                                    disabled={openingProject || loadingIdeOptions}
                                >
                                    <FolderOpen className="size-3.5" />
                                    {preferredIde ? `Open in ${preferredIde.label}` : 'Open in Editor'}
                                </Button>
                                {ideOptions?.project_root && execContext.worktreePath && (
                                    <Popover open={openEditorDropdown} onOpenChange={setOpenEditorDropdown}>
                                        <PopoverTrigger asChild>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                className="h-8 px-1.5 rounded-l-none"
                                                disabled={openingProject || loadingIdeOptions}
                                            >
                                                <ChevronDown className="size-3.5" />
                                            </Button>
                                        </PopoverTrigger>
                                        <PopoverContent className="w-auto p-1" align="start">
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-8 gap-2 text-xs font-medium w-full justify-start"
                                                onClick={() => {
                                                    setOpenEditorDropdown(false);
                                                    handleOpenProject();
                                                }}
                                            >
                                                <FolderOpen className="size-3.5" />
                                                Open project root
                                            </Button>
                                        </PopoverContent>
                                    </Popover>
                                )}
                            </div>
                        </div>
                    )}
                    {editingField === 'title' ? (
                        <div className="flex gap-2">
                            <Input className="flex-1 text-xl font-bold" value={editValue}
                                onChange={e => setEditValue(e.target.value)} autoFocus />
                            <Button size="sm" onClick={handleSaveField}>Save</Button>
                            <Button size="sm" variant="outline" onClick={() => setEditingField(null)}>Cancel</Button>
                        </div>
                    ) : (
                        <DialogTitle className="flex items-center gap-2 text-2xl font-bold tracking-tight">
                            {task.title || task.name}
                            <button className="opacity-50 hover:opacity-100 transition-opacity" onClick={() => startEditingField('title', task.title || task.name)}>
                                <Pencil className="size-4" />
                            </button>
                            {(task.skipReflection || task.boardSkipReflection) && (
                                <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 border border-amber-200 tracking-normal">
                                    {task.boardSkipReflection && !task.skipReflection ? 'Reflection disabled (board)' : 'Reflection disabled'}
                                </span>
                            )}
                        </DialogTitle>
                    )}
                </DialogHeader>

                {/* SCROLLABLE BODY — flexbox so min-h-0 properly constrains each column */}
                <div className="flex-1 min-h-0 flex">

                    {/* LEFT COLUMN: Metadata — compact layout */}
                    <div className="w-[320px] shrink-0 border-r border-border/50 flex flex-col">
                        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-0">
                            {/* Status + Priority — side by side */}
                            <div className="flex gap-3 pb-2.5 border-b border-border/30">
                                <div className="flex-1">
                                    <span className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold">Status</span>
                                    {editingField === 'status' ? (
                                        <div className="flex gap-1.5 mt-1">
                                            <Select value={editValue} onValueChange={setEditValue}>
                                                <SelectTrigger className="flex-1 h-7 text-xs"><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {availableStatuses.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                            <Button size="sm" className="h-7 px-2 text-xs" onClick={handleSaveField}>OK</Button>
                                        </div>
                                    ) : (
                                        <Badge className={`gap-1.5 mt-1 px-2 py-0.5 text-xs font-medium border ${isExecuting ? 'cursor-not-allowed opacity-80' : 'cursor-pointer'}`} style={{ background: `${statusColor}15`, color: statusColor, borderColor: `${statusColor}30` }}
                                            onClick={() => startEditingField('status', task.currentStatus)}>
                                            <span className="size-1.5 rounded-full" style={{ background: statusColor }} />
                                            {task.currentStatus}
                                        </Badge>
                                    )}
                                </div>
                                <div className="flex-1">
                                    <span className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold">Priority</span>
                                    {editingField === 'priority' ? (
                                        <div className="flex gap-1.5 mt-1">
                                            <Select value={editValue} onValueChange={setEditValue}>
                                                <SelectTrigger className="flex-1 h-7 text-xs"><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {PRIORITIES.map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                            <Button size="sm" className="h-7 px-2 text-xs" onClick={handleSaveField}>OK</Button>
                                        </div>
                                    ) : (
                                        <div className={`cursor-pointer flex items-center gap-1 mt-1 text-sm font-medium ${PRIORITY_COLORS[task.priority || 'MEDIUM'] || 'text-muted-foreground'}`}
                                            onClick={() => startEditingField('priority', task.priority || 'MEDIUM')}>
                                            {task.priority || 'MEDIUM'}
                                            <Pencil className="size-2.5 opacity-40" />
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Failure Reason — prominent banner for FAILED tasks */}
                            {task.currentStatus === 'FAILED' && (
                                <div className="rounded-md border border-red-500/20 bg-red-500/5 px-2.5 py-1.5 mb-1">
                                    <div className="text-[10px] font-semibold text-red-400 uppercase tracking-wider mb-0.5">Failure</div>
                                    <div className="flex items-start gap-1.5">
                                        <div className="text-xs text-foreground/80 font-mono break-all leading-snug flex-1">
                                            {(task.metadata as Record<string, unknown>)?.last_failure_reason as string || 'Unknown error — check task comments for details'}
                                        </div>
                                        <CopyButton text={(task.metadata as Record<string, unknown>)?.last_failure_reason as string || 'Unknown error — check task comments for details'} />
                                    </div>
                                    {(() => {
                                        const metadata = task.metadata as Record<string, unknown> | undefined;
                                        const lastFailureOrigin = metadata?.last_failure_origin;
                                        return (lastFailureOrigin ? (
                                            <div className="text-[10px] text-muted-foreground/60 mt-0.5">
                                                Origin: {String(lastFailureOrigin)}
                                            </div>
                                        ) : null) as React.ReactNode;
                                    })()}
                                    {(() => {
                                        const metadata = task.metadata as Record<string, unknown> | undefined;
                                        const lastFailureType = metadata?.last_failure_type;
                                        return (lastFailureType ? (
                                            <Badge variant="outline" className="text-[9px] px-1 py-0 mt-1 bg-red-500/10 text-red-400 border-red-500/20">
                                                {String(lastFailureType)}
                                            </Badge>
                                        ) : null) as React.ReactNode;
                                    })()}
                                    {(() => {
                                        const metadata = task.metadata as Record<string, unknown> | undefined;
                                        const failureDebug = metadata?.failure_debug;
                                        return (failureDebug ? (
                                            <div className="mt-1 max-h-[120px] overflow-y-auto text-[10px] font-mono text-muted-foreground/70 bg-red-500/5 rounded p-1.5 border border-red-500/10 whitespace-pre-wrap break-all">
                                                {String(failureDebug)}
                                            </div>
                                        ) : null) as React.ReactNode;
                                    })()}
                                </div>
                            )}


                            {/* Pending Question Banner */}
                            {!!task.metadata?.has_pending_question && (
                                <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-amber-500/10 border border-amber-500/20 mb-1">
                                    <HelpCircle className="size-3.5 text-amber-500 animate-pulse shrink-0" />
                                    <span className="text-[11px] font-medium text-amber-600">Agent waiting for reply</span>
                                </div>
                            )}
                            {isExecuting && (
                                <div className="px-2 py-1.5 rounded-md bg-blue-500/10 border border-blue-500/20 mb-1">
                                    <div className="flex items-center gap-2">
                                        <Terminal className="size-3.5 text-blue-500 shrink-0" />
                                        <span className="text-[11px] font-medium text-blue-600">Executing</span>
                                        <ExecutingTimer task={task} />
                                    </div>
                                </div>
                            )}

                            {/* Assignee */}
                            <CompactRow label="Assignee">
                                {!isEditingAssignees ? (
                                    <div className="flex items-center gap-1.5 flex-wrap">
                                        {task.assignees.length > 0
                                            ? task.assignees.map((name, i) => {
                                                const member = memberMap?.get(task.assigneeIds[i]) ?? allMembers.find(m => m.id === task.assigneeIds[i]);
                                                const isAgent = member?.role === 'AGENT' || member?.email.endsWith('@odin.agent');
                                                return (
                                                    <div key={i} className="flex items-center gap-1.5 bg-secondary/60 rounded px-1.5 py-0.5">
                                                        <div className="relative shrink-0">
                                                            <div className="size-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white font-mono"
                                                                style={{ background: member?.color || 'hsl(240, 60%, 50%)' }}>
                                                                {member?.initials || name.substring(0, 2).toUpperCase()}
                                                            </div>
                                                            {isAgent && (
                                                                <div className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full bg-indigo-600 border border-background flex items-center justify-center">
                                                                    <Bot className="size-1 text-white" />
                                                                </div>
                                                            )}
                                                        </div>
                                                        <span className="text-xs font-medium">{member?.fullName || name}</span>
                                                    </div>
                                                );
                                            })
                                            : <span className="text-xs text-muted-foreground italic flex items-center gap-1"><User className="size-3 opacity-50" />Unassigned</span>
                                        }
                                        <button className={`transition-opacity ${isExecuting ? 'opacity-30 cursor-not-allowed' : 'opacity-40 hover:opacity-100'}`} onClick={startEditingAssignees}>
                                            <Pencil className="size-3" />
                                        </button>
                                    </div>
                                ) : (
                                    <div>
                                        <div className="bg-secondary/50 p-2.5 rounded-lg border border-border shadow-inner">
                                            <div className="relative mb-2">
                                                <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground" />
                                                <Input placeholder="Search..." value={assigneeSearch}
                                                    onChange={e => setAssigneeSearch(e.target.value)} autoFocus className="pl-7 h-7 text-xs bg-background" />
                                            </div>
                                            <div className="max-h-[120px] overflow-y-auto">
                                                {(() => {
                                                    const isAgent = (m: Member) => m.role === 'AGENT' || m.email.endsWith('@odin.agent');
                                                    const humans = filteredMembers.filter(m => !isAgent(m));
                                                    const agents = filteredMembers.filter(m => isAgent(m));

                                                    const renderItem = (member: Member) => {
                                                        const isSelected = selectedAssignee === member.id;
                                                        const isA = isAgent(member);
                                                        return (
                                                            <div key={member.id}
                                                                className={`flex items-center gap-2 p-1.5 rounded cursor-pointer transition-colors ${isSelected ? 'bg-primary/15' : 'hover:bg-background/80'}`}
                                                                onClick={() => setSelectedAssignee(isSelected ? null : member.id)}>
                                                                <div className={`size-4 shrink-0 rounded-full border-2 flex items-center justify-center ${isSelected ? 'border-primary bg-primary' : 'border-muted-foreground/30'}`}>
                                                                    {isSelected && <div className="size-1.5 rounded-full bg-white" />}
                                                                </div>
                                                                <div className="relative shrink-0">
                                                                    <div className="size-4 rounded-full flex items-center justify-center text-[8px] font-bold text-white font-mono"
                                                                        style={{ background: member.color }}>{member.initials}</div>
                                                                    {isA && (
                                                                        <div className="absolute -right-0.5 -bottom-0.5 size-1.5 rounded-full bg-indigo-600 border border-background flex items-center justify-center">
                                                                            <Bot className="size-[4px] text-white" />
                                                                        </div>
                                                                    )}
                                                                </div>
                                                                <span className="text-xs font-medium truncate flex-1">{member.fullName}</span>
                                                                {isA && <Badge variant="secondary" className="text-[9px] px-1 py-0 bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-400">Agent</Badge>}
                                                            </div>
                                                        );
                                                    };

                                                    return (
                                                    <div className="space-y-1">
                                                        {humans.length > 0 && (
                                                            <div>
                                                                <div className="text-[9px] font-bold text-muted-foreground px-1.5 py-0.5 uppercase tracking-wider">Team</div>
                                                                {humans.map(renderItem)}
                                                            </div>
                                                        )}
                                                        {agents.length > 0 && (
                                                            <div>
                                                                <div className="text-[9px] font-bold text-muted-foreground px-1.5 py-0.5 uppercase tracking-wider">Agents</div>
                                                                {agents.map(renderItem)}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })()}
                                        </div>
                                    </div>
                                    <div className="flex gap-2 justify-end mt-1.5">
                                        <Button size="sm" variant="ghost" onClick={() => setIsEditingAssignees(false)} className="h-6 text-[10px] px-2">Cancel</Button>
                                        <Button size="sm" onClick={handleSaveAssignees} className="h-6 text-[10px] px-2">Save</Button>
                                    </div>
                                </div>
                            )}
                            </CompactRow>

                            {/* Model — directly below assignee so changes are visible */}
                            {(assigneeModels.length > 0 || execContext.model) && (
                                <CompactRow label="Model">
                                    {assigneeModels.length > 0 ? (
                                        <Select
                                            value={execContext.model || ''}
                                            onValueChange={(value) => {
                                                if (isExecuting) {
                                                    toast({ title: 'Task is executing', description: 'Stop the task before changing model.' });
                                                    return;
                                                }
                                                onUpdateTask(task.id, { modelName: value });
                                            }}
                                            disabled={isExecuting}
                                        >
                                            <SelectTrigger className="h-6 text-[10px] font-mono w-full !whitespace-nowrap overflow-hidden">
                                                <SelectValue placeholder="Select model..." />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {assigneeModels.map(m => (
                                                    <SelectItem key={m.name} value={m.name}>
                                                        <div>
                                                            <span className="font-mono text-[11px]">{m.name}</span>
                                                            {m.description && (
                                                                <span className="block text-[9px] text-muted-foreground leading-tight">{m.description}</span>
                                                            )}
                                                        </div>
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    ) : (
                                        <span className="text-[11px] font-mono font-medium text-primary/80 truncate">{execContext.model}</span>
                                    )}
                                </CompactRow>
                            )}

                            {showTimeBudget && (
                                <CompactRow label="Time Budget">
                                    {editingField === 'devEta' && canEditTimeBudget ? (
                                        <div className="flex flex-wrap items-center gap-2">
                                            <Input
                                                type="number"
                                                min="0"
                                                step={budgetUnit === 'hours' || budgetUnit === 'days' ? '0.5' : '1'}
                                                value={editValue}
                                                onChange={e => setEditValue(e.target.value)}
                                                autoFocus
                                                className="h-8 w-24 text-xs [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                                            />
                                            <Select value={budgetUnit} onValueChange={(value) => setBudgetUnit(value as BudgetUnit)}>
                                                <SelectTrigger className="h-8 w-28 text-xs">
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="seconds">Seconds</SelectItem>
                                                    <SelectItem value="minutes">Minutes</SelectItem>
                                                    <SelectItem value="hours">Hours</SelectItem>
                                                    <SelectItem value="days">Days</SelectItem>
                                                </SelectContent>
                                            </Select>
                                            <Button
                                                size="sm"
                                                className="h-7 px-2.5 text-[11px]"
                                                onClick={handleSaveField}
                                                disabled={!canSaveBudget}
                                            >
                                                Save
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="h-7 px-2.5 text-[11px]"
                                                onClick={() => setEditingField(null)}
                                            >
                                                Cancel
                                            </Button>
                                        </div>
                                    ) : (
                                        <div className="flex items-start justify-between gap-4">
                                            <div className="space-y-1 text-xs">
                                                <div className="flex items-center gap-2">
                                                    <span className="font-medium text-foreground">Budget:</span>
                                                    <span className="font-mono text-foreground/90">{formatBudgetValue(task.devEta)}</span>
                                                </div>
                                                <div className={`inline-flex items-center gap-2 rounded-md px-2 py-1 ${isOverBudget ? 'bg-red-500/10 text-red-500 dark:text-red-300' : 'text-foreground'}`}>
                                                    <span className="font-medium">Used:</span>
                                                    <span className="font-mono">{formatDuration(usedExecutionMs)}</span>
                                                </div>
                                            </div>
                                            {canEditTimeBudget && (
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    className="h-7 px-2 text-xs"
                                                    onClick={startEditingBudget}
                                                >
                                                    Edit
                                                </Button>
                                            )}
                                        </div>
                                    )}
                                </CompactRow>
                            )}

                            {/* Spec Link */}
                            {task.specId && (
                                <CompactRow label="Spec">
                                    <a href={`/specs/${task.specId}${searchParams.get('board') ? `?board=${searchParams.get('board')}` : ''}`} className="flex items-center gap-1.5 text-xs text-primary hover:underline font-medium truncate">
                                        <FileText className="size-3 shrink-0" />
                                        {task.specName || task.specId.substring(0, 8)}
                                    </a>
                                </CompactRow>
                            )}

                            {/* Labels */}
                            <CompactRow label="Labels">
                                <LabelPicker
                                    boardId={task.boardId}
                                    selectedLabelIds={selectedLabels}
                                    allLabels={allLabels}
                                    onLabelsChange={handleLabelsChange}
                                    onSelectionChange={handleSelectionChange}
                                    readonly={isExecuting}
                                />
                            </CompactRow>

                            {/* Created — inline */}
                            <CompactRow label="Created">
                                <span className="text-xs font-mono text-muted-foreground">{task.createdAt ? formatDate(task.createdAt) : '\u2014'}</span>
                            </CompactRow>

                            {/* Branch & Merge — always visible */}
                            <CompactRow label="Branch">
                                <div className="flex items-center gap-1.5">
                                    <span className="text-xs font-mono truncate" title={execContext.branch || undefined}>{formatBranchDisplay(execContext.branch)}</span>
                                    {execContext.branch && <CopyButton text={execContext.branch} />}
                                    <span
                                        className={`text-[9px] font-semibold px-1 py-0.5 rounded shrink-0 ${
                                            execContext.mergeStatus === 'merged' ? 'bg-emerald-500/20 text-emerald-400' :
                                            execContext.mergeStatus === 'noop' ? 'bg-amber-500/20 text-amber-400' :
                                            execContext.mergeStatus === 'conflict' ? 'bg-red-500/20 text-red-400' :
                                            execContext.mergeStatus === 'error' ? 'bg-red-500/20 text-red-400' :
                                            execContext.mergeStatus ? 'bg-yellow-500/20 text-yellow-400' :
                                            'bg-muted/30 text-muted-foreground/50'
                                        }`}
                                        title={execContext.mergeError || undefined}
                                    >
                                        {formatMergeStatus(execContext.mergeStatus)}
                                    </span>
                                </div>
                            </CompactRow>
                            {execContext.worktreePath && (
                                <CompactRow label="Worktree">
                                    <div className="flex items-center gap-1.5">
                                        <span className="text-xs font-mono truncate" title={execContext.worktreePath}>{execContext.worktreePath}</span>
                                        <CopyButton text={execContext.worktreePath} />
                                    </div>
                                </CompactRow>
                            )}

                            {/* Diff stat — files changed by this task */}
                            {execContext.diffStat && (
                                <CompactRow label="Changes">
                                    <pre className="text-[10px] font-mono text-muted-foreground whitespace-pre-wrap overflow-hidden max-w-full">{
                                        execContext.diffStat.replace(/[+-]{21,}/g, m => {
                                            const plus = (m.match(/\+/g) || []).length;
                                            const minus = (m.match(/-/g) || []).length;
                                            const total = plus + minus;
                                            const max = 20;
                                            const p = Math.round((plus / total) * max);
                                            const mn = max - p;
                                            return '+'.repeat(p) + '-'.repeat(mn);
                                        })
                                    }</pre>
                                </CompactRow>
                            )}

                            {task.scheduleSummary ? (
                                <div className="py-2 border-b border-border/30">
                                    <span className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground/70 mb-1 block">Scheduling</span>
                                    <CompactRow label="Mode" noBorder>
                                        <span className="text-xs font-mono">{task.scheduleSummary.kind}</span>
                                    </CompactRow>
                                    <CompactRow label="Status" noBorder>
                                        <span className="text-xs font-mono">{task.scheduleSummary.status}</span>
                                    </CompactRow>
                                    <CompactRow label="Timezone" noBorder>
                                        <span className="text-xs font-mono">{task.scheduleSummary.timezone}</span>
                                    </CompactRow>
                                    <CompactRow label="Next Run" noBorder>
                                        <span className="text-xs font-mono">{task.scheduleSummary.next_run_at_utc ? formatDate(task.scheduleSummary.next_run_at_utc) : '\u2014'}</span>
                                    </CompactRow>
                                    {task.scheduleRuns && task.scheduleRuns.length > 0 ? (
                                        <div className="pt-2 border-t border-border/30">
                                            <div className="text-[10px] uppercase tracking-wider text-muted-foreground/70 mb-1">Recent Schedule Runs</div>
                                            <div className="space-y-1">
                                                {task.scheduleRuns.slice(0, 4).map(run => (
                                                    <div key={run.id} className="flex items-center justify-between gap-3">
                                                        <span className="text-muted-foreground text-xs">#{run.run_number}</span>
                                                        <span className="font-mono text-xs">{run.status}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    ) : null}
                                </div>
                            ) : null}

                            {/* Cost & Tokens — show for any executed task, with placeholders when data is missing */}
                            {(execMetrics.usage?.total_tokens || execMetrics.durationMs || ['REVIEW', 'DONE', 'FAILED', 'TESTING'].includes(task.currentStatus)) && (
                                <div className="py-2 border-b border-border/30">
                                    <div className="flex items-center gap-3">
                                        <div>
                                            <span className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold block">Est. Cost</span>
                                            <span className={`text-sm font-semibold font-mono ${estimatedCost !== null ? 'text-emerald-400' : 'text-muted-foreground/50'}`}>
                                                {formatCostDisplay(estimatedCost)}
                                            </span>
                                        </div>
                                        <div>
                                            <span className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold block">Input</span>
                                            <span className="text-sm font-mono text-muted-foreground">
                                                {execMetrics.usage?.input_tokens != null ? execMetrics.usage.input_tokens.toLocaleString() : '\u2014'}
                                            </span>
                                        </div>
                                        <div>
                                            <span className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold block">Output</span>
                                            <span className="text-sm font-semibold font-mono text-primary">
                                                {execMetrics.usage?.output_tokens != null ? execMetrics.usage.output_tokens.toLocaleString() : '\u2014'}
                                            </span>
                                        </div>
                                        <div>
                                            <span className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold block">Duration</span>
                                            <span className="text-sm font-mono text-muted-foreground">
                                                {execMetrics.durationMs != null ? `${(execMetrics.durationMs / 1000).toFixed(1)}s` : '\u2014'}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* Escalation History — shows model retry chain or why escalation didn't happen */}
                            {(() => {
                                const md = task.metadata as Record<string, unknown> | undefined;
                                const escalationHistory = md?.escalation_history as Array<{from_model: string | null; from_agent: string | null; to_model: string; to_agent: string}> | undefined;
                                const escalationMax = (md?.escalation_max as number) || 0;
                                const isFailed = task.currentStatus === 'FAILED';
                                const hasHistory = escalationHistory && escalationHistory.length > 0;

                                // Show a message for failed tasks with no escalation history
                                const skipReason = md?.escalation_skip_reason as string | undefined;
                                if (!hasHistory && isFailed && skipReason) {
                                    const model = task.modelName || (md?.selected_model as string) || null;
                                    const reasonMessages: Record<string, React.ReactNode> = {
                                        disabled: 'Escalation is disabled for this board.',
                                        no_priority_list: 'No escalation priority list configured for this board.',
                                        max_retries_reached: 'Max retries reached.',
                                        no_current_model: 'No model assigned to this task.',
                                        model_not_in_list: <>Model {model && <span className="font-mono text-red-400">{model}</span>} is not in the escalation priority list.</>,
                                        already_highest: <>Model {model && <span className="font-mono text-red-400">{model}</span>} is already at the highest priority — no next model to escalate to.</>,
                                    };
                                    return (
                                        <CollapsibleSection label="Escalation" defaultOpen={true}>
                                            <div className="text-[11px] text-muted-foreground/70">
                                                {reasonMessages[skipReason] || `Escalation skipped: ${skipReason}`}
                                            </div>
                                        </CollapsibleSection>
                                    );
                                }

                                if (!hasHistory) return null;

                                const isLast = (idx: number) => idx === escalationHistory.length - 1;
                                return (
                                    <CollapsibleSection label={`Escalation History (${escalationHistory.length}${escalationMax ? `/${escalationMax}` : ''})`} defaultOpen={true}>
                                        <div className="space-y-1">
                                            {escalationHistory.map((entry, idx) => (
                                                <div key={idx} className="flex items-center gap-1.5 text-xs font-mono">
                                                    <span className="text-muted-foreground/60 w-4 text-right shrink-0">#{idx + 1}</span>
                                                    <span className="text-red-400">{entry.from_model || '?'}</span>
                                                    <span className="text-muted-foreground/40">→</span>
                                                    <span className={
                                                        isLast(idx) && isFailed
                                                            ? 'text-red-400'
                                                            : isLast(idx)
                                                                ? 'text-emerald-500 font-medium'
                                                                : 'text-muted-foreground'
                                                    }>
                                                        {entry.to_model}
                                                    </span>
                                                </div>
                                            ))}
                                            {isFailed && (
                                                <div className="text-[10px] text-red-400/80 mt-1">
                                                    {escalationHistory.length >= escalationMax
                                                        ? 'Max retries reached'
                                                        : 'Already at highest priority model'}
                                                </div>
                                            )}
                                        </div>
                                    </CollapsibleSection>
                                );
                            })()}

                            {/* Execution Context — collapsible */}
                            {hasExecContext && (
                                <CollapsibleSection
                                    label="Execution Context"
                                >
                                    {/* Model is now shown next to Assignee above */}

                                    {task.currentStatus === 'TODO' && (
                                        <CompactRow label="Skip reflection" noBorder>
                                            <div className="flex items-center gap-2">
                                                <Switch
                                                    checked={!!task.skipReflection}
                                                    onCheckedChange={v => onUpdateTask(task.id, { skipReflection: v })}
                                                />
                                                <span className="text-[11px] text-muted-foreground select-none">
                                                    {task.skipReflection ? 'Enabled' : 'Disabled'}
                                                </span>
                                            </div>
                                        </CompactRow>
                                    )}
                                    {routingReasoning && (
                                        <CompactRow label="Routing" noBorder>
                                            <span className="text-xs text-muted-foreground leading-snug">{routingReasoning}</span>
                                        </CompactRow>
                                    )}

                                    {execContext.cwd && (
                                        <CompactRow label="CWD" noBorder>
                                            <div className="flex items-center gap-1.5">
                                                <span className="text-xs font-mono break-all">{execContext.cwd}</span>
                                                <CopyButton text={execContext.cwd} />
                                            </div>
                                        </CompactRow>
                                    )}


                                    {execContext.harness && (
                                        <CompactRow label="Harness" noBorder>
                                            <span className="text-xs font-mono">{execContext.harness}</span>
                                        </CompactRow>
                                    )}

                                    {task.complexity && (
                                        <CompactRow label="Complexity" noBorder>
                                            <Badge variant="outline" className="text-[10px] font-semibold uppercase h-5 px-1.5">{task.complexity}</Badge>
                                        </CompactRow>
                                    )}

                                    <CompactRow label="Dependencies" noBorder>
                                        <div className="w-full space-y-2">
                                            <div className="flex justify-end">
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    className="h-6 px-2 text-[10px]"
                                                    onClick={openDependencyPicker}
                                                >
                                                    Manage Dependencies
                                                </Button>
                                            </div>
                                            {currentDependencyIds.length > 0 ? (
                                                <div className="space-y-1.5">
                                                    {currentDependencyIds.map(dep => {
                                                        const depTask = taskMap.get(dep);
                                                        const isClickable = !!(depTask && onSelectTask);
                                                        return (
                                                            <div
                                                                key={dep}
                                                                className={`flex items-center gap-2 rounded-md border border-border/60 bg-muted/30 px-2.5 py-2 ${isClickable ? 'cursor-pointer hover:bg-muted/60 transition-colors' : ''}`}
                                                                onClick={isClickable ? () => onSelectTask!(depTask!.id) : undefined}
                                                            >
                                                                <span className="shrink-0 rounded bg-background px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                                                                    #{depTask?.idShort || dep}
                                                                </span>
                                                                <span className={`min-w-0 truncate text-xs font-medium ${isClickable ? 'text-primary hover:underline' : ''}`}>
                                                                    {depTask?.title || depTask?.name || `Task ${dep}`}
                                                                </span>
                                                                {depTask && (
                                                                    <Badge
                                                                        className="ml-auto shrink-0 border-0 px-1.5 py-0 text-[9px]"
                                                                        style={{ background: `${getStatusColor(depTask.currentStatus)}20`, color: getStatusColor(depTask.currentStatus) }}
                                                                    >
                                                                        {depTask.currentStatus}
                                                                    </Badge>
                                                                )}
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            ) : (
                                                <div className="flex items-center justify-between rounded-md border border-dashed border-border/70 px-2.5 py-2">
                                                    <span className="text-xs text-muted-foreground">No dependencies</span>
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        className="h-6 px-2 text-[10px]"
                                                        onClick={openDependencyPicker}
                                                    >
                                                        Add
                                                    </Button>
                                                </div>
                                            )}
                                        </div>
                                    </CompactRow>

                                </CollapsibleSection>
                            )}

                            {/* Time Distribution — collapsible */}
                            {statusEntries.length > 0 && (
                                <CollapsibleSection label="Time Distribution">
                                    <div className="flex h-1.5 rounded-full overflow-hidden mb-2 bg-secondary">
                                        {statusEntries.map(([status, ms]) => {
                                            const pct = totalStatusTime > 0 ? (ms / totalStatusTime) * 100 : 0;
                                            return (
                                                <div key={status} style={{ flex: pct, background: getStatusColor(status) }}
                                                    title={`${status}: ${formatDuration(ms)}`} />
                                            );
                                        })}
                                    </div>
                                    <div className="space-y-0.5">
                                        {statusEntries.map(([status, ms]) => (
                                            <div key={status} className="flex items-center justify-between text-[10px] text-muted-foreground">
                                                <div className="flex items-center gap-1.5">
                                                    <span className="size-1.5 rounded-full" style={{ background: getStatusColor(status) }} />
                                                    <span>{status}</span>
                                                </div>
                                                <span className="font-mono font-medium">{formatDuration(ms)}</span>
                                            </div>
                                        ))}
                                    </div>
                                </CollapsibleSection>
                            )}

                            {/* Last Execution — collapsible, shows summary/status (cost+tokens shown above) */}
                            {execMetrics.summary && (
                                <CollapsibleSection
                                    label="Last Execution"
                                >
                                    <div className="space-y-1">
                                        <div className="flex items-center gap-1.5">
                                            <span className={`size-2 rounded-full ${execMetrics.success ? 'bg-emerald-500' : 'bg-red-500'}`} />
                                            <span className="text-xs font-medium">{execMetrics.success ? 'Completed' : 'Failed'}</span>
                                            {execMetrics.durationMs && (
                                                <span className="text-[10px] font-mono text-muted-foreground">
                                                    {(execMetrics.durationMs / 1000).toFixed(1)}s
                                                </span>
                                            )}
                                        </div>
                                        <div className="text-xs text-muted-foreground/80 mt-1">{execMetrics.summary}</div>
                                    </div>
                                </CollapsibleSection>
                            )}
                        </div>

                        {/* Reflect + Delete — pinned at bottom of sidebar */}
                        {canReflect && (
                            <div className="shrink-0 px-4 py-1.5 border-t border-border/50">
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    className="w-full text-indigo-400 hover:text-indigo-300 hover:bg-indigo-500/10 gap-1.5 h-7 text-xs"
                                    onClick={() => setShowReflectionModal(true)}
                                >
                                    <Sparkles className="size-3" />
                                    Reflect
                                    {reflections.length > 0 && (
                                        <Badge variant="outline" className="ml-auto text-[9px] h-4 px-1 bg-indigo-500/10 text-indigo-400 border-indigo-500/20">
                                            {reflections.length}
                                        </Badge>
                                    )}
                                </Button>
                            </div>
                        )}
                        {onDeleteTask && (
                            <div className="shrink-0 px-4 py-2.5 border-t border-border/50">
                                <AlertDialog>
                                    <AlertDialogTrigger asChild>
                                        <Button variant="ghost" size="sm" className="w-full text-destructive/70 hover:text-destructive hover:bg-destructive/10 gap-1.5 h-7 text-xs">
                                            <Trash2 className="size-3" /> Delete Task
                                        </Button>
                                    </AlertDialogTrigger>
                                    <AlertDialogContent>
                                        <AlertDialogHeader>
                                            <AlertDialogTitle>Delete Task?</AlertDialogTitle>
                                            <AlertDialogDescription>
                                                This action cannot be undone. This will permanently delete
                                                <span className="font-bold text-foreground"> "{task.title || task.name}"</span>.
                                            </AlertDialogDescription>
                                        </AlertDialogHeader>
                                        <AlertDialogFooter>
                                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                                            <AlertDialogAction onClick={() => onDeleteTask(task.id)} className="bg-destructive hover:bg-destructive/90">
                                                Delete Task
                                            </AlertDialogAction>
                                        </AlertDialogFooter>
                                    </AlertDialogContent>
                                </AlertDialog>
                            </div>
                        )}
                    </div>

                    {/* RIGHT COLUMN: Description & Timeline */}
                    <div className="flex-1 min-w-0 overflow-y-auto">
                        <div className="px-6 py-4">
                        {/* Description */}
                        <div className="mb-8">
                            <div className="flex items-center justify-between mb-3">
                                <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
                                    Description
                                    {isJson && (
                                        <Badge variant="outline" className="text-[10px] py-0 h-5 font-normal bg-blue-500/10 text-blue-400 border-blue-500/20">JSON Detected</Badge>
                                    )}
                                </h3>
                                <div className="flex items-center gap-2">
                                    {isJson && (
                                        <Button variant="ghost" size="sm" onClick={() => setShowRawJson(!showRawJson)} className="h-7 text-xs gap-1.5">
                                            {showRawJson ? <><Eye className="size-3" /> View Organized</> : <><Code className="size-3" /> View Raw JSON</>}
                                        </Button>
                                    )}
                                    {editingField !== 'description' && (
                                        <button className="opacity-50 hover:opacity-100 transition-opacity p-1" onClick={() => startEditingField('description', task.description || '')}>
                                            <Pencil className="size-3.5" />
                                        </button>
                                    )}
                                </div>
                            </div>

                            {editingField === 'description' ? (
                                <div className="flex flex-col gap-2">
                                    <MarkdownEditor
                                        value={editValue}
                                        onChange={setEditValue}
                                        rows={12}
                                        className="font-mono text-sm leading-relaxed"
                                        placeholder="Write task details with markdown, lists, and code blocks..."
                                    />
                                    <div className="flex gap-2 justify-end">
                                        <Button size="sm" variant="outline" onClick={() => setEditingField(null)}>Cancel</Button>
                                        <Button size="sm" onClick={handleSaveField}>Save</Button>
                                    </div>
                                </div>
                            ) : (
                                <div className="rounded-lg border border-border bg-card overflow-hidden shadow-sm">
                                    {isJson && !showRawJson && descriptionData ? (
                                        <div className="p-4 text-sm font-mono overflow-x-auto bg-[#0d1117]">
                                            <JsonViewer data={descriptionData} />
                                        </div>
                                    ) : (
                                        <div className={`p-4 text-sm leading-relaxed ${isJson ? 'font-mono text-xs whitespace-pre-wrap' : ''}`}>
                                            {isJson ? (
                                                task.description || <em className="text-muted-foreground">No description provided.</em>
                                            ) : (
                                                <MarkdownRenderer
                                                    text={task.description}
                                                    emptyFallback={<em className="text-muted-foreground">No description provided.</em>}
                                                />
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>

                        {/* Reference Images */}
                        {task.referenceImages && task.referenceImages.length > 0 && (
                            <div className="mb-8 mt-6">
                                <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider mb-3">
                                    Reference Images
                                </h3>
                                <div className="flex flex-wrap gap-3">
                                    {task.referenceImages.map(img => (
                                        <button
                                            key={img.id}
                                            type="button"
                                            className="block text-left cursor-zoom-in"
                                            onClick={() => setRefImageLightbox({ url: img.url, filename: img.originalFilename })}
                                        >
                                            <img
                                                src={img.url}
                                                alt={img.originalFilename}
                                                loading="lazy"
                                                className="rounded border border-border/40 max-w-[300px] max-h-[200px] object-contain hover:border-cyan-500/50 transition-colors"
                                            />
                                            <span className="text-[10px] text-muted-foreground/60 font-mono block mt-0.5 truncate max-w-[300px]">
                                                {img.originalFilename}
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        <Separator className="mb-8" />

                        {/* Comments Section */}
                        {(task.comments.length > 0 || true) && (
                            <div>
                                <div className="flex items-center justify-between mb-4">
                                    <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider">
                                        Comments ({detailLoading ? '...' : task.comments.length})
                                    </h3>
                                    <div className="flex items-center gap-2">
                                        {task.comments.some(c => Array.isArray(c.attachments) && c.attachments.some(a => typeof a === 'string' && a.startsWith('debug:'))) && (
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground gap-1"
                                                onClick={() => setShowDebugComments(!showDebugComments)}
                                            >
                                                <Terminal className="size-3" />
                                                {showDebugComments ? 'Hide' : 'Show'} debug logs
                                            </Button>
                                        )}
                                        <div className="flex flex-col items-end gap-0.5">
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                onClick={handleSummarize}
                                                disabled={isSummarizing}
                                                className="h-6 px-2 text-xs text-purple-400 hover:text-purple-300 hover:bg-purple-400/10 gap-1"
                                            >
                                                {isSummarizing
                                                    ? <Loader2 className="size-3 animate-spin" />
                                                    : <Sparkles className="size-3" />
                                                }
                                                {isSummarizing ? 'Summarizing...' : 'Summarize'}
                                            </Button>
                                            {summarizeError && (
                                                <span className="text-[10px] text-red-400">{summarizeError}</span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                                <div className="space-y-3 mb-4">
                                    {detailLoading ? (
                                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                            <div className="size-4 rounded-full border-2 border-border border-t-primary animate-spin" />
                                            Loading comments...
                                        </div>
                                    ) : (
                                        <>
                                            {(showAllComments ? task.comments : task.comments.slice(-10))
                                                .filter(c => {
                                                    // Hide debug comments unless toggled
                                                    if (!showDebugComments && Array.isArray(c.attachments) && c.attachments.some(a => typeof a === 'string' && a.startsWith('debug:'))) return false;
                                                    // Hide replies that are shown inline under their question
                                                    if (c.commentType === 'reply') return false;
                                                    return true;
                                                })
                                                .map(comment => (
                                                <CommentItem
                                                    key={comment.id}
                                                    comment={comment}
                                                    onReply={(qId) => setReplyingTo(qId)}
                                                    replyComment={replyMap.get(comment.id)}
                                                />
                                            ))}
                                            {!showAllComments && task.comments.length > 10 && (
                                                <Button variant="ghost" size="sm" className="w-full text-xs text-muted-foreground"
                                                    onClick={() => setShowAllComments(true)}>
                                                    Show {task.comments.length - 10} older comments
                                                </Button>
                                            )}
                                            {task.comments.length === 0 && (
                                                <p className="text-sm text-muted-foreground/50 italic">No comments yet.</p>
                                            )}
                                        </>
                                    )}
                                </div>
                                {/* Reply input (for answering questions) */}
                                {replyingTo && (
                                    <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
                                        <div className="flex items-center gap-1.5 text-xs text-amber-600 mb-2">
                                            <CornerDownRight className="size-3" />
                                            <span className="font-medium">Replying to question #{replyingTo}</span>
                                            <button className="ml-auto text-muted-foreground hover:text-foreground text-[10px]" onClick={() => setReplyingTo(null)}>Cancel</button>
                                        </div>
                                        <div className="flex gap-2">
                                            <Input
                                                placeholder="Type your reply..."
                                                value={replyText}
                                                onChange={e => setReplyText(e.target.value)}
                                                onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handlePostReply()}
                                                autoFocus
                                                className="text-sm border-amber-500/20"
                                            />
                                            <Button
                                                size="sm"
                                                onClick={handlePostReply}
                                                disabled={!replyText.trim() || isPostingReply}
                                                className="gap-1 bg-amber-600 hover:bg-amber-700"
                                            >
                                                <Send className="size-3" /> Reply
                                            </Button>
                                        </div>
                                    </div>
                                )}
                                {/* Add comment input */}
                                <div className="flex gap-2">
                                    <Input
                                        placeholder="Add a comment..."
                                        value={commentText}
                                        onChange={e => setCommentText(e.target.value)}
                                        onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handlePostComment()}
                                        className="text-sm"
                                    />
                                    <Button
                                        size="sm"
                                        onClick={handlePostComment}
                                        disabled={!commentText.trim() || isPostingComment}
                                    >
                                        Post
                                    </Button>
                                </div>
                            </div>
                        )}

                        {/* Reflections Section */}
                        {reflections.length > 0 && (
                            <>
                                <Separator className="my-6" />
                                <ReflectionReportViewer
                                    reports={reflections}
                                    onCancel={handleCancelReflection}
                                    onDelete={handleDeleteReflection}
                                    onViewDetail={(id) => window.open(`/reflections/${id}`, '_blank')}
                                />
                            </>
                        )}

                        <Separator className="my-6" />

                        {/* Activity Timeline */}
                        <div>
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider">
                                    Activity ({visibleMutations.length})
                                </h3>
                                {hiddenCount > 0 && (
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground"
                                        onClick={() => setShowAllHistory(!showAllHistory)}
                                    >
                                        {showAllHistory ? 'Show key events' : `Show all history (+${hiddenCount})`}
                                    </Button>
                                )}
                            </div>
                            <div className="relative">
                                <div className="absolute left-[7px] top-4 bottom-4 w-px bg-border" />
                                <div className="space-y-5">
                                    {visibleMutations.map((mutation, idx) => (
                                        <MutationItem key={mutation.id || idx} mutation={mutation} />
                                    ))}
                                </div>
                            </div>
                        </div>


                        </div>
                    </div>
                </div>
            </DialogContent>

            {/* Reference Image Lightbox */}
            {refImageLightbox && (
                <Dialog open={true} onOpenChange={() => setRefImageLightbox(null)}>
                    <DialogContent className="max-w-[90vw] max-h-[90vh] p-0 bg-black/95 border-border/20 overflow-hidden flex items-center justify-center">
                        <DialogHeader className="sr-only">
                            <DialogTitle>{refImageLightbox.filename}</DialogTitle>
                        </DialogHeader>
                        <img
                            src={refImageLightbox.url}
                            alt={refImageLightbox.filename}
                            className="max-w-full max-h-[85vh] object-contain"
                        />
                        <span className="absolute bottom-3 left-1/2 -translate-x-1/2 text-xs text-white/60 font-mono bg-black/60 px-3 py-1 rounded-full">
                            {refImageLightbox.filename}
                        </span>
                    </DialogContent>
                </Dialog>
            )}

            <IdeSetupModal
                open={showIdeSetup}
                onOpenChange={setShowIdeSetup}
                detectedIdes={ideOptions?.detected_ides || []}
                initialIdeId={ideOptions?.preferred_ide_id || null}
                saving={savingIde}
                onSave={handleSaveIde}
                title="Configure IDE"
                description="Choose the IDE TaskIt should use when opening this board's Odin project root."
            />

            {/* Reflection Modal */}
            {showReflectionModal && (
                <ReflectionModal
                    taskId={task.id}
                    taskIdShort={task.idShort}
                    onClose={() => setShowReflectionModal(false)}
                    onSubmit={handleTriggerReflection}
                />
            )}

            <Dialog open={showDependencyPicker} onOpenChange={setShowDependencyPicker}>
                <DialogContent className="sm:max-w-[640px]">
                    <DialogHeader>
                        <DialogTitle>Edit Dependencies</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4">
                        <p className="text-sm text-muted-foreground">
                            Select active tasks from this board. Tasks that already depend on this one are hidden to prevent cycles.
                        </p>
                        <ScrollArea className="h-[360px] rounded-md border">
                            <div className="p-3 space-y-2">
                                {currentDependencyIds
                                    .filter(depId => !activeDependencyCandidates.some(taskItem => taskItem.id === depId))
                                    .map(depId => {
                                        const depTask = taskMap.get(depId) || boardTasks.find(taskItem => taskItem.id === depId);
                                        return (
                                            <label key={`existing-${depId}`} className="flex items-start gap-3 rounded-md border p-3">
                                                <Checkbox
                                                    checked={dependencyDraft.includes(depId)}
                                                    onCheckedChange={(next) => {
                                                        setDependencyDraft(prev => next ? [...prev, depId] : prev.filter(id => id !== depId));
                                                    }}
                                                />
                                                <div className="min-w-0">
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                                        <span className="font-mono">#{depTask?.idShort || depId}</span>
                                                        <Badge variant="outline" className="h-5 text-[10px]">
                                                            {depTask?.currentStatus || 'UNKNOWN'}
                                                        </Badge>
                                                    </div>
                                                    <div className="mt-1 text-sm font-medium">{depTask?.title || depTask?.name || `Task ${depId}`}</div>
                                                    <div className="mt-1 text-xs text-muted-foreground">Existing inactive dependency</div>
                                                </div>
                                            </label>
                                        );
                                    })}
                                {activeDependencyCandidates.map(depTask => (
                                    <label key={depTask.id} className="flex items-start gap-3 rounded-md border p-3">
                                        <Checkbox
                                            checked={dependencyDraft.includes(depTask.id)}
                                            onCheckedChange={(next) => {
                                                setDependencyDraft(prev => next ? [...prev, depTask.id] : prev.filter(id => id !== depTask.id));
                                            }}
                                        />
                                        <div className="min-w-0">
                                            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                                <span className="font-mono">#{depTask.idShort}</span>
                                                <Badge variant="outline" className="h-5 text-[10px]">{depTask.currentStatus}</Badge>
                                            </div>
                                            <div className="mt-1 text-sm font-medium">{depTask.title || depTask.name}</div>
                                        </div>
                                    </label>
                                ))}
                                {currentDependencyIds.filter(depId => !activeDependencyCandidates.some(taskItem => taskItem.id === depId)).length === 0
                                    && activeDependencyCandidates.length === 0 && (
                                    <p className="text-sm text-muted-foreground">
                                        No current-board tasks in To Do or In Progress are available to add as dependencies.
                                    </p>
                                )}
                            </div>
                        </ScrollArea>
                        <div className="flex justify-end gap-2">
                            <Button variant="outline" onClick={() => setShowDependencyPicker(false)} disabled={savingDependencies}>Cancel</Button>
                            <Button onClick={handleSaveDependencies} disabled={savingDependencies}>
                                {savingDependencies ? 'Saving...' : 'Save'}
                            </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>

        </Dialog>
    );
}

function CollapsibleContent({ children, maxHeight = 150 }: { children: React.ReactNode; maxHeight?: number }) {
    const ref = useRef<HTMLDivElement>(null);
    const [overflows, setOverflows] = useState(false);
    const [expanded, setExpanded] = useState(false);

    const measure = useCallback(() => {
        if (ref.current) {
            setOverflows(ref.current.scrollHeight > maxHeight);
        }
    }, [maxHeight]);

    useEffect(() => {
        measure();
        const el = ref.current;
        if (!el) return;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [measure]);

    return (
        <div>
            <div
                ref={ref}
                className={expanded ? '' : 'overflow-hidden'}
                style={!expanded && overflows ? { maxHeight } : undefined}
            >
                {children}
            </div>
            {overflows && !expanded && (
                <div className="relative -mt-6 pt-6 bg-gradient-to-t from-card/90 to-transparent">
                    <button
                        className="text-[11px] text-muted-foreground hover:text-foreground font-medium transition-colors"
                        onClick={() => setExpanded(true)}
                    >
                        Show more
                    </button>
                </div>
            )}
            {overflows && expanded && (
                <button
                    className="text-[11px] text-muted-foreground hover:text-foreground font-medium mt-1 transition-colors"
                    onClick={() => setExpanded(false)}
                >
                    Show less
                </button>
            )}
        </div>
    );
}

function CommentItem({ comment, onReply, replyComment }: {
    comment: TaskComment;
    onReply?: (questionCommentId: string) => void;
    replyComment?: TaskComment;
}) {
    const actor = parseActor(comment.authorEmail);
    const [showTrace, setShowTrace] = useState(false);

    const [traceCopyState, setTraceCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
    const [lightboxImage, setLightboxImage] = useState<{ url: string; filename: string } | null>(null);
    const [zoomScale, setZoomScale] = useState(1);
    const [panX, setPanX] = useState(0);
    const [panY, setPanY] = useState(0);
    const [isDragging, setIsDragging] = useState(false);
    const dragStartRef = useRef<{ x: number; y: number } | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const imageWrapperRef = useRef<HTMLDivElement>(null);
    const imageRef = useRef<HTMLImageElement>(null);
    const viewStateRef = useRef({ zoomScale: 1, panX: 0, panY: 0 });
    const pinchStateRef = useRef<{ lastDistance: number } | null>(null);

    useEffect(() => {
        if (!lightboxImage) {
            setZoomScale(1);
            setPanX(0);
            setPanY(0);
        }
    }, [lightboxImage]);

    useEffect(() => {
        if (traceCopyState === 'idle') return;
        const t = window.setTimeout(() => setTraceCopyState('idle'), 1400);
        return () => window.clearTimeout(t);
    }, [traceCopyState]);

    useEffect(() => {
        viewStateRef.current = { zoomScale, panX, panY };
    }, [zoomScale, panX, panY]);

    const commentType = comment.commentType || 'status_update';
    const isQuestion = commentType === 'question';
    const isReply = commentType === 'reply';
    const isSummary = commentType === 'summary';
    const isExecutionTraceAtt = Array.isArray(comment.attachments) && comment.attachments.includes('trace:execution_jsonl');
    const isDebugInput = Array.isArray(comment.attachments) && comment.attachments.some(a => typeof a === 'string' && a.startsWith('debug:'));
    const isReflection = commentType === 'reflection';
    const isProof = commentType === 'proof';
    const isProofAtt = Array.isArray(comment.attachments) && comment.attachments.some(a => typeof a === 'object' && a !== null && (a as Record<string, unknown>).type === 'proof');
    const isProofFinal = isProof || isProofAtt;
    const isMcpAgent = comment.authorEmail?.endsWith('@odin.agent') ?? false;

    // Determine question status from attachments
    const questionStatus = isQuestion
        ? (comment.attachments as Array<Record<string, unknown>>)?.find(a => a?.type === 'question')?.status as string || 'pending'
        : null;
    const isPending = questionStatus === 'pending';

    const [metricsLine, ...bodyLines] = comment.content.split('\n');
    const hasMetrics = metricsLine.startsWith('Completed in ') || metricsLine.startsWith('Failed in ');
    const rawBody = hasMetrics ? bodyLines.join('\n').trim() : comment.content;
    const metrics = hasMetrics ? metricsLine : null;

    const { summary, traceData } = parseCommentBody(rawBody);
    const failureDetails = parseFailureDetails(summary);
    const traceText = useMemo(() => {
        const parts: string[] = [];
        if (failureDetails.failureDebug) parts.push(`Debug: ${failureDetails.failureDebug}`);
        if (traceData) parts.push(traceData);
        return parts.join('\n\n').trim();
    }, [failureDetails.failureDebug, traceData]);

    const handleZoomIn = () => setZoomScale(s => Math.min(s + 0.1, 5));
    const handleZoomOut = () => setZoomScale(s => Math.max(s - 0.1, 0.5));
    const handleResetZoom = () => {
        setZoomScale(1);
        setPanX(0);
        setPanY(0);
    };

    // Clamp pan values to prevent image from going completely off-screen
    const clampPan = (panXValue: number, panYValue: number, scale: number) => {
        const container = containerRef.current;
        const image = imageRef.current;
        if (!container || !image) return { x: panXValue, y: panYValue };

        const containerRect = container.getBoundingClientRect();
        const imageRect = image.getBoundingClientRect();

        const scaledWidth = imageRect.width * scale;
        const scaledHeight = imageRect.height * scale;

        const maxX = Math.max(0, (scaledWidth - containerRect.width) / 2);
        const maxY = Math.max(0, (scaledHeight - containerRect.height) / 2);

        return {
            x: Math.max(-maxX, Math.min(maxX, panXValue)),
            y: Math.max(-maxY, Math.min(maxY, panYValue))
        };
    };

    const applyZoomAtPoint = (
        nextScale: number,
        clientX: number,
        clientY: number,
        previous = viewStateRef.current,
    ) => {
        const wrapper = imageWrapperRef.current;
        if (!wrapper) return;

        const clampedScale = Math.max(0.5, Math.min(5, nextScale));
        const { zoomScale: prevScale, panX: prevPanX, panY: prevPanY } = previous;
        if (clampedScale === prevScale) return;

        const rect = wrapper.getBoundingClientRect();
        const imageX = (clientX - rect.left) / prevScale;
        const imageY = (clientY - rect.top) / prevScale;
        const baseLeft = rect.left - prevPanX;
        const baseTop = rect.top - prevPanY;

        const nextPanX = clientX - baseLeft - imageX * clampedScale;
        const nextPanY = clientY - baseTop - imageY * clampedScale;
        const clampedPan = clampPan(nextPanX, nextPanY, clampedScale);

        setZoomScale(clampedScale);
        setPanX(clampedPan.x);
        setPanY(clampedPan.y);
    };

    const handleWheelZoom = (e: React.WheelEvent) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        applyZoomAtPoint(viewStateRef.current.zoomScale + delta, e.clientX, e.clientY);
    };

    const handleMouseDown = (e: React.MouseEvent) => {
        if (zoomScale <= 1) return; // Only allow dragging when zoomed in
        e.preventDefault();
        setIsDragging(true);
        dragStartRef.current = { x: e.clientX - panX, y: e.clientY - panY };
    };

    const handleMouseMove = (e: React.MouseEvent) => {
        if (!isDragging || zoomScale <= 1 || !dragStartRef.current) return;
        e.preventDefault();

        const newX = e.clientX - dragStartRef.current.x;
        const newY = e.clientY - dragStartRef.current.y;

        const clamped = clampPan(newX, newY, zoomScale);
        setPanX(clamped.x);
        setPanY(clamped.y);
    };

    const handleMouseUp = () => {
        setIsDragging(false);
        dragStartRef.current = null;
    };

    const handleMouseLeave = () => {
        setIsDragging(false);
        dragStartRef.current = null;
    };

    const getTouchDistance = (touches: TouchList) => {
        const [a, b] = [touches[0], touches[1]];
        return Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY);
    };

    const getTouchCenter = (touches: TouchList) => {
        const [a, b] = [touches[0], touches[1]];
        return {
            x: (a.clientX + b.clientX) / 2,
            y: (a.clientY + b.clientY) / 2,
        };
    };

    const startPinchGesture = (touches: TouchList) => {
        if (touches.length !== 2) {
            pinchStateRef.current = null;
            return;
        }
        pinchStateRef.current = {
            lastDistance: getTouchDistance(touches),
        };
    };

    const updatePinchGesture = (touches: TouchList) => {
        if (touches.length !== 2 || !pinchStateRef.current) return;
        const center = getTouchCenter(touches);
        const currentDistance = getTouchDistance(touches);
        const { lastDistance } = pinchStateRef.current;
        const { zoomScale: currentScale } = viewStateRef.current;
        const scaleFactor = lastDistance > 0 ? currentDistance / lastDistance : 1;
        const nextScale = currentScale * scaleFactor;

        applyZoomAtPoint(nextScale, center.x, center.y);
        pinchStateRef.current = { lastDistance: currentDistance };
    };

    useEffect(() => {
        const container = containerRef.current;
        if (!container || !lightboxImage) return;

        const onTouchStart = (e: TouchEvent) => {
            if (e.touches.length !== 2) {
                pinchStateRef.current = null;
                return;
            }
            e.preventDefault();
            startPinchGesture(e.touches);
        };

        const onTouchMove = (e: TouchEvent) => {
            if (e.touches.length !== 2 || !pinchStateRef.current) return;
            e.preventDefault();
            updatePinchGesture(e.touches);
        };

        const onTouchEnd = () => {
            if (pinchStateRef.current) pinchStateRef.current = null;
        };

        container.addEventListener('touchstart', onTouchStart, { passive: false });
        container.addEventListener('touchmove', onTouchMove, { passive: false });
        container.addEventListener('touchend', onTouchEnd, { passive: false });
        container.addEventListener('touchcancel', onTouchEnd, { passive: false });

        return () => {
            container.removeEventListener('touchstart', onTouchStart);
            container.removeEventListener('touchmove', onTouchMove);
            container.removeEventListener('touchend', onTouchEnd);
            container.removeEventListener('touchcancel', onTouchEnd);
        };
    }, [lightboxImage]);

    // Extract reflection verdict from attachments for color coding
    const reflectionVerdict = isReflection
        ? (comment.attachments as Array<Record<string, unknown>>)?.find(a => a?.type === 'reflection')?.verdict as string || ''
        : '';

    // Type-specific border and background styles
    const borderStyle = isSummary
        ? 'border-l-2 border-l-purple-400/60 border-purple-400/20 bg-purple-400/5'
        : isQuestion
            ? (isPending ? 'border-l-4 border-l-amber-500 border-amber-500/20 bg-amber-500/5' : 'border-l-4 border-l-amber-500/40 border-border bg-card')
            : isReply
                ? 'border-l-4 border-l-emerald-500 border-border bg-card ml-6'
                : isReflection
                    ? 'border-l-4 border-l-violet-500 border-violet-500/20 bg-violet-500/5'
                    : isProofFinal
                        ? 'border-l-4 border-l-cyan-500 border-cyan-500/20 bg-cyan-500/5'
                        : 'border-border bg-card';

    return (
        <div className={`rounded-lg border p-3 ${borderStyle}`}>
            <div className="flex items-baseline justify-between mb-1">
                <div className="flex items-center gap-1.5">
                    {isSummary && <Sparkles className="size-3.5 text-purple-400" />}
                    {isQuestion && <HelpCircle className={`size-3.5 ${isPending ? 'text-amber-500 animate-pulse' : 'text-amber-500/50'}`} />}
                    {isReply && <CornerDownRight className="size-3.5 text-emerald-500" />}
                    {isReflection && <Sparkles className="size-3.5 text-violet-400" />}
                    {isProofFinal && !isQuestion && !isReply && !isSummary && !isReflection && <ShieldCheck className="size-3.5 text-cyan-400" />}
                    <span className="text-sm font-semibold flex items-center gap-1">
                        {comment.authorLabel || actor.display}
                        {isMcpAgent && <Bot className="size-3 text-indigo-500" />}
                    </span>
                    {/* Comment type badge */}
                    {isSummary && (
                        <Badge variant="outline" className="text-[9px] h-4 px-1 bg-purple-400/20 text-purple-300 border-purple-400/30 font-semibold">
                            summary
                        </Badge>
                    )}
                    {isQuestion && isPending && (
                        <Badge variant="outline" className="text-[9px] h-4 px-1 bg-amber-500/10 text-amber-500 border-amber-500/20 font-semibold">
                            PENDING
                        </Badge>
                    )}
                    {isQuestion && !isPending && (
                        <Badge variant="outline" className="text-[9px] h-4 px-1 bg-emerald-500/10 text-emerald-500 border-emerald-500/20 font-semibold">
                            ANSWERED
                        </Badge>
                    )}
                    {!isQuestion && !isReply && !isSummary && isExecutionTraceAtt && (
                        <Badge variant="outline" className="text-[9px] h-4 px-1 bg-violet-500/10 text-violet-400 border-violet-500/20 font-mono">
                            trace
                        </Badge>
                    )}
                    {!isQuestion && !isReply && !isSummary && isDebugInput && (
                        <Badge variant="outline" className="text-[9px] h-4 px-1 bg-zinc-500/10 text-zinc-400 border-zinc-500/20 font-mono">
                            debug
                        </Badge>
                    )}
                    {!isQuestion && !isReply && !isSummary && isProofFinal && (
                        <Badge variant="outline" className="text-[9px] h-4 px-1 bg-cyan-500/10 text-cyan-400 border-cyan-500/20 font-semibold">
                            proof
                        </Badge>
                    )}
                    {isReflection && (
                        <Badge variant="outline" className={`text-[9px] h-4 px-1 font-semibold ${
                            reflectionVerdict === 'PASS' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                            : reflectionVerdict === 'FAIL' ? 'bg-red-500/10 text-red-400 border-red-500/20'
                            : 'bg-violet-500/10 text-violet-400 border-violet-500/20'
                        }`}>
                            reflection
                        </Badge>
                    )}
                    {!isQuestion && !isReply && !isSummary && !isExecutionTraceAtt && !isDebugInput && !isProofFinal && !isReflection && commentType === 'status_update' && (
                        <Badge variant="outline" className="text-[9px] h-4 px-1 bg-sky-500/10 text-sky-400 border-sky-500/20 font-mono">
                            {isMcpAgent ? 'status-via-mcp' : 'status'}
                        </Badge>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground font-mono">{formatDate(comment.createdAt)}</span>
                </div>
            </div>
            {metrics && (
                <div className="text-xs font-mono mb-1 text-muted-foreground">{metrics}</div>
            )}
            {!summary && isExecutionTraceAtt && traceText && (
                <CollapsibleContent>
                    <TraceViewer traceText={traceText} />
                </CollapsibleContent>
            )}
            {summary && (
                isSummary ? (
                    <div className="mt-1 rounded-md border border-amber-400/20 bg-card/60 p-3 overflow-x-auto">
                        <CollapsibleContent>
                            <MarkdownRenderer
                                text={summary}
                                className="text-sm text-foreground/80"
                            />
                        </CollapsibleContent>
                    </div>
                ) : (
                    <>
                        {(failureDetails.failureType || failureDetails.failureReason || failureDetails.failureOrigin) && (
                            <div className="mb-2 rounded border border-red-500/20 bg-red-500/5 p-2">
                                <div className="text-[11px] font-semibold text-red-400 mb-1">Failure details</div>
                                {failureDetails.failureType && <div className="text-xs text-foreground/80"><span className="font-medium">Type:</span> {failureDetails.failureType}</div>}
                                {failureDetails.failureReason && <div className="text-xs text-foreground/80"><span className="font-medium">Reason:</span> {failureDetails.failureReason}</div>}
                                {failureDetails.failureOrigin && <div className="text-xs text-foreground/80"><span className="font-medium">Origin:</span> {failureDetails.failureOrigin}</div>}
                            </div>
                        )}
                        {failureDetails.displaySummary && (
                            <CollapsibleContent>
                                <MarkdownRenderer
                                    text={failureDetails.displaySummary}
                                    className="text-sm text-foreground/80"
                                />
                            </CollapsibleContent>
                        )}
                    </>
                )
            )}
            {(traceData || failureDetails.failureDebug) && !(!summary && isExecutionTraceAtt && traceText) && (
                <div className="mt-2">
                    <button
                        className="text-[10px] text-muted-foreground/60 hover:text-muted-foreground font-mono flex items-center gap-1 transition-colors"
                        onClick={() => setShowTrace(!showTrace)}
                    >
                        <Code className="size-3" />
                        {showTrace ? 'Hide' : 'Show'} failure/trace details
                    </button>
                    {showTrace && (
                        <>
                            {failureDetails.failureDebug && (
                                <pre className="mt-1.5 text-[10px] font-mono text-muted-foreground/70 bg-red-500/5 rounded p-2 overflow-x-auto max-h-[150px] overflow-y-auto border border-red-500/20 whitespace-pre-wrap break-all">
                                    Debug: {failureDetails.failureDebug}
                                </pre>
                            )}
                            {traceData && (
                                <TraceViewer traceText={traceData} />
                            )}
                        </>
                    )}
                </div>
            )}

            {/* Screenshot attachments */}
            {comment.fileAttachments && comment.fileAttachments.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                    {comment.fileAttachments
                        .filter(fa => fa.contentType.startsWith('image/'))
                        .map(fa => (
                            <button
                                key={fa.id}
                                type="button"
                                className="block text-left cursor-zoom-in"
                                onClick={() => setLightboxImage({ url: fa.url, filename: fa.originalFilename })}
                            >
                                <img
                                    src={fa.url}
                                    alt={fa.originalFilename}
                                    loading="lazy"
                                    className="rounded border border-border/40 max-w-[300px] max-h-[200px] object-contain hover:border-cyan-500/50 transition-colors"
                                />
                                <span className="text-[10px] text-muted-foreground/60 font-mono block mt-0.5 truncate max-w-[300px]">
                                    {fa.originalFilename}
                                </span>
                            </button>
                        ))
                    }
                </div>
            )}

            {/* Image lightbox preview */}
            {lightboxImage && (
                <Dialog open={true} onOpenChange={() => setLightboxImage(null)}>
                    <DialogContent className="max-w-[95vw] max-h-[95vh] w-full h-full p-0 bg-black/95 border-border/20 overflow-hidden flex flex-col items-center justify-center">
                        <DialogHeader className="sr-only">
                            <DialogTitle>{lightboxImage.filename}</DialogTitle>
                        </DialogHeader>

                        <div
                            ref={containerRef}
                            className="relative w-full h-full flex items-center justify-center overflow-auto custom-scrollbar p-12"
                            onWheel={handleWheelZoom}
                            onMouseDown={handleMouseDown}
                            onMouseMove={handleMouseMove}
                            onMouseUp={handleMouseUp}
                            onMouseLeave={handleMouseLeave}
                            style={{
                                cursor: zoomScale > 1 ? (isDragging ? 'grabbing' : 'grab') : 'default',
                                touchAction: 'none',
                            }}
                        >
                            <div
                                ref={imageWrapperRef}
                                style={{
                                    transform: `translate(${panX}px, ${panY}px) scale(${zoomScale})`,
                                    transformOrigin: 'top left',
                                    transition: isDragging ? 'none' : 'transform 0.1s ease-out',
                                    willChange: 'transform'
                                }}
                                className="flex items-center justify-center"
                            >
                                <img
                                    ref={imageRef}
                                    src={lightboxImage.url}
                                    alt={lightboxImage.filename}
                                    className="max-w-full max-h-[85vh] object-contain shadow-2xl rounded-sm"
                                    style={{ imageRendering: 'auto' }}
                                />
                            </div>
                        </div>

                        {/* Zoom Controls Overlay */}
                        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-2 px-4 py-2 bg-black/60 backdrop-blur-md border border-white/10 rounded-full shadow-2xl z-50">
                            <Button
                                size="icon"
                                variant="ghost"
                                className="size-8 rounded-full text-white/70 hover:text-white hover:bg-white/10"
                                onClick={handleZoomOut}
                                disabled={zoomScale <= 0.5}
                            >
                                <ZoomOut className="size-4" />
                            </Button>
                            <span className="text-xs font-mono text-white/90 min-w-[3rem] text-center">
                                {Math.round(zoomScale * 100)}%
                            </span>
                            <Button
                                size="icon"
                                variant="ghost"
                                className="size-8 rounded-full text-white/70 hover:text-white hover:bg-white/10"
                                onClick={handleZoomIn}
                                disabled={zoomScale >= 5}
                            >
                                <ZoomIn className="size-4" />
                            </Button>
                            <div className="w-px h-4 bg-white/10 mx-1" />
                            <Button
                                size="icon"
                                variant="ghost"
                                className="size-8 rounded-full text-white/70 hover:text-white hover:bg-white/10"
                                onClick={handleResetZoom}
                                title="Reset Zoom"
                            >
                                <RotateCcw className="size-4" />
                            </Button>
                        </div>

                        <span className="absolute top-4 left-1/2 -translate-x-1/2 text-[10px] text-white/40 font-mono tracking-wider uppercase">
                            {lightboxImage.filename}
                        </span>
                    </DialogContent>
                </Dialog>
            )}

            {/* Inline reply for pending questions */}
            {isQuestion && isPending && onReply && (
                <div className="mt-2 pt-2 border-t border-amber-500/20">
                    <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs gap-1 border-amber-500/30 text-amber-600 hover:bg-amber-500/10 hover:text-amber-700"
                        onClick={() => onReply(comment.id)}
                    >
                        <Send className="size-3" /> Reply
                    </Button>
                </div>
            )}

            {/* Show linked reply inline */}
            {isQuestion && replyComment && (
                <div className="mt-2 pt-2 border-t border-emerald-500/20">
                    <div className="flex items-center gap-1.5 text-xs text-emerald-600 mb-1">
                        <CornerDownRight className="size-3" />
                        <span className="font-medium">{replyComment.authorLabel || parseActor(replyComment.authorEmail).display}</span>
                        <span className="text-muted-foreground font-mono">{formatDate(replyComment.createdAt)}</span>
                    </div>
                    <div className="pl-4">
                        <CollapsibleContent>
                            <MarkdownRenderer
                                text={replyComment.content}
                                className="text-sm text-foreground/80"
                            />
                        </CollapsibleContent>
                    </div>
                </div>
            )}
        </div>
    );
}

/** Compact metadata row — label on left, value on right */
function CompactRow({ label, children, noBorder }: { label: string; children: React.ReactNode; noBorder?: boolean }) {
    return (
        <div className={`flex items-start gap-2 py-1.5 ${noBorder ? '' : 'border-b border-border/30'}`}>
            <span className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold shrink-0 w-20 pt-0.5">
                {label}
            </span>
            <div className="flex-1 min-w-0">{children}</div>
        </div>
    );
}

/** Collapsible section for secondary information */
function CollapsibleSection({ label, children, defaultOpen = true }: { label: string; children: React.ReactNode; defaultOpen?: boolean }) {
    const [isOpen, setIsOpen] = useState(defaultOpen);
    return (
        <div className="border-b border-border/30">
            <button
                className="flex items-center gap-1.5 w-full py-1.5 text-[10px] text-muted-foreground/60 uppercase tracking-widest font-bold hover:text-muted-foreground transition-colors"
                onClick={() => setIsOpen(!isOpen)}
            >
                <ChevronRight className={`size-3 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                {label}
            </button>
            {isOpen && (
                <div className="pb-2 pl-1">
                    {children}
                </div>
            )}
        </div>
    );
}

function MutationItem({ mutation }: { mutation: any }) {
    const [isExpanded, setIsExpanded] = useState(false);

    const isComplex = ['result', 'input', 'output', 'context'].includes(mutation.fieldName);

    let parsedNewValue = null;
    let isJson = false;

    if (isComplex && mutation.newValue) {
        try {
            if (typeof mutation.newValue === 'string' && (mutation.newValue.startsWith('{') || mutation.newValue.startsWith('['))) {
                parsedNewValue = JSON.parse(mutation.newValue);
                isJson = true;
            } else if (typeof mutation.newValue === 'object') {
                parsedNewValue = mutation.newValue;
                isJson = true;
            }
        } catch { /* ignore */ }
    }

    const showCustomDisplay = isJson;

    return (
        <div className="relative pl-7">
            <div className={`absolute left-[1px] top-[5px] size-3 rounded-full border-2 border-background ${mutation.type === 'status_change' ? 'bg-blue-500' :
                mutation.type === 'assigned' ? 'bg-purple-500' :
                    mutation.type === 'created' ? 'bg-emerald-500' : 'bg-zinc-500'
                }`} />
            <div className="flex flex-col gap-0.5">
                <div className="flex items-baseline gap-2">
                    <span className="text-sm font-semibold">{mutation.actor}</span>
                    <span className="text-xs text-muted-foreground font-mono">{formatDate(mutation.date)}</span>
                </div>

                {showCustomDisplay ? (
                    <div className="text-sm text-foreground/80 leading-snug">
                        <div className="flex items-center gap-2">
                            <span className="font-medium text-muted-foreground">{mutation.fieldName} updated</span>
                            <Button variant="ghost" size="sm" className="h-5 px-2 text-[10px] text-blue-400 hover:text-blue-300 hover:bg-blue-400/10"
                                onClick={() => setIsExpanded(!isExpanded)}>
                                {isExpanded ? 'Collapse' : 'View Details'}
                            </Button>
                        </div>
                        {isExpanded && (
                            <div className="mt-2 rounded-md border border-border bg-[#0d1117] p-3 overflow-x-auto text-xs font-mono">
                                <JsonViewer data={parsedNewValue} />
                            </div>
                        )}
                    </div>
                ) : (
                    <div className="text-sm text-foreground/80 leading-snug">{mutation.description}</div>
                )}
            </div>
        </div>
    );
}

function JsonViewer({ data }: { data: any }) {
    if (typeof data !== 'object' || data === null) {
        return <span className="text-green-400 break-words">{String(data)}</span>;
    }

    if (Array.isArray(data)) {
        return (
            <div className="space-y-1">
                {data.map((item, i) => (
                    <div key={i} className="pl-4 border-l border-white/10">
                        <JsonViewer data={item} />
                    </div>
                ))}
            </div>
        );
    }

    return (
        <div className="space-y-1">
            {Object.entries(data).map(([key, value]) => (
                <div key={key} className="flex gap-2">
                    <span className="text-[#a5b4fc] font-semibold font-mono whitespace-nowrap">{key}:</span>
                    <div className="flex-1 min-w-0">
                        <JsonViewer data={value} />
                    </div>
                </div>
            ))}
        </div>
    );
}

function ExecutingTimer({ task }: { task: Task }) {
    const [now, setNow] = useState(Date.now());
    useEffect(() => {
        const id = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(id);
    }, []);

    // Elapsed from EXECUTING time-in-statuses + time since last status change
    const lastStatusMutation = [...task.mutations].reverse().find(m => m.fieldName === 'status' && m.newValue === 'EXECUTING');
    const startedAt = lastStatusMutation ? new Date(lastStatusMutation.date).getTime() : 0;
    const elapsed = startedAt ? now - startedAt : (task.timeInStatuses?.['EXECUTING'] || 0);

    // Last comment timestamp
    const lastComment = task.comments[task.comments.length - 1];
    const lastActivity = lastComment ? Math.floor((now - new Date(lastComment.createdAt).getTime()) / 1000) : null;

    return (
        <span className="text-[10px] font-mono text-blue-400 ml-auto flex items-center gap-2">
            <span>{formatDuration(elapsed)}</span>
            {lastActivity !== null && lastActivity < 3600 && (
                <span className="text-muted-foreground/60">upd {lastActivity}s ago</span>
            )}
        </span>
    );
}
