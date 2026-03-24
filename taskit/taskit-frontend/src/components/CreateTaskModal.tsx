import { useEffect, useMemo, useState, useCallback } from 'react';
import { format } from 'date-fns';
import { useToast } from '@/hooks/use-toast';
import type { AgentConfig, Board, Label as LabelType, ModelInfo, Task, TaskPreset, PresetCategory } from '../types';
import { useService } from '../contexts/ServiceContext';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollArea } from '@/components/ui/scroll-area';
import { ChevronDown, Plus, X, Bot, User, AlertTriangle } from 'lucide-react';
import { PresetPicker } from './PresetPicker';
import { ImageDropZone } from './ImageDropZone';

const WEEKDAY_KEYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'] as const;

export interface ApiUser {
    id: number;
    name: string;
    email: string;
    role?: 'HUMAN' | 'AGENT' | 'ADMIN';
    availableModels?: ModelInfo[];
}

interface CreateTaskModalProps {
    boards: Board[];
    dependencyTasksByBoard?: Record<string, Task[]>;
    dependencyTasksLoadingByBoard?: Record<string, boolean>;
    onEnsureDependencyTasks?: (boardId: string, force?: boolean) => Promise<void> | void;
    defaultBoardId?: string;
    users: ApiUser[];
    onClose: () => void;
    onCreate: (
        boardId: string,
        title: string,
        description: string,
        priority: string,
        assigneeId: number,
        modelName?: string,
        devEta?: number,
        labelIds?: number[],
        dependsOn?: string[],
        workingDir?: string
    ) => Promise<string | void>;
    onCreateSchedule?: (payload: Record<string, unknown>) => Promise<void>;
    availableLabels?: LabelType[];
}

export function CreateTaskModal({
    boards,
    dependencyTasksByBoard,
    dependencyTasksLoadingByBoard,
    onEnsureDependencyTasks,
    defaultBoardId,
    users: initialUsers,
    onClose,
    onCreate,
    onCreateSchedule,
    availableLabels,
}: CreateTaskModalProps) {
    const service = useService();
    const { toast } = useToast();
    const [boardId, setBoardId] = useState(defaultBoardId || (boards[0]?.id || ''));
    const [title, setTitle] = useState('');
    const [description, setDescription] = useState('');
    const [priority, setPriority] = useState('MEDIUM');
    const [selectedUserId, setSelectedUserId] = useState<string>(initialUsers.length > 0 ? String(initialUsers[0].id) : '');
    const [selectedModelName, setSelectedModelName] = useState<string>('');
    const [devEta, setDevEta] = useState<string>('');
    const [loading, setLoading] = useState(false);
    const [showExtra, setShowExtra] = useState(false);
    const [stagedFiles, setStagedFiles] = useState<File[]>([]);
    const [uploadingScreenshots, setUploadingScreenshots] = useState(false);
    const [selectedDependencyIds, setSelectedDependencyIds] = useState<string[]>([]);
    const [dependencyDialogOpen, setDependencyDialogOpen] = useState(false);
    const [createMode, setCreateMode] = useState<'TASK' | 'SCHEDULE'>('TASK');
    const [scheduleKind, setScheduleKind] = useState<'ONE_TIME' | 'RECURRING'>('ONE_TIME');
    const [scheduleAt, setScheduleAt] = useState('');
    const [recurrenceFreq, setRecurrenceFreq] = useState<'DAILY' | 'WEEKLY' | 'MONTHLY'>('WEEKLY');
    const [recurrenceInterval, setRecurrenceInterval] = useState('1');
    const [recurrenceWeekdays, setRecurrenceWeekdays] = useState<string[]>([]);
    const [recurrenceMonthday, setRecurrenceMonthday] = useState('1');

    const [users, setUsers] = useState<ApiUser[]>(initialUsers);
    const [presets, setPresets] = useState<TaskPreset[]>([]);
    const [presetCategories, setPresetCategories] = useState<PresetCategory[]>([]);
    const [selectedPreset, setSelectedPreset] = useState<TaskPreset | null>(null);
    const [agentConfigs, setAgentConfigs] = useState<AgentConfig[]>([]);
    const [allLabels, setAllLabels] = useState<LabelType[]>(availableLabels || []);
    const [selectedLabelIds, setSelectedLabelIds] = useState<number[]>([]);
    const [showLabelPicker, setShowLabelPicker] = useState(false);
    const [newLabelName, setNewLabelName] = useState('');
    const [newLabelColor, setNewLabelColor] = useState('#3b82f6');
    const [showNewUser, setShowNewUser] = useState(false);
    const [newUserName, setNewUserName] = useState('');
    const [newUserEmail, setNewUserEmail] = useState('');
    const [creatingUser, setCreatingUser] = useState(false);

    const LABEL_COLORS = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899', '#64748b'];

    useEffect(() => {
        service.fetchPresets()
            .then(data => {
                setPresets(data.presets);
                setPresetCategories(data.categories);
            })
            .catch(() => {});
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const handlePresetSelect = (preset: TaskPreset) => {
        setSelectedPreset(preset);
        setTitle(preset.title);
        setDescription(preset.description);
        if (preset.suggested_priority) setPriority(preset.suggested_priority);
    };

    const handlePresetClear = () => {
        setSelectedPreset(null);
        setTitle('');
        setDescription('');
        setPriority('MEDIUM');
    };

    const selectedBoardObj = useMemo(() => boards.find(b => b.id === boardId), [boards, boardId]);
    const localTimezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', []);
    const dependencyBoardTasks = useMemo(
        () => (boardId ? dependencyTasksByBoard?.[boardId] || [] : []),
        [boardId, dependencyTasksByBoard],
    );
    const dependencyTasksLoading = boardId ? !!dependencyTasksLoadingByBoard?.[boardId] : false;
    const activeDependencyTasks = useMemo(
        () => dependencyBoardTasks.filter(task => task.currentStatus === 'TODO' || task.currentStatus === 'IN_PROGRESS'),
        [dependencyBoardTasks],
    );

    const loadAgentConfigs = useCallback(async (board: Board) => {
        if (!board.odinInitialized) {
            setAgentConfigs([]);
            return;
        }
        try {
            const configs = await service.fetchBoardAgents(board.id);
            setAgentConfigs(configs);
        } catch {
            setAgentConfigs([]);
        }
    }, [service]);

    useEffect(() => {
        if (selectedBoardObj) {
            void loadAgentConfigs(selectedBoardObj);
        }
    }, [selectedBoardObj, loadAgentConfigs]);

    useEffect(() => {
        if (!boardId || !onEnsureDependencyTasks) return;
        void onEnsureDependencyTasks(boardId);
    }, [boardId, onEnsureDependencyTasks]);

    const isAgentUser = (u: ApiUser) => u.email.endsWith('@odin.agent') || u.role === 'AGENT';
    const disabledAgentEmails = useMemo(
        () => new Set(agentConfigs.filter(a => !a.enabled).map(a => `${a.name}@odin.agent`)),
        [agentConfigs],
    );

    const boardUsers = useMemo(() => {
        if (!selectedBoardObj) return users;
        const memberIds = new Set(selectedBoardObj.memberIds);
        const onBoard = users.filter(u => memberIds.has(String(u.id)));
        const disabledAgents = users.filter(
            u => disabledAgentEmails.has(u.email) && !memberIds.has(String(u.id))
        );
        return [...onBoard, ...disabledAgents];
    }, [users, selectedBoardObj, disabledAgentEmails]);

    useEffect(() => {
        const enabledBoardUsers = boardUsers.filter(u => !disabledAgentEmails.has(u.email));
        const currentValid = enabledBoardUsers.some(u => String(u.id) === selectedUserId);
        if (!currentValid && enabledBoardUsers.length > 0) {
            setSelectedUserId(String(enabledBoardUsers[0].id));
        }
    }, [boardId, boardUsers, disabledAgentEmails, selectedUserId]);

    useEffect(() => {
        const allowed = new Set(activeDependencyTasks.map(task => task.id));
        setSelectedDependencyIds(prev => prev.filter(id => allowed.has(id)));
    }, [activeDependencyTasks]);

    const selectedAssignee = useMemo(
        () => boardUsers.find(u => String(u.id) === selectedUserId),
        [boardUsers, selectedUserId],
    );
    const availableModels = useMemo(
        () => selectedAssignee?.availableModels || [],
        [selectedAssignee],
    );
    const selectedModel = useMemo(
        () => availableModels.find(m => m.name === selectedModelName),
        [availableModels, selectedModelName],
    );
    const selectedModelSupportsImageInput = Boolean(selectedModel?.supports_image_input);
    const hasImageModelMismatch = createMode === 'TASK' && stagedFiles.length > 0 && !selectedModelSupportsImageInput;

    const parsedScheduleDate = useMemo(() => {
        if (!scheduleAt) return null;
        const parsed = new Date(scheduleAt);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }, [scheduleAt]);

    const minScheduleAt = useMemo(() => {
        const now = new Date();
        const offsetMs = now.getTimezoneOffset() * 60000;
        return new Date(now.getTime() - offsetMs).toISOString().slice(0, 16);
    }, []);

    const selectedScheduleWeekday = useMemo(() => {
        if (!parsedScheduleDate) return null;
        return WEEKDAY_KEYS[parsedScheduleDate.getDay()];
    }, [parsedScheduleDate]);

    useEffect(() => {
        if (!parsedScheduleDate) return;
        if (scheduleKind === 'RECURRING' && recurrenceFreq === 'WEEKLY' && recurrenceWeekdays.length === 0 && selectedScheduleWeekday) {
            setRecurrenceWeekdays([selectedScheduleWeekday]);
        }
        if (scheduleKind === 'RECURRING' && recurrenceFreq === 'MONTHLY') {
            const day = String(parsedScheduleDate.getDate());
            if (recurrenceMonthday !== day) {
                setRecurrenceMonthday(day);
            }
        }
    }, [parsedScheduleDate, scheduleKind, recurrenceFreq, recurrenceWeekdays.length, selectedScheduleWeekday, recurrenceMonthday]);

    const scheduleValidationError = useMemo(() => {
        if (createMode !== 'SCHEDULE' || scheduleKind !== 'RECURRING' || !parsedScheduleDate) return '';
        if (recurrenceFreq === 'WEEKLY') {
            if (!selectedScheduleWeekday) return '';
            if (recurrenceWeekdays.length === 0) return 'Select at least one weekday for a weekly recurrence.';
            if (!recurrenceWeekdays.includes(selectedScheduleWeekday)) {
                return `The first scheduled date is ${selectedScheduleWeekday}. Weekly recurrence must include ${selectedScheduleWeekday}.`;
            }
        }
        if (recurrenceFreq === 'MONTHLY') {
            const pickedDay = parsedScheduleDate.getDate();
            const configuredDay = Math.max(1, Number(recurrenceMonthday) || 1);
            if (pickedDay !== configuredDay) {
                return `The first scheduled date is day ${pickedDay}. Monthly recurrence must use day ${pickedDay}.`;
            }
        }
        return '';
    }, [createMode, scheduleKind, parsedScheduleDate, recurrenceFreq, recurrenceWeekdays, selectedScheduleWeekday, recurrenceMonthday]);

    const schedulePreview = useMemo(() => {
        if (createMode !== 'SCHEDULE') return '';
        if (!scheduleAt) return 'Pick a date and time to see exactly how this schedule will run.';
        const parsed = parsedScheduleDate;
        if (!parsed) return 'Pick a valid date and time to see exactly how this schedule will run.';
        const timeLabel = format(parsed, "MMM d, yyyy 'at' h:mm a");

        if (scheduleKind === 'ONE_TIME') {
            return `Runs once on ${timeLabel} in ${localTimezone}.`;
        }

        const interval = Math.max(1, Number(recurrenceInterval) || 1);
        if (recurrenceFreq === 'DAILY') {
            return interval === 1
                ? `Runs every day at ${format(parsed, 'h:mm a')} in ${localTimezone}.`
                : `Runs every ${interval} days at ${format(parsed, 'h:mm a')} in ${localTimezone}.`;
        }
        if (recurrenceFreq === 'WEEKLY') {
            const days = recurrenceWeekdays.length > 0
                ? recurrenceWeekdays.join(', ')
                : (selectedScheduleWeekday || format(parsed, 'EEE').toUpperCase());
            return interval === 1
                ? `Runs every week on ${days} at ${format(parsed, 'h:mm a')} in ${localTimezone}.`
                : `Runs every ${interval} weeks on ${days} at ${format(parsed, 'h:mm a')} in ${localTimezone}.`;
        }

        const monthDay = Math.max(1, Number(recurrenceMonthday) || parsed.getDate() || 1);
        return interval === 1
            ? `Runs every month on day ${monthDay} at ${format(parsed, 'h:mm a')} in ${localTimezone}.`
            : `Runs every ${interval} months on day ${monthDay} at ${format(parsed, 'h:mm a')} in ${localTimezone}.`;
    }, [createMode, scheduleAt, parsedScheduleDate, scheduleKind, localTimezone, recurrenceFreq, recurrenceInterval, recurrenceWeekdays, selectedScheduleWeekday, recurrenceMonthday]);

    useEffect(() => {
        if (availableModels.length === 0) {
            setSelectedModelName('');
            return;
        }
        const defaultModel = availableModels.find(m => m.is_default)?.name || availableModels[0].name;
        setSelectedModelName(defaultModel);
    }, [selectedUserId, availableModels]);

    const toggleLabel = (id: number) => {
        setSelectedLabelIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
    };

    const handleCreateLabel = async () => {
        if (!newLabelName.trim()) return;
        try {
            const label = await service.createLabel(newLabelName.trim(), newLabelColor);
            setAllLabels(prev => [...prev, label]);
            setSelectedLabelIds(prev => [...prev, label.id]);
            setNewLabelName('');
        } catch {
            toast({ title: 'Error', description: 'Failed to create label.', variant: 'destructive' });
        }
    };

    const handleCreateUser = async () => {
        if (!newUserName.trim() || !newUserEmail.trim()) return;
        setCreatingUser(true);
        try {
            const user = await service.createUser(newUserName.trim(), newUserEmail.trim());
            const createdUser: ApiUser = {
                id: user.id,
                name: user.name,
                email: user.email,
                availableModels: user.available_models || [],
            };
            setUsers(prev => [...prev, createdUser]);
            setSelectedUserId(String(user.id));
            setShowNewUser(false);
            setNewUserName('');
            setNewUserEmail('');
        } catch {
            toast({
                title: 'Error',
                description: 'Failed to create user. Email may already exist.',
                variant: 'destructive',
            });
        } finally {
            setCreatingUser(false);
        }
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!title.trim() || !description.trim() || !boardId || !selectedUserId) return;
        if (createMode === 'SCHEDULE' && !scheduleAt) return;
        if (scheduleValidationError) {
            toast({
                title: 'Invalid schedule',
                description: scheduleValidationError,
                variant: 'destructive',
            });
            return;
        }
        if (hasImageModelMismatch) {
            toast({
                title: 'Model does not support images',
                description: 'Choose a model with image input support or remove the attached images.',
                variant: 'destructive',
            });
            return;
        }

        setLoading(true);
        let taskId: string | undefined;
        try {
            const etaNum = devEta ? parseFloat(devEta) : undefined;
            if (createMode === 'SCHEDULE' && onCreateSchedule) {
                const recurrenceRule = scheduleKind === 'RECURRING' ? {
                    freq: recurrenceFreq,
                    interval: Number(recurrenceInterval) || 1,
                    by_weekday: recurrenceFreq === 'WEEKLY' ? recurrenceWeekdays : [],
                    by_monthday: recurrenceFreq === 'MONTHLY' ? [Number(recurrenceMonthday) || 1] : [],
                    end_mode: 'NEVER',
                } : undefined;
                await onCreateSchedule({
                    board_id: Number(boardId),
                    kind: scheduleKind,
                    timezone: localTimezone,
                    starts_at_local: scheduleAt,
                    created_by_user_id: Number(selectedUserId),
                    template: {
                        title,
                        description,
                        priority,
                        assignee_id: Number(selectedUserId),
                        model_name: selectedModelName || undefined,
                        label_ids: selectedLabelIds,
                        depends_on: selectedDependencyIds,
                        dev_eta_seconds: etaNum !== undefined ? Math.round(etaNum * 3600) : undefined,
                        metadata: {},
                    },
                    recurrence_rule: recurrenceRule,
                });
                onClose();
                return;
            }

            const result = await onCreate(
                boardId,
                title,
                description,
                priority,
                Number(selectedUserId),
                selectedModelName || undefined,
                etaNum,
                selectedLabelIds.length > 0 ? selectedLabelIds : undefined,
                selectedDependencyIds.length > 0 ? selectedDependencyIds : undefined,
                undefined,
            );
            taskId = result || undefined;
        } catch {
            toast({
                title: 'Error',
                description: createMode === 'SCHEDULE' ? 'Failed to create schedule' : 'Failed to create task',
                variant: 'destructive',
            });
            setLoading(false);
            return;
        }
        setLoading(false);

        if (stagedFiles.length > 0 && taskId) {
            setUploadingScreenshots(true);
            try {
                await service.uploadScreenshots(taskId, stagedFiles);
            } catch {
                toast({
                    title: 'Warning',
                    description: 'Task created, but reference images failed to upload.',
                    variant: 'destructive',
                });
            } finally {
                setUploadingScreenshots(false);
            }
        }

        onClose();
    };

    return (
        <Dialog open onOpenChange={onClose}>
            <DialogContent className="max-h-[90vh] overflow-hidden p-0 sm:max-w-[760px]">
                <DialogHeader>
                    <div className="px-6 pt-6">
                        <DialogTitle>Create New Task</DialogTitle>
                    </div>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="flex flex-col">
                    <ScrollArea className="max-h-[calc(90vh-144px)] px-6 pb-4">
                        <div className="flex flex-col gap-4 pb-4">
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="flex flex-col gap-2">
                                    <Label>Create Mode</Label>
                                    <Select value={createMode} onValueChange={(value) => setCreateMode(value as 'TASK' | 'SCHEDULE')}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="TASK">Run Now</SelectItem>
                                            <SelectItem value="SCHEDULE">Schedule</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>

                                <div className="flex flex-col gap-2">
                                    <Label>Target Board</Label>
                                    <Select value={boardId} onValueChange={setBoardId}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            {boards.map(b => (
                                                <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>

                            {createMode === 'SCHEDULE' && (
                                <div className="rounded-lg border bg-muted/20 p-3">
                                    <div className="mb-2 flex items-start justify-between gap-3">
                                        <div>
                                            <p className="text-sm font-medium">Schedule Settings</p>
                                            <p className="text-[11px] text-muted-foreground">
                                                This task will stay out of the board until its scheduled release time in your local timezone.
                                            </p>
                                        </div>
                                        <Badge variant="outline" className="text-[10px]">{localTimezone}</Badge>
                                    </div>

                                    <div className="grid gap-3 sm:grid-cols-[160px_minmax(0,1fr)]">
                                        <div className="flex flex-col gap-2">
                                            <Label>Schedule Type</Label>
                                            <Select value={scheduleKind} onValueChange={(value) => setScheduleKind(value as 'ONE_TIME' | 'RECURRING')}>
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="ONE_TIME">One-time</SelectItem>
                                                    <SelectItem value="RECURRING">Recurring</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>

                                        <div className="flex flex-col gap-2">
                                            <Label>Scheduled Time</Label>
                                            <Input
                                                type="datetime-local"
                                                value={scheduleAt}
                                                min={minScheduleAt}
                                                onChange={(e) => setScheduleAt(e.target.value)}
                                                required={createMode === 'SCHEDULE'}
                                            />
                                        </div>
                                    </div>

                                    {scheduleKind === 'RECURRING' && (
                                        <div className="mt-3 space-y-3 rounded-md border bg-background p-3">
                                            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
                                                <div className="flex flex-col gap-2">
                                                    <Label>Frequency</Label>
                                                    <Select value={recurrenceFreq} onValueChange={(value) => setRecurrenceFreq(value as 'DAILY' | 'WEEKLY' | 'MONTHLY')}>
                                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="DAILY">Daily</SelectItem>
                                                            <SelectItem value="WEEKLY">Weekly</SelectItem>
                                                            <SelectItem value="MONTHLY">Monthly</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                </div>

                                                <div className="flex flex-col gap-2">
                                                    <Label>Interval</Label>
                                                    <Input type="number" min="1" value={recurrenceInterval} onChange={(e) => setRecurrenceInterval(e.target.value)} />
                                                </div>
                                            </div>

                                            {recurrenceFreq === 'WEEKLY' && (
                                                <div className="flex flex-col gap-2">
                                                    <Label>Weekdays</Label>
                                                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-7">
                                                        {['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'].map(day => (
                                                            <label key={day} className="flex items-center gap-2 rounded-md border px-2.5 py-2 text-xs">
                                                                <Checkbox
                                                                    checked={recurrenceWeekdays.includes(day)}
                                                                    onCheckedChange={(checked) => setRecurrenceWeekdays(prev => checked ? [...prev, day] : prev.filter(item => item !== day))}
                                                                />
                                                                {day}
                                                            </label>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}

                                            {recurrenceFreq === 'MONTHLY' && (
                                                <div className="flex flex-col gap-2">
                                                    <Label>Day Of Month</Label>
                                                    <Input className="max-w-[160px]" type="number" min="1" max="31" value={recurrenceMonthday} onChange={(e) => setRecurrenceMonthday(e.target.value)} />
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    <div className="mt-3 rounded-md border border-dashed bg-background/70 px-3 py-2 text-xs text-muted-foreground">
                                        {schedulePreview}
                                    </div>
                                    {scheduleValidationError ? (
                                        <div className="mt-2 text-xs text-destructive">{scheduleValidationError}</div>
                                    ) : null}
                                </div>
                            )}

                            <div className="flex flex-col gap-2">
                                <div className="flex items-center justify-between gap-2">
                                    <Label>Dependencies</Label>
                                    <Button type="button" size="sm" variant="outline" className="h-7 text-xs" onClick={() => setDependencyDialogOpen(true)}>
                                        Add Dependencies
                                    </Button>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Only active tasks in To Do or In Progress can be used as new dependencies.
                                </p>
                                {selectedDependencyIds.length > 0 ? (
                                    <div className="flex flex-wrap gap-1.5">
                                        {selectedDependencyIds.map(depId => {
                                            const depTask = activeDependencyTasks.find(task => task.id === depId);
                                            return (
                                                <Badge key={depId} variant="outline" className="gap-1 text-[10px]">
                                                    <span className="font-mono">#{depTask?.idShort || depId}</span>
                                                    <span>{depTask?.title || depTask?.name || `Task ${depId}`}</span>
                                                </Badge>
                                            );
                                        })}
                                    </div>
                                ) : (
                                    <p className="text-xs text-muted-foreground italic">No dependencies selected.</p>
                                )}
                            </div>

                            {presets.length > 0 && (
                                <PresetPicker
                                    presets={presets}
                                    categories={presetCategories}
                                    selectedPreset={selectedPreset}
                                    onSelect={handlePresetSelect}
                                    onClear={handlePresetClear}
                                />
                            )}

                            <div className="flex flex-col gap-2">
                                <Label>Title</Label>
                                <Input required value={title} onChange={e => setTitle(e.target.value)} placeholder="Brief task title" />
                            </div>

                            <div className="flex flex-col gap-2">
                                <Label>Task Description</Label>
                                <Textarea required value={description} onChange={e => setDescription(e.target.value)} placeholder="What needs to be done?" className="min-h-[90px] max-h-[220px]" />
                            </div>

                            {createMode === 'TASK' && (
                                <div className="flex flex-col gap-2">
                                    <Label>Reference Images <span className="text-muted-foreground font-normal">(optional)</span></Label>
                                    <ImageDropZone files={stagedFiles} onFilesChange={setStagedFiles} />
                                    {hasImageModelMismatch && (
                                        <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-2.5 py-2 text-xs text-amber-900">
                                            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                                            <span>
                                                The selected model does not support image input. Choose an image-capable model or remove the attached images.
                                            </span>
                                        </div>
                                    )}
                                </div>
                            )}

                            <div className="flex flex-col gap-2">
                                <Label>Assignee</Label>
                                {showNewUser ? (
                                    <div className="flex flex-col gap-2">
                                        <Input value={newUserName} onChange={e => setNewUserName(e.target.value)} placeholder="Name" />
                                        <Input type="email" value={newUserEmail} onChange={e => setNewUserEmail(e.target.value)} placeholder="Email" />
                                        <div className="flex gap-2">
                                            <Button type="button" size="sm" className="flex-1" disabled={creatingUser || !newUserName.trim() || !newUserEmail.trim()} onClick={handleCreateUser}>
                                                {creatingUser ? 'Creating...' : 'Add User'}
                                            </Button>
                                            <Button type="button" size="sm" variant="outline" onClick={() => setShowNewUser(false)}>Cancel</Button>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="flex gap-2">
                                        <Select value={selectedUserId} onValueChange={setSelectedUserId}>
                                            <SelectTrigger className="flex-1"><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                {boardUsers.map(u => {
                                                    const agent = isAgentUser(u);
                                                    const disabled = disabledAgentEmails.has(u.email);
                                                    return (
                                                        <SelectItem key={u.id} value={String(u.id)} disabled={disabled} className={disabled ? 'opacity-40' : ''}>
                                                            <span className="flex items-center gap-2">
                                                                {agent ? (
                                                                    <Bot className="size-3.5 text-blue-500 shrink-0" />
                                                                ) : (
                                                                    <User className="size-3.5 text-muted-foreground shrink-0" />
                                                                )}
                                                                <span className={disabled ? 'line-through' : ''}>{u.name}</span>
                                                                {disabled && <span className="text-[10px] text-muted-foreground">disabled</span>}
                                                            </span>
                                                        </SelectItem>
                                                    );
                                                })}
                                            </SelectContent>
                                        </Select>
                                        <Button type="button" variant="outline" size="icon" onClick={() => setShowNewUser(true)} title="Create new user">
                                            <Plus className="size-4" />
                                        </Button>
                                    </div>
                                )}
                            </div>

                            <button
                                type="button"
                                className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors py-1"
                                onClick={() => setShowExtra(!showExtra)}
                            >
                                <ChevronDown className={`size-3.5 transition-transform ${showExtra ? 'rotate-0' : '-rotate-90'}`} />
                                Extra settings
                                {(priority !== 'MEDIUM' || selectedLabelIds.length > 0 || devEta) && (
                                    <span className="text-[10px] bg-muted px-1.5 py-0.5 rounded">customized</span>
                                )}
                            </button>

                            {showExtra && (
                                <div className="flex flex-col gap-4 pl-2 border-l-2 border-muted">
                                    <div className="flex flex-col gap-2">
                                        <Label>Model</Label>
                                        {availableModels.length > 0 ? (
                                            <Select value={selectedModelName} onValueChange={setSelectedModelName}>
                                                <SelectTrigger><SelectValue placeholder="Select model..." /></SelectTrigger>
                                                <SelectContent>
                                                    {availableModels.map(model => (
                                                        <SelectItem key={model.name} value={model.name}>
                                                            <span className="font-mono">{model.name}</span>
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        ) : (
                                            <p className="text-xs text-muted-foreground">
                                                No models configured for this assignee — can be set later.
                                            </p>
                                        )}
                                    </div>

                                    <div className="flex flex-col gap-2">
                                        <Label>Dev ETA (Hours)</Label>
                                        <Input type="number" min="0" step="0.5" value={devEta} onChange={e => setDevEta(e.target.value)} placeholder="e.g. 5" />
                                    </div>

                                    <div className="flex flex-col gap-2">
                                        <Label>Priority</Label>
                                        <div className="flex gap-2">
                                            {['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map(p => (
                                                <Button key={p} type="button" variant={priority === p ? 'default' : 'outline'} size="sm" className="flex-1" onClick={() => setPriority(p)}>
                                                    {p}
                                                </Button>
                                            ))}
                                        </div>
                                    </div>

                                    <div className="flex flex-col gap-2">
                                        <Label>Labels</Label>
                                        <div className="flex flex-wrap gap-1.5 min-h-[32px] items-center">
                                            {selectedLabelIds.map(id => {
                                                const label = allLabels.find(l => l.id === id);
                                                if (!label) return null;
                                                return (
                                                    <Badge key={id} className="gap-1 pr-1 text-xs text-white border-0" style={{ backgroundColor: label.color }}>
                                                        {label.name}
                                                        <button type="button" onClick={() => toggleLabel(id)} className="hover:opacity-70">
                                                            <X className="size-3" />
                                                        </button>
                                                    </Badge>
                                                );
                                            })}
                                            <Button type="button" variant="outline" size="sm" className="h-6 text-xs px-2" onClick={() => setShowLabelPicker(!showLabelPicker)}>
                                                <Plus className="size-3 mr-1" /> {showLabelPicker ? 'Close' : 'Add Labels'}
                                            </Button>
                                        </div>
                                        {showLabelPicker && (
                                            <div className="border rounded-lg p-3 bg-secondary/50 space-y-2">
                                                <ScrollArea className="max-h-[120px]">
                                                    <div className="space-y-1">
                                                        {allLabels.map(label => (
                                                            <div key={label.id} className="flex items-center gap-2 p-1 rounded hover:bg-background/80 cursor-pointer" onClick={() => toggleLabel(label.id)}>
                                                                <Checkbox checked={selectedLabelIds.includes(label.id)} />
                                                                <div className="h-3 w-6 rounded" style={{ backgroundColor: label.color }} />
                                                                <span className="text-sm">{label.name}</span>
                                                            </div>
                                                        ))}
                                                        {allLabels.length === 0 && <span className="text-xs text-muted-foreground">No labels yet.</span>}
                                                    </div>
                                                </ScrollArea>
                                                <div className="flex gap-2 items-center border-t pt-2">
                                                    <Input placeholder="New label" value={newLabelName} onChange={e => setNewLabelName(e.target.value)} className="h-7 text-xs flex-1" />
                                                    <div className="flex gap-1">
                                                        {LABEL_COLORS.map(c => (
                                                            <div key={c} className={`size-4 rounded-full cursor-pointer ${newLabelColor === c ? 'ring-2 ring-primary' : ''}`} style={{ backgroundColor: c }} onClick={() => setNewLabelColor(c)} />
                                                        ))}
                                                    </div>
                                                    <Button type="button" size="sm" className="h-7 text-xs" disabled={!newLabelName.trim()} onClick={handleCreateLabel}>Add</Button>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}
                        </div>
                    </ScrollArea>
                    <div className="border-t bg-background px-6 py-4">
                        <div className="flex items-center justify-between gap-4">
                            <p className="text-xs text-muted-foreground">
                                {createMode === 'SCHEDULE'
                                    ? 'The scheduled item will appear in Scheduling and move to the board only when due.'
                                    : 'The task will be created immediately on the selected board.'}
                            </p>
                            <div className="flex shrink-0 gap-3">
                                <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
                                <Button
                                    type="submit"
                                    disabled={
                                        loading
                                        || uploadingScreenshots
                                        || !title.trim()
                                        || !description.trim()
                                        || !boardId
                                        || !selectedUserId
                                        || (createMode === 'SCHEDULE' && (!scheduleAt || !!scheduleValidationError))
                                        || hasImageModelMismatch
                                    }
                                >
                                    {uploadingScreenshots
                                        ? `Uploading ${stagedFiles.length} image${stagedFiles.length !== 1 ? 's' : ''}...`
                                        : loading
                                            ? (createMode === 'SCHEDULE' ? 'Creating Schedule...' : 'Creating...')
                                            : (createMode === 'SCHEDULE'
                                                ? 'Create Schedule'
                                                : stagedFiles.length > 0
                                                    ? `Create Task + ${stagedFiles.length} image${stagedFiles.length !== 1 ? 's' : ''}`
                                                    : 'Create Task')}
                                </Button>
                            </div>
                        </div>
                    </div>
                </form>

                <Dialog open={dependencyDialogOpen} onOpenChange={setDependencyDialogOpen}>
                    <DialogContent className="sm:max-w-[640px]">
                        <DialogHeader>
                            <DialogTitle>Select Dependencies</DialogTitle>
                        </DialogHeader>
                        <div className="space-y-3">
                            <p className="text-sm text-muted-foreground">
                                Choose one or more active tasks from this board. These tasks must be completed before the new task can proceed.
                            </p>
                            <ScrollArea className="max-h-[360px] rounded-md border">
                                <div className="p-3 space-y-2">
                                    {dependencyTasksLoading ? (
                                        <p className="text-sm text-muted-foreground">Loading board tasks...</p>
                                    ) : activeDependencyTasks.length === 0 ? (
                                        <p className="text-sm text-muted-foreground">No To Do or In Progress tasks are available.</p>
                                    ) : activeDependencyTasks.map(task => {
                                        const checked = selectedDependencyIds.includes(task.id);
                                        return (
                                            <label key={task.id} className="flex items-start gap-3 rounded-md border p-3 cursor-pointer hover:bg-secondary/40">
                                                <Checkbox
                                                    checked={checked}
                                                    onCheckedChange={(next) => {
                                                        setSelectedDependencyIds(prev => next ? [...prev, task.id] : prev.filter(id => id !== task.id));
                                                    }}
                                                />
                                                <div className="min-w-0 space-y-1">
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                                        <span className="font-mono">#{task.idShort}</span>
                                                        <Badge variant="outline" className="h-5 text-[10px]">{task.currentStatus}</Badge>
                                                    </div>
                                                    <div className="text-sm font-medium">{task.title || task.name}</div>
                                                </div>
                                            </label>
                                        );
                                    })}
                                </div>
                            </ScrollArea>
                            <div className="flex justify-between gap-2">
                                <p className="text-xs text-muted-foreground">{selectedDependencyIds.length} selected</p>
                                <Button type="button" size="sm" onClick={() => setDependencyDialogOpen(false)}>Done</Button>
                            </div>
                        </div>
                    </DialogContent>
                </Dialog>
            </DialogContent>
        </Dialog>
    );
}
