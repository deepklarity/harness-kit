import { useState, useMemo, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { Task, Member, Label, TaskComment } from '../types';
import { formatDate, formatDuration, getStatusColor } from '../utils/transformer';
import { parseActor } from '../services/harness/HarnessTimeService';
import { CountdownTimer } from './CountdownTimer';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
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
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { MarkdownRenderer } from './MarkdownRenderer';
import { MarkdownEditor } from './MarkdownEditor';
import { TraceViewer } from './TraceViewer';
import {
    Pencil, Search, Trash2, Eye, Code, FileText, FolderOpen,
    GitBranch, Package, Terminal, User, ChevronRight,
    HelpCircle, CornerDownRight, Send, ShieldCheck, Sparkles, Loader2,
    Copy, Check,
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

interface TaskDetailModalProps {
    task: Task;
    onClose: () => void;
    allMembers: Member[];
    allTasks?: Task[];
    memberMap?: Map<string, Member>;
    onUpdateAssignees: (taskId: string, memberIds: string[]) => void;
    onUpdateTask: (taskId: string, updates: Record<string, unknown>) => void;
    onSelectTask?: (taskId: string) => void;
    availableStatuses: string[];
    onDeleteTask?: (taskId: string) => void;
    availableLabels?: Label[];
    detailLoading?: boolean;
    onRefresh?: (taskId: string) => void;
}

const PRIORITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

const LABEL_COLORS = [
    '#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4',
    '#3b82f6', '#8b5cf6', '#ec4899', '#64748b',
];

const PRIORITY_COLORS: Record<string, string> = {
    CRITICAL: 'text-red-400',
    HIGH: 'text-orange-400',
    MEDIUM: 'text-blue-400',
    LOW: 'text-muted-foreground',
};

export function TaskDetailModal({
    task, onClose, allMembers, allTasks, memberMap, onUpdateAssignees, onUpdateTask, onSelectTask, availableStatuses, onDeleteTask, availableLabels, detailLoading, onRefresh
}: TaskDetailModalProps) {
    const service = useService();
    const { user: authUser } = useAuth();
    const { toast } = useToast();
    const [searchParams] = useSearchParams();
    const isExecuting = task.currentStatus === 'EXECUTING';
    const [isEditingAssignees, setIsEditingAssignees] = useState(false);
    const [editingField, setEditingField] = useState<string | null>(null);
    const [editValue, setEditValue] = useState<string>('');
    const [selectedAssignee, setSelectedAssignee] = useState<string | null>(null);
    const [assigneeSearch, setAssigneeSearch] = useState('');
    const [showRawJson, setShowRawJson] = useState(false);

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
    const [isEditingLabels, setIsEditingLabels] = useState(false);
    const [allLabels, setAllLabels] = useState<Label[]>([]);
    const [selectedLabels, setSelectedLabels] = useState<number[]>([]);
    const [labelSearch, setLabelSearch] = useState('');
    const [newLabelName, setNewLabelName] = useState('');
    const [newLabelColor, setNewLabelColor] = useState(LABEL_COLORS[0]);

    // Reflection state
    const [showReflectionModal, setShowReflectionModal] = useState(false);
    const [reflections, setReflections] = useState<ReflectionReport[]>([]);

    useEffect(() => {
        if (isEditingLabels) {
            setAllLabels(availableLabels || []);
            setSelectedLabels(task.labels?.map(l => l.id) || []);
        }
    }, [isEditingLabels, availableLabels, task.labels]);

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

    const startEditingLabels = () => setIsEditingLabels(true);

    const toggleLabel = (labelId: number) => {
        if (selectedLabels.includes(labelId)) {
            setSelectedLabels(prev => prev.filter(id => id !== labelId));
        } else {
            setSelectedLabels(prev => [...prev, labelId]);
        }
    };

    const handleCreateLabel = async () => {
        if (!newLabelName) return;
        try {
            const label = await service.createLabel(newLabelName, newLabelColor);
            setAllLabels(prev => [...prev, label]);
            setSelectedLabels(prev => [...prev, label.id]);
            setNewLabelName('');
            setNewLabelColor(LABEL_COLORS[0]);
        } catch (e) {
            console.error('Failed to create label', e);
        }
    };

    const handleSaveLabels = async () => {
        onUpdateTask(task.id, { labelIds: selectedLabels });
        setIsEditingLabels(false);
    };

    const filteredLabels = allLabels.filter(l =>
        l.name.toLowerCase().includes(labelSearch.toLowerCase())
    );

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
            harness: md.harness as string | undefined,
            branch: md.branch as string | undefined,
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

    const hasExecContext = !!(execContext.model || execContext.cwd || execContext.harness || execContext.branch || task.complexity || task.dependsOn?.length);

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
        onUpdateAssignees(task.id, selectedAssignee ? [selectedAssignee] : []);
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

    const handleSaveField = () => {
        if (!editingField) return;
        const updateKey = editingField === 'title' ? 'title' :
            editingField === 'description' ? 'description' :
                editingField === 'status' ? 'status' :
                    editingField === 'priority' ? 'priority' :
                        editingField === 'devEta' ? 'devEta' : null;
        if (updateKey) {
            const finalValue = updateKey === 'devEta' ? parseFloat(editValue) : editValue;
            onUpdateTask(task.id, { [updateKey]: finalValue });
        }
        setEditingField(null);
    };

    const filteredMembers = allMembers.filter(m =>
        m.fullName.toLowerCase().includes(assigneeSearch.toLowerCase()) ||
        m.username.toLowerCase().includes(assigneeSearch.toLowerCase())
    );

    const statusColor = getStatusColor(task.currentStatus);
    const statusEntries = Object.entries(task.timeInStatuses);
    const totalStatusTime = statusEntries.reduce((sum, [, ms]) => sum + ms, 0);

    return (
        <Dialog open onOpenChange={onClose}>
            <DialogContent className="sm:max-w-[90vw] w-[90vw] h-[90vh] flex flex-col p-0 overflow-hidden gap-0">
                {/* FIXED HEADER */}
                <DialogHeader className="shrink-0 px-4 pt-4 pb-2">
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
                        <div className="flex-1" />
                    </div>
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
                        </DialogTitle>
                    )}
                </DialogHeader>

                {/* SCROLLABLE BODY — flexbox so min-h-0 properly constrains each column */}
                <div className="flex-1 min-h-0 flex">

                    {/* LEFT COLUMN: Metadata — compact layout */}
                    <div className="w-[280px] shrink-0 border-r border-border/50 flex flex-col">
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

                            {/* Pending Question Banner */}
                            {!!task.metadata?.has_pending_question && (
                                <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-amber-500/10 border border-amber-500/20 mb-1">
                                    <HelpCircle className="size-3.5 text-amber-500 animate-pulse shrink-0" />
                                    <span className="text-[11px] font-medium text-amber-600">Agent waiting for reply</span>
                                </div>
                            )}
                            {isExecuting && (
                                <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-blue-500/10 border border-blue-500/20 mb-1">
                                    <Terminal className="size-3.5 text-blue-500 shrink-0" />
                                    <span className="text-[11px] font-medium text-blue-600">
                                        Execution active: status, assignee, and model are locked until stop.
                                    </span>
                                </div>
                            )}

                            {/* Assignee */}
                            <CompactRow label="Assignee">
                                {!isEditingAssignees ? (
                                    <div className="flex items-center gap-1.5 flex-wrap">
                                        {task.assignees.length > 0
                                            ? task.assignees.map((name, i) => {
                                                const member = memberMap?.get(task.assigneeIds[i]) ?? allMembers.find(m => m.id === task.assigneeIds[i]);
                                                return (
                                                    <div key={i} className="flex items-center gap-1.5 bg-secondary/60 rounded px-1.5 py-0.5">
                                                        <div className="size-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white"
                                                            style={{ background: member?.color || 'hsl(240, 60%, 50%)' }}>
                                                            {member?.initials || name.substring(0, 2).toUpperCase()}
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
                                                {filteredMembers.map(member => {
                                                    const isSelected = selectedAssignee === member.id;
                                                    return (
                                                        <div key={member.id}
                                                            className={`flex items-center gap-2 p-1.5 rounded cursor-pointer transition-colors ${isSelected ? 'bg-primary/15' : 'hover:bg-background/80'}`}
                                                            onClick={() => setSelectedAssignee(isSelected ? null : member.id)}>
                                                            <div className={`size-4 rounded-full border-2 flex items-center justify-center ${isSelected ? 'border-primary bg-primary' : 'border-muted-foreground/30'}`}>
                                                                {isSelected && <div className="size-1.5 rounded-full bg-white" />}
                                                            </div>
                                                            <div className="size-4 rounded-full flex items-center justify-center text-[8px] font-bold text-white"
                                                                style={{ background: member.color }}>{member.initials}</div>
                                                            <span className="text-xs font-medium">{member.fullName}</span>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                        <div className="flex gap-2 justify-end mt-1.5">
                                            <Button size="sm" variant="ghost" onClick={() => setIsEditingAssignees(false)} className="h-6 text-[10px] px-2">Cancel</Button>
                                            <Button size="sm" onClick={handleSaveAssignees} className="h-6 text-[10px] px-2">Save</Button>
                                        </div>
                                    </div>
                                )}
                            </CompactRow>

                            {/* Time Budget — inline */}
                            <CompactRow label="Time Budget">
                                {editingField === 'devEta' ? (
                                    <div className="flex gap-1.5">
                                        <Input type="number" min="0" step="0.5" value={editValue}
                                            onChange={e => setEditValue(e.target.value)} autoFocus className="h-7 text-xs w-20" />
                                        <Button size="sm" className="h-7 px-2 text-xs" onClick={handleSaveField}>OK</Button>
                                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => setEditingField(null)}>X</Button>
                                    </div>
                                ) : (
                                    <div className="flex items-center gap-1.5 cursor-pointer" onClick={() => startEditingField('devEta', task.devEta?.toString() || '')}>
                                        {task.devEta !== undefined ? (
                                            <>
                                                <span className="text-xs font-medium">{task.devEta}h</span>
                                                {task.remainingTimeMs !== undefined && (
                                                    <CountdownTimer remainingMs={task.remainingTimeMs} isRunning={!!task.isTimerRunning} />
                                                )}
                                            </>
                                        ) : (
                                            <span className="text-xs text-muted-foreground italic">None</span>
                                        )}
                                        <Pencil className="size-2.5 opacity-40" />
                                    </div>
                                )}
                            </CompactRow>

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
                                {!isEditingLabels ? (
                                    <div className="flex items-center gap-1.5 flex-wrap">
                                        {task.labels && task.labels.length > 0 ? (
                                            task.labels.map(label => (
                                                <Badge key={label.id} className="px-1.5 py-0 text-[10px] font-medium text-white border-0 h-5"
                                                    style={{ backgroundColor: label.color }}>
                                                    {label.name}
                                                </Badge>
                                            ))
                                        ) : (
                                            <span className="text-xs text-muted-foreground italic">None</span>
                                        )}
                                        <button className="opacity-40 hover:opacity-100 transition-opacity" onClick={startEditingLabels}>
                                            <Pencil className="size-3" />
                                        </button>
                                    </div>
                                ) : (
                                    <div className="bg-secondary/50 p-2.5 rounded-lg border border-border shadow-inner">
                                        <div className="space-y-2">
                                            <div className="relative">
                                                <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground" />
                                                <Input placeholder="Search labels..." value={labelSearch}
                                                    onChange={e => setLabelSearch(e.target.value)} autoFocus className="pl-7 h-7 text-xs bg-background" />
                                            </div>
                                            <ScrollArea className="max-h-[120px]">
                                                <div className="space-y-0.5">
                                                    {filteredLabels.map(label => (
                                                        <div key={label.id}
                                                            className="flex items-center gap-2 p-1 rounded hover:bg-background/80 cursor-pointer"
                                                            onClick={() => toggleLabel(label.id)}>
                                                            <Checkbox checked={selectedLabels.includes(label.id)} />
                                                            <div className="h-3 w-6 rounded" style={{ backgroundColor: label.color }} />
                                                            <span className="text-xs">{label.name}</span>
                                                        </div>
                                                    ))}
                                                    {filteredLabels.length === 0 && (
                                                        <div className="text-[10px] text-muted-foreground p-1">No matching labels.</div>
                                                    )}
                                                </div>
                                            </ScrollArea>
                                            <div className="border-t border-border/50 pt-1.5">
                                                <div className="flex gap-1.5 mb-1.5">
                                                    <Input placeholder="New label" value={newLabelName} onChange={e => setNewLabelName(e.target.value)} className="h-6 text-[10px] flex-1" />
                                                    <Button size="sm" disabled={!newLabelName} onClick={handleCreateLabel} className="h-6 text-[10px] px-2">+</Button>
                                                </div>
                                                <div className="flex gap-1 flex-wrap mb-1.5">
                                                    {LABEL_COLORS.map(c => (
                                                        <div key={c}
                                                            className={`size-3.5 rounded-full cursor-pointer ${newLabelColor === c ? 'ring-2 ring-primary ring-offset-1 ring-offset-background' : ''}`}
                                                            style={{ backgroundColor: c }}
                                                            onClick={() => setNewLabelColor(c)}
                                                        />
                                                    ))}
                                                </div>
                                            </div>
                                            <div className="flex gap-2 justify-end pt-1 border-t border-border/50">
                                                <Button size="sm" variant="ghost" onClick={() => setIsEditingLabels(false)} className="h-6 text-[10px] px-2">Cancel</Button>
                                                <Button size="sm" onClick={handleSaveLabels} className="h-6 text-[10px] px-2">Save</Button>
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </CompactRow>

                            {/* Created — inline */}
                            <CompactRow label="Created">
                                <span className="text-xs font-mono text-muted-foreground">{task.createdAt ? formatDate(task.createdAt) : '\u2014'}</span>
                            </CompactRow>

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
                                    </div>
                                </div>
                            )}

                            {/* Execution Context — collapsible */}
                            {hasExecContext && (
                                <CollapsibleSection
                                    label="Execution Context"
                                    icon={<Terminal className="size-3" />}
                                >
                                    {(execContext.model || assigneeModels.length > 0) && (
                                        <CompactRow label="Model" icon={<Package className="size-2.5 text-muted-foreground/60" />} noBorder>
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
                                                    <SelectTrigger className="h-6 text-[10px] font-mono">
                                                        <SelectValue placeholder="Select model..." />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {assigneeModels.map(m => (
                                                            <SelectItem key={m.name} value={m.name}>
                                                                <span className="font-mono">{m.name}</span>
                                                                {m.description && <span className="text-muted-foreground ml-1">— {m.description}</span>}
                                                            </SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                            ) : (
                                                <span className="text-[11px] font-mono font-medium text-primary/80 truncate">{execContext.model}</span>
                                            )}
                                        </CompactRow>
                                    )}

                                    {task.metadata?.routing_reasoning && (
                                        <CompactRow label="Routing" icon={<GitBranch className="size-2.5 text-muted-foreground/60" />} noBorder>
                                            <span className="text-[10px] font-mono text-muted-foreground/80 leading-snug">{task.metadata.routing_reasoning as string}</span>
                                        </CompactRow>
                                    )}

                                    {execContext.cwd && (
                                        <CompactRow label="CWD" icon={<FolderOpen className="size-2.5 text-muted-foreground/60" />} noBorder>
                                            <span className="text-[10px] font-mono text-muted-foreground break-all bg-secondary/50 px-1 py-0.5 rounded">{execContext.cwd}</span>
                                        </CompactRow>
                                    )}

                                    {execContext.harness && (
                                        <CompactRow label="Harness" noBorder>
                                            <span className="text-xs font-mono">{execContext.harness}</span>
                                        </CompactRow>
                                    )}

                                    {execContext.branch && (
                                        <CompactRow label="Branch" icon={<GitBranch className="size-2.5 text-muted-foreground/60" />} noBorder>
                                            <span className="text-xs font-mono">{execContext.branch}</span>
                                        </CompactRow>
                                    )}

                                    {task.complexity && (
                                        <CompactRow label="Complexity" noBorder>
                                            <Badge variant="outline" className="text-[10px] font-semibold uppercase h-5 px-1.5">{task.complexity}</Badge>
                                        </CompactRow>
                                    )}

                                    {task.dependsOn && task.dependsOn.length > 0 && (
                                        <CompactRow label="Depends On" noBorder>
                                            <div className="space-y-1">
                                                {task.dependsOn.map(dep => {
                                                    const depTask = taskMap.get(dep);
                                                    const isClickable = !!(depTask && onSelectTask);
                                                    return (
                                                        <div key={dep}
                                                            className={`flex items-center gap-1.5 text-xs bg-secondary/50 rounded px-2 py-1 ${isClickable ? 'cursor-pointer hover:bg-secondary/80 transition-colors' : ''}`}
                                                            onClick={isClickable ? () => onSelectTask!(depTask!.id) : undefined}>
                                                            <span className="text-muted-foreground font-mono text-[10px] shrink-0">#{dep}</span>
                                                            <span className={`font-medium truncate ${isClickable ? 'text-primary hover:underline' : ''}`}>{depTask?.title || depTask?.name || `Task ${dep}`}</span>
                                                            {depTask && (
                                                                <Badge className="ml-auto shrink-0 text-[9px] px-1 py-0 border-0" style={{ background: `${getStatusColor(depTask.currentStatus)}20`, color: getStatusColor(depTask.currentStatus) }}>
                                                                    {depTask.currentStatus}
                                                                </Badge>
                                                            )}
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </CompactRow>
                                    )}

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
                                    icon={<Terminal className="size-3" />}
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
                        <div className="px-4 py-3">
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

            {/* Reflection Modal */}
            {showReflectionModal && (
                <ReflectionModal
                    taskId={task.id}
                    taskIdShort={task.idShort}
                    onClose={() => setShowReflectionModal(false)}
                    onSubmit={handleTriggerReflection}
                />
            )}
        </Dialog>
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

    useEffect(() => {
        if (traceCopyState === 'idle') return;
        const t = window.setTimeout(() => setTraceCopyState('idle'), 1400);
        return () => window.clearTimeout(t);
    }, [traceCopyState]);

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
                    <span className="text-sm font-semibold">
                        {comment.authorLabel || actor.display}
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
                <TraceViewer traceText={traceText} />
            )}
            {summary && (
                isSummary ? (
                    <div className="mt-1 rounded-md border border-amber-400/20 bg-card/60 p-3 overflow-x-auto">
                        <pre className="text-xs font-mono whitespace-pre-wrap leading-relaxed text-foreground/80">{summary}</pre>
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
                            <div className="text-sm whitespace-pre-wrap leading-relaxed text-foreground/80">
                                {failureDetails.displaySummary}
                            </div>
                        )}
                    </>
                )
            )}
            {(traceData || failureDetails.failureDebug) && (
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
                    <DialogContent className="max-w-[90vw] max-h-[90vh] p-0 bg-black/95 border-border/20 overflow-hidden flex items-center justify-center">
                        <DialogHeader className="sr-only">
                            <DialogTitle>{lightboxImage.filename}</DialogTitle>
                        </DialogHeader>
                        <img
                            src={lightboxImage.url}
                            alt={lightboxImage.filename}
                            className="max-w-full max-h-[85vh] object-contain"
                        />
                        <span className="absolute bottom-3 left-1/2 -translate-x-1/2 text-xs text-white/60 font-mono bg-black/60 px-3 py-1 rounded-full">
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
                    <div className="text-sm text-foreground/80 pl-4">{replyComment.content}</div>
                </div>
            )}
        </div>
    );
}

/** Compact metadata row — label on left, value on right */
function CompactRow({ label, icon, children, noBorder }: { label: string; icon?: React.ReactNode; children: React.ReactNode; noBorder?: boolean }) {
    return (
        <div className={`flex items-start gap-2 py-1 ${noBorder ? '' : 'border-b border-border/30'}`}>
            <span className="text-[10px] text-muted-foreground/70 uppercase tracking-wider font-semibold shrink-0 w-[64px] pt-0.5 flex items-center gap-1">
                {icon}
                {label}
            </span>
            <div className="flex-1 min-w-0">{children}</div>
        </div>
    );
}

/** Collapsible section for secondary information */
function CollapsibleSection({ label, icon, children }: { label: string; icon?: React.ReactNode; children: React.ReactNode }) {
    const [isOpen, setIsOpen] = useState(false);
    return (
        <div className="border-b border-border/30">
            <button
                className="flex items-center gap-1.5 w-full py-1.5 text-[10px] text-muted-foreground/60 uppercase tracking-widest font-bold hover:text-muted-foreground transition-colors"
                onClick={() => setIsOpen(!isOpen)}
            >
                <ChevronRight className={`size-3 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                {icon}
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
