import { useEffect, useMemo, useState, useCallback } from 'react';
import { useToast } from '@/hooks/use-toast';
import type { AgentConfig, Board, Label as LabelType, ModelInfo, TaskPreset, PresetCategory } from '../types';
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
import { ChevronDown, Plus, X, Bot, User } from 'lucide-react';
import { PresetPicker } from './PresetPicker';

export interface ApiUser {
    id: number;
    name: string;
    email: string;
    role?: 'HUMAN' | 'AGENT' | 'ADMIN';
    availableModels?: ModelInfo[];
}

interface CreateTaskModalProps {
    boards: Board[];
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
        workingDir?: string
    ) => Promise<void>;
    availableLabels?: LabelType[];
}

export function CreateTaskModal({ boards, defaultBoardId, users: initialUsers, onClose, onCreate, availableLabels }: CreateTaskModalProps) {
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

    const [users, setUsers] = useState<ApiUser[]>(initialUsers);

    // Preset state
    const [presets, setPresets] = useState<TaskPreset[]>([]);
    const [presetCategories, setPresetCategories] = useState<PresetCategory[]>([]);
    const [selectedPreset, setSelectedPreset] = useState<TaskPreset | null>(null);

    useEffect(() => {
        service.fetchPresets()
            .then(data => {
                setPresets(data.presets);
                setPresetCategories(data.categories);
            })
            .catch(() => { /* presets are optional — silently degrade */ });
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

    // Agent configs for current board (to know disabled agents)
    const [agentConfigs, setAgentConfigs] = useState<AgentConfig[]>([]);

    const selectedBoardObj = useMemo(() => boards.find(b => b.id === boardId), [boards, boardId]);

    const loadAgentConfigs = useCallback(async (board: Board) => {
        if (!board.odinInitialized) { setAgentConfigs([]); return; }
        try {
            const configs = await service.fetchBoardAgents(board.id);
            setAgentConfigs(configs);
        } catch {
            setAgentConfigs([]);
        }
    }, [service]);

    useEffect(() => {
        if (selectedBoardObj) loadAgentConfigs(selectedBoardObj);
    }, [selectedBoardObj, loadAgentConfigs]);

    const isAgentUser = (u: ApiUser) => u.email.endsWith('@odin.agent') || u.role === 'AGENT';
    const disabledAgentEmails = useMemo(
        () => new Set(agentConfigs.filter(a => !a.enabled).map(a => `${a.name}@odin.agent`)),
        [agentConfigs],
    );

    // Filter users to selected board's members, plus disabled agents from config
    const boardUsers = useMemo(() => {
        if (!selectedBoardObj) return users;
        const memberIds = new Set(selectedBoardObj.memberIds);
        const onBoard = users.filter(u => memberIds.has(String(u.id)));

        // Add disabled agents that exist in users but aren't board members
        const disabledAgents = users.filter(
            u => disabledAgentEmails.has(u.email) && !memberIds.has(String(u.id))
        );

        return [...onBoard, ...disabledAgents];
    }, [users, selectedBoardObj, disabledAgentEmails]);

    const [allLabels, setAllLabels] = useState<LabelType[]>(availableLabels || []);
    const [selectedLabelIds, setSelectedLabelIds] = useState<number[]>([]);
    const [showLabelPicker, setShowLabelPicker] = useState(false);
    const [newLabelName, setNewLabelName] = useState('');
    const [newLabelColor, setNewLabelColor] = useState('#3b82f6');

    const LABEL_COLORS = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899', '#64748b'];

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
            toast({ title: "Error", description: "Failed to create label.", variant: "destructive" });
        }
    };

    const [showNewUser, setShowNewUser] = useState(false);
    const [newUserName, setNewUserName] = useState('');
    const [newUserEmail, setNewUserEmail] = useState('');
    const [creatingUser, setCreatingUser] = useState(false);

    // Reset assignee when board changes and current selection isn't on the new board
    useEffect(() => {
        const enabledBoardUsers = boardUsers.filter(u => !disabledAgentEmails.has(u.email));
        const currentValid = enabledBoardUsers.some(u => String(u.id) === selectedUserId);
        if (!currentValid && enabledBoardUsers.length > 0) {
            setSelectedUserId(String(enabledBoardUsers[0].id));
        }
    }, [boardId, boardUsers, disabledAgentEmails]); // eslint-disable-line react-hooks/exhaustive-deps

    const selectedAssignee = useMemo(
        () => boardUsers.find(u => String(u.id) === selectedUserId),
        [boardUsers, selectedUserId],
    );

    const availableModels = useMemo(
        () => selectedAssignee?.availableModels || [],
        [selectedAssignee],
    );

    useEffect(() => {
        if (availableModels.length === 0) {
            setSelectedModelName('');
            return;
        }
        const defaultModel = availableModels.find(m => m.is_default)?.name || availableModels[0].name;
        setSelectedModelName(defaultModel);
    }, [selectedUserId, availableModels]);

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
                title: "Error",
                description: "Failed to create user. Email may already exist.",
                variant: "destructive"
            });
        } finally {
            setCreatingUser(false);
        }
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!title.trim() || !description.trim() || !boardId || !selectedUserId) return;
        setLoading(true);
        try {
            const etaNum = devEta ? parseFloat(devEta) : undefined;
            await onCreate(
                boardId,
                title,
                description,
                priority,
                Number(selectedUserId),
                selectedModelName || undefined,
                etaNum,
                selectedLabelIds.length > 0 ? selectedLabelIds : undefined,
                undefined, // workingDir — inherited from board
            );
            onClose();
        } catch {
            toast({
                title: "Error",
                description: "Failed to create task",
                variant: "destructive"
            });
        } finally {
            setLoading(false);
        }
    };

    return (
        <Dialog open onOpenChange={onClose}>
            <DialogContent className="sm:max-w-[460px]">
                <DialogHeader>
                    <DialogTitle>Create New Task</DialogTitle>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="flex flex-col gap-4">
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
                        <Textarea required value={description} onChange={e => setDescription(e.target.value)} placeholder="What needs to be done?" className="min-h-[80px] max-h-[200px]" />
                    </div>

                    <div className="flex flex-col gap-2">
                        <Label>Assignee</Label>
                        {showNewUser ? (
                            <div className="flex flex-col gap-2">
                                <Input value={newUserName} onChange={e => setNewUserName(e.target.value)} placeholder="Name" />
                                <Input type="email" value={newUserEmail} onChange={e => setNewUserEmail(e.target.value)} placeholder="Email" />
                                <div className="flex gap-2">
                                    <Button type="button" size="sm" className="flex-1"
                                        disabled={creatingUser || !newUserName.trim() || !newUserEmail.trim()}
                                        onClick={handleCreateUser}>
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
                                                <SelectItem
                                                    key={u.id}
                                                    value={String(u.id)}
                                                    disabled={disabled}
                                                    className={disabled ? 'opacity-40' : ''}
                                                >
                                                    <span className="flex items-center gap-2">
                                                        {agent ? (
                                                            <Bot className="size-3.5 text-blue-500 shrink-0" />
                                                        ) : (
                                                            <User className="size-3.5 text-muted-foreground shrink-0" />
                                                        )}
                                                        <span className={disabled ? 'line-through' : ''}>
                                                            {u.name}
                                                        </span>
                                                        {disabled && (
                                                            <span className="text-[10px] text-muted-foreground">disabled</span>
                                                        )}
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

                    {/* Collapsible extra settings */}
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
                                        <Button key={p} type="button" variant={priority === p ? 'default' : 'outline'} size="sm" className="flex-1"
                                            onClick={() => setPriority(p)}>
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
                                                    <div key={c} className={`size-4 rounded-full cursor-pointer ${newLabelColor === c ? 'ring-2 ring-primary' : ''}`}
                                                        style={{ backgroundColor: c }} onClick={() => setNewLabelColor(c)} />
                                                ))}
                                            </div>
                                            <Button type="button" size="sm" className="h-7 text-xs" disabled={!newLabelName.trim()} onClick={handleCreateLabel}>Add</Button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    <div className="flex gap-3 justify-end mt-2">
                        <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
                        <Button type="submit" disabled={loading || !title.trim() || !description.trim() || !boardId || !selectedUserId}>
                            {loading ? 'Creating...' : 'Create Task'}
                        </Button>
                    </div>
                </form>
            </DialogContent>
        </Dialog>
    );
}
