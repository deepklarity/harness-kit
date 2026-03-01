import { useState, useEffect, useCallback } from 'react';
import type { AgentConfig, Board, Member } from '../types';
import { useService } from '../contexts/ServiceContext';
import { useToast } from '@/hooks/use-toast';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Bot, UserMinus, UserPlus, Check, ChevronDown, ChevronRight } from 'lucide-react';
import { MEMBER_COLORS } from '@/services/harness/HarnessTimeService';
import { cn } from '@/lib/utils';

interface ManageMembersModalProps {
    board: Board;
    members: Member[];
    onClose: () => void;
    onDataChange: () => void;
}

export function ManageMembersModal({ board, members, onClose, onDataChange }: ManageMembersModalProps) {
    const service = useService();
    const { toast } = useToast();

    // Agents tab state
    const [agents, setAgents] = useState<AgentConfig[]>([]);
    const [agentsLoading, setAgentsLoading] = useState(false);
    const [expandedAgents, setExpandedAgents] = useState<Set<string>>(new Set());

    // People tab state
    const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
    const [adding, setAdding] = useState(false);
    const [removeTarget, setRemoveTarget] = useState<Member | null>(null);
    const [removing, setRemoving] = useState(false);

    // Inline create member form
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [newName, setNewName] = useState('');
    const [newEmail, setNewEmail] = useState('');
    const [newColor, setNewColor] = useState(MEMBER_COLORS[0]);
    const [creating, setCreating] = useState(false);

    const defaultTab = board.odinInitialized ? 'agents' : 'people';

    const loadAgents = useCallback(async () => {
        if (!board.odinInitialized) return;
        setAgentsLoading(true);
        try {
            const result = await service.fetchBoardAgents(board.id);
            setAgents(result);
        } catch {
            // Silent fail
        } finally {
            setAgentsLoading(false);
        }
    }, [board.id, board.odinInitialized, service]);

    useEffect(() => {
        loadAgents();
    }, [loadAgents]);

    const handleToggleAgent = async (agentName: string, enabled: boolean) => {
        // Optimistic update
        setAgents(prev => prev.map(a => a.name === agentName ? { ...a, enabled } : a));
        try {
            await service.toggleBoardAgent(board.id, agentName, enabled);
            onDataChange();
        } catch (e) {
            // Revert
            setAgents(prev => prev.map(a => a.name === agentName ? { ...a, enabled: !enabled } : a));
            toast({
                title: 'Failed to toggle agent',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        }
    };

    const handleToggleModel = async (agentName: string, modelName: string, enabled: boolean) => {
        // Optimistic update
        setAgents(prev => prev.map(a => {
            if (a.name !== agentName) return a;
            return {
                ...a,
                models: a.models.map(m => m.name === modelName ? { ...m, enabled } : m),
            };
        }));
        try {
            await service.toggleBoardModel(board.id, agentName, modelName, enabled);
            onDataChange();
        } catch (e) {
            // Revert
            setAgents(prev => prev.map(a => {
                if (a.name !== agentName) return a;
                return {
                    ...a,
                    models: a.models.map(m => m.name === modelName ? { ...m, enabled: !enabled } : m),
                };
            }));
            toast({
                title: 'Failed to toggle model',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        }
    };

    const handleAddMembers = async () => {
        if (selectedUserIds.length === 0) return;
        setAdding(true);
        try {
            await service.addBoardMembers(board.id, selectedUserIds);
            toast({ title: 'Members added', description: `Added ${selectedUserIds.length} member(s).` });
            setSelectedUserIds([]);
            onDataChange();
        } catch (e) {
            toast({
                title: 'Failed to add members',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        } finally {
            setAdding(false);
        }
    };

    const handleRemoveMember = async (member: Member) => {
        setRemoving(true);
        setRemoveTarget(member);
        try {
            await service.removeBoardMembers(board.id, [member.id]);
            toast({ title: 'Member removed', description: `"${member.fullName}" removed from board.` });
            onDataChange();
        } catch (e) {
            toast({
                title: 'Failed to remove member',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        } finally {
            setRemoving(false);
            setRemoveTarget(null);
        }
    };

    const handleCreateMember = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!newName.trim() || !newEmail.trim()) return;
        setCreating(true);
        try {
            const user = await service.createUser(newName, newEmail, newColor);
            await service.addBoardMembers(board.id, [String(user.id)]);
            toast({ title: 'Member created', description: `"${newName}" created and added to board.` });
            setNewName('');
            setNewEmail('');
            setNewColor(MEMBER_COLORS[0]);
            setShowCreateForm(false);
            onDataChange();
        } catch {
            toast({
                title: 'Failed to create member',
                description: 'Email may already exist.',
                variant: 'destructive',
            });
        } finally {
            setCreating(false);
        }
    };

    const costTierColor = (tier: string) => {
        switch (tier) {
            case 'low': return 'text-emerald-600 border-emerald-300 dark:text-emerald-400 dark:border-emerald-600';
            case 'medium': return 'text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600';
            case 'high': return 'text-red-600 border-red-300 dark:text-red-400 dark:border-red-600';
            default: return 'text-muted-foreground border-border';
        }
    };

    const isAgentUser = (member: Member) => member.email.endsWith('@odin.agent');
    const boardMembers = board.members;
    const humanMembers = boardMembers.filter(m => !isAgentUser(m));
    const availableToAdd = members.filter(m => !board.memberIds.includes(m.id) && !isAgentUser(m));

    const getAffectedTaskCount = (memberId: string) => {
        return board.tasks.filter(t => t.assigneeIds.includes(memberId)).length;
    };

    return (
        <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
            <DialogContent className="sm:max-w-[520px]">
                <DialogHeader>
                    <DialogTitle>Manage members — {board.name}</DialogTitle>
                </DialogHeader>

                <Tabs defaultValue={defaultTab}>
                    <TabsList className="w-full">
                        {board.odinInitialized && (
                            <TabsTrigger value="agents" className="flex-1 gap-1.5">
                                <Bot className="size-3.5" />
                                Agents
                            </TabsTrigger>
                        )}
                        <TabsTrigger value="people" className="flex-1 gap-1.5">
                            <UserPlus className="size-3.5" />
                            People
                        </TabsTrigger>
                    </TabsList>

                    {/* ─── Agents Tab ─── */}
                    {board.odinInitialized && (
                        <TabsContent value="agents">
                            <div className="max-h-[400px] overflow-y-auto space-y-2 py-2">
                                {agentsLoading ? (
                                    <div className="text-sm text-muted-foreground text-center py-4">Loading agents...</div>
                                ) : agents.length === 0 ? (
                                    <div className="text-sm text-muted-foreground text-center py-4">No agents configured</div>
                                ) : (
                                    agents.map(agent => {
                                        const isExpanded = expandedAgents.has(agent.name);
                                        const enabledModelCount = agent.models.filter(m => m.enabled).length;
                                        const hasModels = agent.models.length > 0;
                                        return (
                                            <div key={agent.name} className="space-y-1">
                                                <div
                                                    className={cn(
                                                        'flex items-center gap-3 px-3 py-2 rounded-lg border bg-muted/20',
                                                        agent.enabled ? 'border-border' : 'border-border/50 opacity-60',
                                                    )}
                                                >
                                                    <button
                                                        type="button"
                                                        role="switch"
                                                        aria-checked={agent.enabled}
                                                        className={cn(
                                                            'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors',
                                                            agent.enabled ? 'bg-primary' : 'bg-muted-foreground/25',
                                                        )}
                                                        onClick={() => handleToggleAgent(agent.name, !agent.enabled)}
                                                    >
                                                        <span
                                                            className={cn(
                                                                'pointer-events-none inline-block size-4 transform rounded-full bg-background shadow-sm ring-0 transition-transform',
                                                                agent.enabled ? 'translate-x-4' : 'translate-x-0',
                                                            )}
                                                        />
                                                    </button>
                                                    <span className="text-sm font-medium min-w-[80px]">{agent.name}</span>
                                                    <Badge variant="outline" className={`text-[9px] px-1.5 py-0 ${costTierColor(agent.cost_tier)}`}>
                                                        {agent.cost_tier}
                                                    </Badge>
                                                    {agent.capabilities.length > 0 && (
                                                        <div className="flex gap-1">
                                                            {agent.capabilities.slice(0, 3).map(cap => (
                                                                <Badge key={cap} variant="secondary" className="text-[9px] px-1.5 py-0">
                                                                    {cap}
                                                                </Badge>
                                                            ))}
                                                            {agent.capabilities.length > 3 && (
                                                                <Badge variant="secondary" className="text-[9px] px-1.5 py-0">
                                                                    +{agent.capabilities.length - 3}
                                                                </Badge>
                                                            )}
                                                        </div>
                                                    )}
                                                    {hasModels && agent.enabled && (
                                                        <button
                                                            type="button"
                                                            onClick={() => {
                                                                setExpandedAgents(prev => {
                                                                    const next = new Set(prev);
                                                                    if (next.has(agent.name)) next.delete(agent.name);
                                                                    else next.add(agent.name);
                                                                    return next;
                                                                });
                                                            }}
                                                            className="ml-auto text-muted-foreground hover:text-foreground transition-colors"
                                                        >
                                                            {isExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                                                        </button>
                                                    )}
                                                </div>

                                                {isExpanded && agent.enabled && hasModels && (
                                                    <div className="ml-6 space-y-1 pl-3 border-l-2 border-border/30">
                                                        {agent.models.map(model => {
                                                            const isLastEnabled = enabledModelCount === 1 && model.enabled;
                                                            return (
                                                                <div
                                                                    key={model.name}
                                                                    className="flex items-center gap-2 px-2 py-1.5 rounded text-xs"
                                                                >
                                                                    <Checkbox
                                                                        checked={model.enabled}
                                                                        disabled={isLastEnabled}
                                                                        onCheckedChange={(checked) => {
                                                                            if (!isLastEnabled) {
                                                                                handleToggleModel(agent.name, model.name, checked === true);
                                                                            }
                                                                        }}
                                                                        className="size-3.5"
                                                                    />
                                                                    <div className="flex-1 min-w-0">
                                                                        <div className="flex items-center gap-1.5">
                                                                            <span className={cn(
                                                                                "font-medium",
                                                                                !model.enabled && "text-muted-foreground"
                                                                            )}>
                                                                                {model.name}
                                                                            </span>
                                                                            {model.is_default && (
                                                                                <Badge variant="secondary" className="text-[8px] px-1 py-0">
                                                                                    default
                                                                                </Badge>
                                                                            )}
                                                                        </div>
                                                                        {model.description && (
                                                                            <div className="text-[10px] text-muted-foreground truncate">
                                                                                {model.description}
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                </div>
                                                            );
                                                        })}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })
                                )}
                            </div>
                        </TabsContent>
                    )}

                    {/* ─── People Tab ─── */}
                    <TabsContent value="people">
                        <div className="max-h-[400px] overflow-y-auto space-y-4 py-2">
                            {/* Current human members */}
                            {humanMembers.length > 0 && (
                                <div>
                                    <div className="text-xs font-medium text-muted-foreground mb-2">On this board</div>
                                    <div className="space-y-1">
                                        {humanMembers.map(member => {
                                            const taskCount = getAffectedTaskCount(member.id);
                                            return (
                                                <div
                                                    key={member.id}
                                                    className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-muted/50 group"
                                                >
                                                    <div
                                                        className="size-6 rounded-full flex items-center justify-center text-[10px] font-medium text-white shrink-0"
                                                        style={{ backgroundColor: member.color }}
                                                    >
                                                        {member.initials}
                                                    </div>
                                                    <div className="min-w-0 flex-1">
                                                        <span className="text-sm font-medium">{member.fullName}</span>
                                                        {taskCount > 0 && (
                                                            <span className="text-[10px] text-muted-foreground ml-2">
                                                                {taskCount} task{taskCount !== 1 ? 's' : ''}
                                                            </span>
                                                        )}
                                                    </div>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="size-6 p-0 opacity-0 group-hover:opacity-100 text-destructive hover:text-destructive hover:bg-destructive/10 transition-opacity"
                                                        disabled={removing && removeTarget?.id === member.id}
                                                        onClick={() => handleRemoveMember(member)}
                                                    >
                                                        <UserMinus className="size-3.5" />
                                                    </Button>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            )}

                            {/* Add existing users */}
                            {availableToAdd.length > 0 && (
                                <div>
                                    <div className="text-xs font-medium text-muted-foreground mb-2">Add existing users</div>
                                    <div className="space-y-1">
                                        {availableToAdd.map(member => (
                                            <label
                                                key={member.id}
                                                className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-muted/50 cursor-pointer"
                                            >
                                                <Checkbox
                                                    checked={selectedUserIds.includes(member.id)}
                                                    onCheckedChange={(checked) => {
                                                        setSelectedUserIds(prev =>
                                                            checked
                                                                ? [...prev, member.id]
                                                                : prev.filter(id => id !== member.id)
                                                        );
                                                    }}
                                                />
                                                <div
                                                    className="size-6 rounded-full flex items-center justify-center text-[10px] font-medium text-white shrink-0"
                                                    style={{ backgroundColor: member.color }}
                                                >
                                                    {member.initials}
                                                </div>
                                                <div className="min-w-0">
                                                    <span className="text-sm font-medium">{member.fullName}</span>
                                                    <div className="text-[11px] text-muted-foreground">{member.email}</div>
                                                </div>
                                            </label>
                                        ))}
                                    </div>
                                    {selectedUserIds.length > 0 && (
                                        <Button
                                            size="sm"
                                            className="mt-2 w-full"
                                            disabled={adding}
                                            onClick={handleAddMembers}
                                        >
                                            {adding ? 'Adding...' : `Add ${selectedUserIds.length} member${selectedUserIds.length !== 1 ? 's' : ''}`}
                                        </Button>
                                    )}
                                </div>
                            )}

                            {/* Create new member inline */}
                            <div>
                                {!showCreateForm ? (
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        className="w-full gap-1.5"
                                        onClick={() => setShowCreateForm(true)}
                                    >
                                        <UserPlus className="size-3.5" />
                                        Create new member
                                    </Button>
                                ) : (
                                    <form onSubmit={handleCreateMember} className="border border-border rounded-lg p-3 space-y-3">
                                        <div className="text-xs font-medium text-muted-foreground">New member</div>
                                        <div className="flex flex-col gap-2">
                                            <Label className="text-xs">Name</Label>
                                            <Input
                                                autoFocus
                                                required
                                                value={newName}
                                                onChange={e => setNewName(e.target.value)}
                                                placeholder="e.g. Alice Smith"
                                                className="h-8 text-sm"
                                            />
                                        </div>
                                        <div className="flex flex-col gap-2">
                                            <Label className="text-xs">Email</Label>
                                            <Input
                                                required
                                                type="email"
                                                value={newEmail}
                                                onChange={e => setNewEmail(e.target.value)}
                                                placeholder="e.g. alice@example.com"
                                                className="h-8 text-sm"
                                            />
                                        </div>
                                        <div className="flex flex-col gap-2">
                                            <Label className="text-xs">Color</Label>
                                            <div className="flex flex-wrap gap-1.5">
                                                {MEMBER_COLORS.map(c => (
                                                    <button
                                                        key={c}
                                                        type="button"
                                                        onClick={() => setNewColor(c)}
                                                        className={cn(
                                                            'size-6 rounded-full border-2 transition-all flex items-center justify-center',
                                                            newColor === c ? 'border-foreground scale-110' : 'border-transparent hover:opacity-80',
                                                        )}
                                                        style={{ backgroundColor: c }}
                                                    >
                                                        {newColor === c && <Check className="size-3 text-white drop-shadow-md" />}
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                        <div className="flex gap-2 justify-end">
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="sm"
                                                onClick={() => { setShowCreateForm(false); setNewName(''); setNewEmail(''); }}
                                            >
                                                Cancel
                                            </Button>
                                            <Button
                                                type="submit"
                                                size="sm"
                                                disabled={creating || !newName.trim() || !newEmail.trim()}
                                            >
                                                {creating ? 'Creating...' : 'Create & Add'}
                                            </Button>
                                        </div>
                                    </form>
                                )}
                            </div>
                        </div>
                    </TabsContent>
                </Tabs>
            </DialogContent>
        </Dialog>
    );
}
