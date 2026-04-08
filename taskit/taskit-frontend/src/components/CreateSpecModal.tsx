import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import type { AgentConfig, Board } from '@/types';
import { useService } from '@/contexts/ServiceContext';
import { useToast } from '@/hooks/use-toast';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { OdinGuideContent } from '@/components/OdinGuideModal';
import { MarkdownEditor } from '@/components/MarkdownEditor';
import { ChevronDown, FileText, FolderOpen, Sparkles, Terminal, Upload } from 'lucide-react';

interface CreateSpecModalProps {
    open: boolean;
    onClose: () => void;
    boardId: string;
    board?: Board | null;
    onCreated: (specId: number) => void;
    onOpenChange?: (open: boolean) => void;
}

interface PlannerAgentOption {
    value: string;
    label: string;
    models: AgentConfig['models'];
    defaultModel?: string;
    premiumModel?: string;
}

function buildPlannerAgentOptions(agents: AgentConfig[]): PlannerAgentOption[] {
    return agents.map((agent) => ({
        value: agent.name,
        label: agent.name.charAt(0).toUpperCase() + agent.name.slice(1),
        models: agent.models,
        defaultModel: agent.default_model || undefined,
        premiumModel: agent.premium_model || undefined,
    }));
}

function preferredPlannerModel(option: PlannerAgentOption | undefined): string {
    if (!option) return '';
    return (
        option.premiumModel ||
        option.defaultModel ||
        option.models.find((model) => model.is_default)?.name ||
        option.models[0]?.name ||
        ''
    );
}

function plannerModelLabel(option: PlannerAgentOption | undefined, model: AgentConfig['models'][number]): string {
    if (option?.premiumModel === model.name) return `${model.name} (premium)`;
    if (model.is_default) return `${model.name} (default)`;
    return model.name;
}

function extractTitle(content: string): string {
    const match = content.match(/^#\s+(.+)$/m);
    return match ? match[1].trim() : '';
}

const STORAGE_KEY = 'create-spec-modal-flags';

function loadSavedFlags() {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) {
            const parsed = JSON.parse(saved);
            return {
                quick: Boolean(parsed.quick),
                auto: Boolean(parsed.auto),
                skipReflection: Boolean(parsed.skipReflection),
            };
        }
    } catch {
        // Ignore parse errors
    }
    return { quick: false, auto: false, skipReflection: false };
}

export function CreateSpecModal({ open, onClose, boardId, board, onCreated, onOpenChange }: CreateSpecModalProps) {
    const service = useService();
    const { toast } = useToast();
    const fileInputRef = useRef<HTMLInputElement>(null);

    const savedFlags = loadSavedFlags();
    const [title, setTitle] = useState('');
    const [content, setContent] = useState('');
    const [agent, setAgent] = useState('claude');
    const [model, setModel] = useState('');
    const [availableAgents, setAvailableAgents] = useState<AgentConfig[]>([]);
    const [agentsLoading, setAgentsLoading] = useState(false);
    const [quick, setQuick] = useState(savedFlags.quick);
    const [auto, setAuto] = useState(savedFlags.auto);
    const [skipReflection, setSkipReflection] = useState(savedFlags.skipReflection);
    const [loading, setLoading] = useState(false);
    const [titleError, setTitleError] = useState('');
    const [contentError, setContentError] = useState('');
    const [optionsOpen, setOptionsOpen] = useState(false);
    const [specFiles, setSpecFiles] = useState<{ name: string; path: string }[]>([]);
    const [specFilesLoading, setSpecFilesLoading] = useState(false);
    const [specFileKey, setSpecFileKey] = useState(0);

    // Save flags to localStorage whenever they change
    useEffect(() => {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({ quick, auto, skipReflection }));
        } catch {
            // Ignore storage errors
        }
    }, [quick, auto, skipReflection]);

    const plannerAgentOptions = buildPlannerAgentOptions(availableAgents);
    const selectedAgentOption =
        plannerAgentOptions.find((option) => option.value === agent) || plannerAgentOptions[0];

    useEffect(() => {
        if (!open) {
            setTitle('');
            setContent('');
            setAgent('claude');
            setModel('');
            setAvailableAgents([]);
            setAgentsLoading(false);
            setLoading(false);
            setTitleError('');
            setContentError('');
            setSpecFiles([]);
            setSpecFilesLoading(false);
        }
    }, [open]);

    useEffect(() => {
        if (!open) return;

        let cancelled = false;
        const loadAgents = async () => {
            setAgentsLoading(true);
            try {
                const agents = await service.fetchBoardAgents(boardId);
                if (!cancelled) {
                    setAvailableAgents(agents);
                }
            } catch {
                if (!cancelled) {
                    setAvailableAgents([]);
                }
            } finally {
                if (!cancelled) {
                    setAgentsLoading(false);
                }
            }
        };

        const loadSpecFiles = async () => {
            setSpecFilesLoading(true);
            try {
                const files = await service.fetchBoardSpecFiles(boardId);
                if (!cancelled) {
                    setSpecFiles(files);
                }
            } catch {
                if (!cancelled) {
                    setSpecFiles([]);
                }
            } finally {
                if (!cancelled) {
                    setSpecFilesLoading(false);
                }
            }
        };

        void loadAgents();
        void loadSpecFiles();
        return () => {
            cancelled = true;
        };
    }, [open, boardId, service]);

    useEffect(() => {
        if (!selectedAgentOption) return;
        if (selectedAgentOption.value !== agent) {
            setAgent(selectedAgentOption.value);
            return;
        }

        const availableModelNames = new Set(selectedAgentOption.models.map((entry) => entry.name));
        if (!model || (availableModelNames.size > 0 && !availableModelNames.has(model))) {
            setModel(preferredPlannerModel(selectedAgentOption));
        }
    }, [agent, model, selectedAgentOption]);

    const handleContentChange = (nextValue: string) => {
        setContent(nextValue);
        if (contentError) {
            setContentError('');
        }
        if (!title.trim()) {
            const inferredTitle = extractTitle(nextValue);
            if (inferredTitle) {
                setTitle(inferredTitle);
                setTitleError('');
            }
        }
    };

    const handleFileUpload = (event: ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (loadEvent) => {
            const text = loadEvent.target?.result;
            if (typeof text !== 'string') return;
            handleContentChange(text);
        };
        reader.readAsText(file);
        event.target.value = '';
    };

    const handleSpecFileSelect = async (filePath: string) => {
        if (!filePath) return;
        try {
            const { content: fileContent } = await service.readBoardSpecFile(boardId, filePath);
            handleContentChange(fileContent);
        } catch (error) {
            toast({
                title: 'Failed to load spec file',
                description: error instanceof Error ? error.message : String(error),
                variant: 'destructive',
            });
        } finally {
            // Reset the select so the same file can be picked again.
            setSpecFileKey((k) => k + 1);
        }
    };

    const handleSubmit = async () => {
        const nextTitle = title.trim();
        const nextContent = content.trim();

        let hasError = false;
        if (!nextTitle) {
            setTitleError('Title is required.');
            hasError = true;
        }
        if (!nextContent) {
            setContentError('Spec content is required.');
            hasError = true;
        }
        if (hasError) {
            return;
        }

        setLoading(true);
        try {
            const plannerConfig = {
                agent,
                ...(model && { model }),
                ...(quick && { quick: true }),
                ...(auto && { auto: true }),
                ...(skipReflection && { skip_reflection: true }),
            };

            const specId = await (service as unknown as {
                createPlanningSpec: (data: {
                    title: string;
                    content: string;
                    boardId: string;
                    plannerConfig: Record<string, unknown>;
                }) => Promise<number>;
            }).createPlanningSpec({
                title: nextTitle,
                content: nextContent,
                boardId,
                plannerConfig,
            });

            onCreated(specId);
        } catch (error) {
            toast({
                title: 'Failed to create spec',
                description: error instanceof Error ? error.message : String(error),
                variant: 'destructive',
            });
        } finally {
            setLoading(false);
        }
    };

    const handleOpenChange = (nextOpen: boolean) => {
        if (!nextOpen && !loading) {
            onOpenChange?.(false) || onClose();
        }
    };

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent className="flex h-[85vh] flex-col overflow-hidden p-0 sm:max-w-[90vw] lg:max-w-6xl">
                {/* Header */}
                <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-2 pr-12 shrink-0">
                    <DialogTitle className="flex items-center gap-1.5 text-sm">
                        <Sparkles className="size-3.5 text-primary" />
                        Create spec
                    </DialogTitle>
                    <div className="flex items-center gap-2">
                        <Popover>
                            <PopoverTrigger asChild>
                                <Button variant="ghost" size="sm" className="h-6 gap-1 px-2 text-[11px] text-muted-foreground hover:text-foreground">
                                    <Terminal className="size-3" />
                                    CLI
                                </Button>
                            </PopoverTrigger>
                            <PopoverContent side="bottom" align="end" className="w-[500px] max-h-[70vh] overflow-y-auto">
                                <OdinGuideContent
                                    workingDir={board?.workingDir}
                                    needsInit={board ? !board.odinInitialized : false}
                                />
                            </PopoverContent>
                        </Popover>
                        <Badge variant="outline" className="gap-1 text-[11px]">
                            <FolderOpen className="size-3" />
                            {board?.name || `Board #${boardId}`}
                        </Badge>
                    </div>
                </div>

                {/* Body */}
                <div className="flex min-h-0 flex-1 flex-col px-5 py-2.5 gap-2">
                        {/* Title */}
                        <div className="space-y-1 shrink-0">
                                <Label htmlFor="create-spec-title" className="text-xs">Title</Label>
                                <Input
                                    id="create-spec-title"
                                    value={title}
                                    onChange={(event) => {
                                        setTitle(event.target.value);
                                        if (titleError) setTitleError('');
                                    }}
                                    placeholder="Build a production-ready planning flow"
                                    className="h-8 text-sm"
                                />
                                {titleError ? <p className="text-xs text-destructive">{titleError}</p> : null}
                            </div>

                        {/* Spec content */}
                        <div className="flex flex-col gap-1 min-h-0 flex-1">
                                <div className="flex items-center justify-between gap-3">
                                    <Label htmlFor="create-spec-content" className="text-xs">Spec content</Label>
                                    <div className="flex items-center gap-1.5">
                                        {specFiles.length > 0 && (
                                            <Select key={specFileKey} onValueChange={handleSpecFileSelect}>
                                                <SelectTrigger className="h-6 w-auto gap-1 border-none bg-transparent px-2 text-xs text-muted-foreground shadow-none hover:text-foreground">
                                                    <FileText className="size-3" />
                                                    <SelectValue placeholder={specFilesLoading ? 'Loading…' : 'From specs/'} />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {specFiles.map((f) => (
                                                        <SelectItem key={f.path} value={f.path} className="text-xs">
                                                            {f.name}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        )}
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            className="h-6 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
                                            onClick={() => fileInputRef.current?.click()}
                                        >
                                            <Upload className="size-3" />
                                            Upload
                                        </Button>
                                        <input
                                            ref={fileInputRef}
                                            type="file"
                                            accept=".md,.txt"
                                            className="hidden"
                                            onChange={handleFileUpload}
                                        />
                                    </div>
                                </div>
                                <MarkdownEditor
                                    value={content}
                                    onChange={(value) => handleContentChange(value)}
                                    placeholder={`# Build a planning modal\n\n## Goal\nShip a cleaner spec creation experience.\n\n## Acceptance criteria\n- Modal is simple and clear`}
                                    rows={8}
                                    className="min-h-[200px] flex-1"
                                />
                                {contentError ? <p className="text-xs text-destructive">{contentError}</p> : null}
                            </div>

                        {/* Planning options — collapsible */}
                        <div className="rounded-md border border-border bg-muted/20 overflow-hidden shrink-0">
                                <button
                                    type="button"
                                    onClick={() => setOptionsOpen((prev) => !prev)}
                                    className="flex w-full items-center justify-between px-2 py-1.5 text-[11px] font-medium text-muted-foreground uppercase tracking-wide hover:text-foreground transition-colors"
                                >
                                    Planning options
                                    <ChevronDown className={`size-3.5 transition-transform ${optionsOpen ? 'rotate-180' : ''}`} />
                                </button>
                                <div className={`${optionsOpen ? 'px-2 pb-2 space-y-2' : 'hidden'}`}>

                                {/* Agent + Model */}
                                <div className="grid grid-cols-2 gap-2">
                                    <div className="space-y-1">
                                        <Label htmlFor="create-spec-agent" className="text-xs">Agent</Label>
                                        <Select
                                            value={agent}
                                            onValueChange={(value) => {
                                                const nextAgent = value;
                                                const nextOption = plannerAgentOptions.find((option) => option.value === nextAgent);
                                                setAgent(nextAgent);
                                                setModel(preferredPlannerModel(nextOption));
                                            }}
                                        >
                                            <SelectTrigger id="create-spec-agent" className="h-8 w-full text-xs">
                                                <SelectValue placeholder="Select agent" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {plannerAgentOptions.map((option) => (
                                                    <SelectItem key={option.value} value={option.value} className="text-xs">
                                                        {option.label}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-1">
                                        <Label htmlFor="create-spec-model" className="text-xs">Model</Label>
                                        <Select
                                            value={model}
                                            onValueChange={setModel}
                                            disabled={agentsLoading || selectedAgentOption?.models.length === 0}
                                        >
                                            <SelectTrigger id="create-spec-model" className="h-8 w-full text-xs">
                                                <SelectValue
                                                    placeholder={agentsLoading ? 'Loading...' : 'Planner default'}
                                                />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {(selectedAgentOption?.models || []).map((option) => (
                                                    <SelectItem key={option.name} value={option.name} className="text-xs">
                                                        {plannerModelLabel(selectedAgentOption, option)}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>

                                {/* Extra Settings */}
                                <div className="grid grid-cols-3 gap-2">
                                    <label className={`flex cursor-pointer items-center gap-2.5 rounded-md border px-3 py-2 transition-colors ${quick ? 'border-primary/50 bg-primary/5' : 'border-border bg-background hover:border-primary/30'}`}>
                                        <Checkbox
                                            checked={quick}
                                            onCheckedChange={(checked) => setQuick(Boolean(checked))}
                                        />
                                        <div>
                                            <span className="block text-sm font-medium leading-none">Quick</span>
                                            <span className="mt-0.5 block text-xs text-muted-foreground">Skip exploration</span>
                                        </div>
                                    </label>
                                    <label className={`flex cursor-pointer items-center gap-2.5 rounded-md border px-3 py-2 transition-colors ${auto ? 'border-primary/50 bg-primary/5' : 'border-border bg-background hover:border-primary/30'}`}>
                                        <Checkbox
                                            checked={auto}
                                            onCheckedChange={(checked) => setAuto(Boolean(checked))}
                                        />
                                        <div>
                                            <span className="block text-sm font-medium leading-none">Auto</span>
                                            <span className="mt-0.5 block text-xs text-muted-foreground">Non-interactive</span>
                                        </div>
                                    </label>
                                    <label className={`flex cursor-pointer items-center gap-2.5 rounded-md border px-3 py-2 transition-colors ${skipReflection ? 'border-primary/50 bg-primary/5' : 'border-border bg-background hover:border-primary/30'}`}>
                                        <Checkbox
                                            checked={skipReflection}
                                            onCheckedChange={(checked) => setSkipReflection(Boolean(checked))}
                                        />
                                        <div>
                                            <span className="block text-sm font-medium leading-none">Skip Reflection</span>
                                            <span className="mt-0.5 block text-xs text-muted-foreground">No auto-review</span>
                                        </div>
                                    </label>
                                </div>
                                </div>
                        </div>
                    </div>

                {/* Footer */}
                <div className="flex justify-end gap-2 border-t border-border px-5 py-2 shrink-0">
                    <Button variant="outline" size="sm" onClick={() => handleOpenChange(false)} disabled={loading}>
                        Cancel
                    </Button>
                    <Button size="sm" onClick={handleSubmit} disabled={loading}>
                        {loading ? 'Creating...' : 'Create spec'}
                    </Button>
                </div>
            </DialogContent>
        </Dialog>
    );
}
