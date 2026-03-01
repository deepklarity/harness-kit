import { useState } from 'react';
import type { ReflectionRequest } from '../types';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { Sparkles } from 'lucide-react';

interface ReflectionModalProps {
    taskId: string;
    taskIdShort: number;
    onClose: () => void;
    onSubmit: (params: ReflectionRequest) => Promise<void>;
}

const AGENTS = ['claude', 'gemini', 'codex', 'qwen'];

const DEFAULT_MODELS: Record<string, string> = {
    claude: 'claude-opus-4-6',
    gemini: 'gemini-2.5-pro',
    codex: 'codex-5.2',
    qwen: 'qwen3-coder',
};

const CONTEXT_OPTIONS = [
    { key: 'description', label: 'Description' },
    { key: 'comments', label: 'Comments' },
    { key: 'execution_result', label: 'Execution result' },
    { key: 'dependencies', label: 'Dependencies' },
    { key: 'metadata', label: 'Metadata (model, tokens, duration)' },
];

export function ReflectionModal({ taskId, taskIdShort, onClose, onSubmit }: ReflectionModalProps) {
    const [agent, setAgent] = useState('claude');
    const [model, setModel] = useState(DEFAULT_MODELS.claude);
    const [customPrompt, setCustomPrompt] = useState('');
    const [contextSelections, setContextSelections] = useState<string[]>(
        CONTEXT_OPTIONS.map(o => o.key)
    );
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleAgentChange = (newAgent: string) => {
        setAgent(newAgent);
        setModel(DEFAULT_MODELS[newAgent] || '');
    };

    const toggleContext = (key: string) => {
        setContextSelections(prev =>
            prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
        );
    };

    const handleSubmit = async () => {
        setIsSubmitting(true);
        try {
            await onSubmit({
                reviewer_agent: agent,
                reviewer_model: model,
                custom_prompt: customPrompt || undefined,
                context_selections: contextSelections,
            });
            onClose();
        } catch (e) {
            console.error('Failed to trigger reflection:', e);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <Dialog open onOpenChange={onClose}>
            <DialogContent className="sm:max-w-[480px]">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Sparkles className="size-5 text-indigo-400" />
                        Reflect: Task #{taskIdShort}
                    </DialogTitle>
                </DialogHeader>

                <div className="space-y-4 pt-2">
                    {/* Context selections */}
                    <div>
                        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 block">
                            Context to include
                        </label>
                        <div className="space-y-1.5">
                            {CONTEXT_OPTIONS.map(opt => (
                                <div key={opt.key} className="flex items-center gap-2">
                                    <Checkbox
                                        checked={contextSelections.includes(opt.key)}
                                        onCheckedChange={() => toggleContext(opt.key)}
                                    />
                                    <span className="text-sm">{opt.label}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Reviewer selection */}
                    <div className="flex gap-3">
                        <div className="flex-1">
                            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 block">
                                Agent
                            </label>
                            <Select value={agent} onValueChange={handleAgentChange}>
                                <SelectTrigger className="h-9">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {AGENTS.map(a => (
                                        <SelectItem key={a} value={a}>{a}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex-1">
                            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 block">
                                Model
                            </label>
                            <Input
                                value={model}
                                onChange={e => setModel(e.target.value)}
                                placeholder="e.g. claude-opus-4-6"
                                className="h-9 font-mono text-sm"
                            />
                        </div>
                    </div>

                    {/* Custom prompt */}
                    <div>
                        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5 block">
                            Custom prompt (optional)
                        </label>
                        <Textarea
                            value={customPrompt}
                            onChange={e => setCustomPrompt(e.target.value)}
                            placeholder="Focus on error handling patterns..."
                            rows={3}
                            className="text-sm"
                        />
                    </div>

                    {/* Actions */}
                    <div className="flex justify-end gap-2 pt-2">
                        <Button variant="outline" onClick={onClose}>Cancel</Button>
                        <Button
                            onClick={handleSubmit}
                            disabled={isSubmitting || !model}
                            className="bg-indigo-600 hover:bg-indigo-700 gap-1.5"
                        >
                            <Sparkles className="size-4" />
                            {isSubmitting ? 'Starting...' : 'Start Reflection'}
                        </Button>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
}
