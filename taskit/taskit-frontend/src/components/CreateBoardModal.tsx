import { useEffect, useRef, useState, useCallback } from 'react';
import { useToast } from '@/hooks/use-toast';
import type { DirectoryEntry, DirectoryCheckResult } from '../services/integration/IntegrationService';
import type { Member } from '../types';
import { useService } from '../contexts/ServiceContext';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { Badge } from '@/components/ui/badge';
import { FolderOpen, CheckCircle2, AlertCircle, Loader2, Bot } from 'lucide-react';

interface CreateBoardModalProps {
    onClose: () => void;
    onCreate: (name: string, description: string, workingDir: string, disabledAgents?: string[]) => Promise<void>;
    autoOpen?: boolean;
}

type DirCheckStatus = {
    checking: boolean;
    result: DirectoryCheckResult | null;
};

export function CreateBoardModal({ onClose, onCreate }: CreateBoardModalProps) {
    const service = useService();
    const { toast } = useToast();
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [workingDir, setWorkingDir] = useState('~/');
    const [dirSuggestions, setDirSuggestions] = useState<DirectoryEntry[]>([]);
    const [dirInputFocused, setDirInputFocused] = useState(false);
    const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(-1);
    const [loading, setLoading] = useState(false);
    const blurTimerRef = useRef<number | null>(null);

    const [dirCheck, setDirCheck] = useState<DirCheckStatus>({ checking: false, result: null });
    const [disabledAgents, setDisabledAgents] = useState<Set<string>>(new Set());
    const [availableAgents, setAvailableAgents] = useState<Member[]>([]);
    const [agentsLoading, setAgentsLoading] = useState(false);

    const debouncedWorkingDir = useDebouncedValue(workingDir, 220);
    const debouncedDirForCheck = useDebouncedValue(workingDir, 500);
    const showSuggestions = dirInputFocused && dirSuggestions.length > 0;

    // Fetch available agents
    const loadAgents = useCallback(async () => {
        setAgentsLoading(true);
        try {
            const result = await service.fetchMembersPage({ role: ['AGENT'], page_size: 100 });
            setAvailableAgents(result.results);
        } catch {
            // Silent fail
        } finally {
            setAgentsLoading(false);
        }
    }, [service]);

    useEffect(() => {
        loadAgents();
    }, [loadAgents]);

    // Directory suggestions
    useEffect(() => {
        let cancelled = false;
        const query = debouncedWorkingDir.trim();
        if (!query) {
            setDirSuggestions([]);
            setActiveSuggestionIndex(-1);
            return;
        }
        service
            .suggestDirectories(query, 8)
            .then(entries => {
                if (cancelled) return;
                setDirSuggestions(entries);
                setActiveSuggestionIndex(-1);
            })
            .catch(() => {
                if (cancelled) return;
                setDirSuggestions([]);
                setActiveSuggestionIndex(-1);
            });
        return () => { cancelled = true; };
    }, [debouncedWorkingDir, service]);

    // Directory pre-flight check
    useEffect(() => {
        let cancelled = false;
        const path = debouncedDirForCheck.trim();
        if (!path || path === '~/') {
            setDirCheck({ checking: false, result: null });
            return;
        }
        setDirCheck(prev => ({ ...prev, checking: true }));
        service
            .checkDirectory(path)
            .then(result => {
                if (!cancelled) setDirCheck({ checking: false, result });
            })
            .catch(() => {
                if (!cancelled) setDirCheck({ checking: false, result: null });
            });
        return () => { cancelled = true; };
    }, [debouncedDirForCheck, service]);

    useEffect(() => {
        return () => {
            if (blurTimerRef.current !== null) window.clearTimeout(blurTimerRef.current);
        };
    }, []);

    const handleDirInputBlur = () => {
        blurTimerRef.current = window.setTimeout(() => {
            setDirInputFocused(false);
            setActiveSuggestionIndex(-1);
        }, 120);
    };

    const applySuggestion = (entry: DirectoryEntry) => {
        setWorkingDir(entry.path);
        setDirInputFocused(false);
        setDirSuggestions([]);
        setActiveSuggestionIndex(-1);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (!showSuggestions) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveSuggestionIndex(prev => (prev + 1) % dirSuggestions.length);
            return;
        }
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveSuggestionIndex(prev => (prev <= 0 ? dirSuggestions.length - 1 : prev - 1));
            return;
        }
        if (e.key === 'Enter' && activeSuggestionIndex >= 0 && activeSuggestionIndex < dirSuggestions.length) {
            e.preventDefault();
            applySuggestion(dirSuggestions[activeSuggestionIndex]);
            return;
        }
        if (e.key === 'Escape') {
            setDirInputFocused(false);
            setActiveSuggestionIndex(-1);
        }
    };

    const canSubmit = name.trim() && workingDir.trim() && !loading
        && !dirCheck.checking && dirCheck.result?.can_init !== false;

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!canSubmit) return;
        setLoading(true);
        try {
            await onCreate(name, description, workingDir.trim(), disabledAgents.size > 0 ? [...disabledAgents] : undefined);
            onClose();
        } catch {
            toast({
                title: "Error",
                description: "Failed to create board",
                variant: "destructive"
            });
        } finally {
            setLoading(false);
        }
    };

    const renderDirStatus = () => {
        if (dirCheck.checking) {
            return (
                <p className="text-[11px] text-muted-foreground flex items-center gap-1">
                    <Loader2 className="size-3 animate-spin" />
                    Checking directory...
                </p>
            );
        }
        if (dirCheck.result) {
            if (dirCheck.result.can_init) {
                return (
                    <p className="text-[11px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                        <CheckCircle2 className="size-3" />
                        {dirCheck.result.message}
                    </p>
                );
            }
            return (
                <p className="text-[11px] text-destructive flex items-center gap-1">
                    <AlertCircle className="size-3" />
                    {dirCheck.result.message}
                </p>
            );
        }
        return (
            <p className="text-[11px] text-muted-foreground">
                Start typing to search. Odin will be initialized automatically.
            </p>
        );
    };

    return (
        <Dialog open onOpenChange={onClose}>
            <DialogContent className="sm:max-w-[460px]">
                <DialogHeader>
                    <DialogTitle>Create New Board</DialogTitle>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                    <div className="flex flex-col gap-2">
                        <Label>Board Name</Label>
                        <Input
                            autoFocus
                            required
                            value={name}
                            onChange={e => setName(e.target.value)}
                            placeholder="e.g. Q1 Roadmap"
                        />
                    </div>

                    <div className="flex flex-col gap-2 relative">
                        <Label className="flex items-center gap-1.5">
                            <FolderOpen className="size-3.5" />
                            Project Directory
                        </Label>
                        <Input
                            required
                            value={workingDir}
                            onChange={e => setWorkingDir(e.target.value)}
                            onFocus={() => setDirInputFocused(true)}
                            onBlur={handleDirInputBlur}
                            onKeyDown={handleKeyDown}
                            placeholder="/absolute/path/to/project"
                            className="font-mono text-xs"
                        />
                        {showSuggestions && (
                            <div className="absolute top-[62px] left-0 right-0 z-50 rounded border bg-popover shadow-md">
                                <ScrollArea className="max-h-[180px]">
                                    <div className="p-1">
                                        {dirSuggestions.map((entry, idx) => (
                                            <button
                                                key={entry.path}
                                                type="button"
                                                className={`w-full rounded px-2 py-1.5 text-left text-xs font-mono ${idx === activeSuggestionIndex ? 'bg-accent' : 'hover:bg-accent/70'}`}
                                                onMouseDown={e => { e.preventDefault(); applySuggestion(entry); }}
                                            >
                                                {entry.path}
                                            </button>
                                        ))}
                                    </div>
                                </ScrollArea>
                            </div>
                        )}
                        {renderDirStatus()}
                    </div>

                    <div className="flex flex-col gap-2">
                        <Label>Description (Optional)</Label>
                        <Textarea
                            value={description}
                            onChange={e => setDescription(e.target.value)}
                            placeholder="What is this board for?"
                            className="min-h-[80px]"
                        />
                    </div>

                    {/* Agent toggles — only visible when directory can be initialized */}
                    {dirCheck.result?.can_init && (
                        <div className="flex flex-col gap-2">
                            <Label className="flex items-center gap-1.5">
                                <Bot className="size-3.5" />
                                Agents
                            </Label>
                            {agentsLoading ? (
                                <div className="text-xs text-muted-foreground text-center py-2">Loading agents...</div>
                            ) : availableAgents.length === 0 ? (
                                <div className="text-xs text-muted-foreground text-center py-2">No agents available</div>
                            ) : (
                                <div className="space-y-1.5">
                                    {availableAgents.map(agent => {
                                        const enabled = !disabledAgents.has(agent.fullName);
                                        const costTier = (agent as Member & { cost_tier?: string }).cost_tier || 'medium';
                                        const tierColor = costTier === 'low'
                                            ? 'text-emerald-600 border-emerald-300 dark:text-emerald-400 dark:border-emerald-600'
                                            : costTier === 'medium'
                                                ? 'text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600'
                                                : 'text-red-600 border-red-300 dark:text-red-400 dark:border-red-600';
                                        return (
                                            <div
                                                key={agent.id}
                                                className={`flex items-center gap-3 px-3 py-1.5 rounded-lg border bg-muted/20 ${
                                                    enabled ? 'border-border' : 'border-border/50 opacity-60'
                                                }`}
                                            >
                                                <button
                                                    type="button"
                                                    role="switch"
                                                    aria-checked={enabled}
                                                    className={`relative inline-flex h-4 w-7 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                                                        enabled ? 'bg-primary' : 'bg-muted-foreground/25'
                                                    }`}
                                                    onClick={() => {
                                                        setDisabledAgents(prev => {
                                                            const next = new Set(prev);
                                                            if (next.has(agent.fullName)) next.delete(agent.fullName);
                                                            else next.add(agent.fullName);
                                                            return next;
                                                        });
                                                    }}
                                                >
                                                    <span
                                                        className={`pointer-events-none inline-block size-3 transform rounded-full bg-background shadow-sm ring-0 transition-transform ${
                                                            enabled ? 'translate-x-3' : 'translate-x-0'
                                                        }`}
                                                    />
                                                </button>
                                                <span className="text-xs font-medium min-w-[60px]">{agent.fullName}</span>
                                                <Badge variant="outline" className={`text-[9px] px-1.5 py-0 ${tierColor}`}>
                                                    {costTier}
                                                </Badge>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                            <p className="text-[10px] text-muted-foreground">
                                Disabled agents won't be available for task assignment on this board.
                            </p>
                        </div>
                    )}

                    <div className="flex gap-3 justify-end mt-2">
                        <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
                        <Button type="submit" disabled={!canSubmit}>
                            {loading ? 'Creating...' : 'Create Board'}
                        </Button>
                    </div>
                </form>
            </DialogContent>
        </Dialog>
    );
}
