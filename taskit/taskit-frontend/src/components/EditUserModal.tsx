import { useState } from 'react';
import { useToast } from '@/hooks/use-toast';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { MEMBER_COLORS } from '@/services/harness/HarnessTimeService';
import { cn } from '@/lib/utils';
import { Check, ChevronUp, ChevronDown, X, Plus, Star } from 'lucide-react';
import type { Member, ModelInfo } from '@/types';

interface EditUserModalProps {
    user: Member;
    onClose: () => void;
    onUpdate: (id: string, name: string, email: string, color: string, availableModels?: ModelInfo[]) => Promise<void>;
}

export function EditUserModal({ user, onClose, onUpdate }: EditUserModalProps) {
    const { toast } = useToast();
    const [name, setName] = useState(user.fullName);
    const [email, setEmail] = useState(user.email || '');
    const [color, setColor] = useState(user.color || MEMBER_COLORS[0]);
    const [loading, setLoading] = useState(false);

    const [models, setModels] = useState<ModelInfo[]>(user.availableModels || []);
    const [newModelName, setNewModelName] = useState('');
    const [newModelDesc, setNewModelDesc] = useState('');

    const hasModels = models.length > 0 || user.email?.endsWith('@odin.agent');

    const fmtPrice = (v: number | null | undefined) => {
        if (v == null) return '—';
        if (v === 0) return 'free';
        return `$${v % 1 === 0 ? v.toFixed(0) : v.toFixed(2)}`;
    };

    const moveModel = (index: number, direction: -1 | 1) => {
        const newIndex = index + direction;
        if (newIndex < 0 || newIndex >= models.length) return;
        const updated = [...models];
        const [item] = updated.splice(index, 1);
        updated.splice(newIndex, 0, item);
        setModels(updated);
    };

    const removeModel = (index: number) => {
        setModels(prev => prev.filter((_, i) => i !== index));
    };

    const setDefaultModel = (index: number) => {
        setModels(prev => prev.map((m, i) => ({ ...m, is_default: i === index })));
    };

    const addModel = () => {
        if (!newModelName.trim()) return;
        const isFirst = models.length === 0;
        setModels(prev => [...prev, {
            name: newModelName.trim(),
            description: newModelDesc.trim(),
            is_default: isFirst,
        }]);
        setNewModelName('');
        setNewModelDesc('');
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!name.trim()) return;
        setLoading(true);
        try {
            await onUpdate(user.id, name, email, color, hasModels ? models : undefined);
            onClose();
        } catch {
            toast({
                title: "Error",
                description: "Failed to update user.",
                variant: "destructive"
            });
        } finally {
            setLoading(false);
        }
    };

    return (
        <Dialog open onOpenChange={onClose}>
            <DialogContent className={cn("sm:max-w-[400px]", hasModels && "sm:max-w-[580px]")}>
                <DialogHeader>
                    <DialogTitle>Edit User</DialogTitle>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                    <div className="flex flex-col gap-2">
                        <Label>Name</Label>
                        <Input
                            autoFocus
                            required
                            value={name}
                            onChange={e => setName(e.target.value)}
                        />
                    </div>
                    <div className="flex flex-col gap-2">
                        <Label>Email</Label>
                        <Input
                            type="email"
                            value={email}
                            onChange={e => setEmail(e.target.value)}
                            placeholder="Leave blank to keep current"
                        />
                    </div>
                    <div className="flex flex-col gap-2">
                        <Label>Assigned Color</Label>
                        <div className="flex flex-wrap gap-2">
                            {MEMBER_COLORS.map((c) => (
                                <button
                                    key={c}
                                    type="button"
                                    onClick={() => setColor(c)}
                                    className={cn(
                                        "w-8 h-8 rounded-full border-2 transition-all flex items-center justify-center",
                                        color === c ? "border-foreground scale-110" : "border-transparent hover:opacity-80"
                                    )}
                                    style={{ backgroundColor: c }}
                                >
                                    {color === c && <Check className="w-4 h-4 text-white drop-shadow-md" />}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Model Management Section */}
                    {hasModels && (
                        <div className="flex flex-col gap-2">
                            <Label>Available Models</Label>
                            <div className="border border-border rounded-lg overflow-hidden">
                                {models.length > 0 ? (
                                    <div className="divide-y divide-border">
                                        {models.map((model, index) => (
                                            <div key={`${model.name}-${index}`} className="flex items-center gap-2 px-3 py-2 text-sm group hover:bg-secondary/30">
                                                <button
                                                    type="button"
                                                    onClick={() => setDefaultModel(index)}
                                                    title={model.is_default ? 'Default model' : 'Set as default'}
                                                    className={cn(
                                                        "shrink-0 transition-colors",
                                                        model.is_default ? "text-yellow-500" : "text-muted-foreground/30 hover:text-yellow-500/60"
                                                    )}
                                                >
                                                    <Star className="size-3.5" fill={model.is_default ? 'currentColor' : 'none'} />
                                                </button>
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-baseline gap-2">
                                                        <span className="font-mono text-xs font-medium">{model.name}</span>
                                                        {model.description && (
                                                            <span className="text-muted-foreground text-xs">{model.description}</span>
                                                        )}
                                                    </div>
                                                    {(model.input_price_per_1m_tokens != null || model.output_price_per_1m_tokens != null) && (
                                                        <div className="text-[10px] text-muted-foreground/60 font-mono mt-0.5">
                                                            <span title="Input price per 1M tokens">in {fmtPrice(model.input_price_per_1m_tokens)}</span>
                                                            <span className="mx-1">/</span>
                                                            <span title="Output price per 1M tokens">out {fmtPrice(model.output_price_per_1m_tokens)}</span>
                                                            {model.cache_read_price_per_1m_tokens != null && (
                                                                <>
                                                                    <span className="mx-1">/</span>
                                                                    <span title="Cache read price per 1M tokens">cache {fmtPrice(model.cache_read_price_per_1m_tokens)}</span>
                                                                </>
                                                            )}
                                                        </div>
                                                    )}
                                                </div>
                                                <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                                                    <button type="button" onClick={() => moveModel(index, -1)} disabled={index === 0}
                                                        className="p-0.5 hover:bg-secondary rounded disabled:opacity-20">
                                                        <ChevronUp className="size-3.5" />
                                                    </button>
                                                    <button type="button" onClick={() => moveModel(index, 1)} disabled={index === models.length - 1}
                                                        className="p-0.5 hover:bg-secondary rounded disabled:opacity-20">
                                                        <ChevronDown className="size-3.5" />
                                                    </button>
                                                    <button type="button" onClick={() => removeModel(index)}
                                                        className="p-0.5 hover:bg-destructive/20 hover:text-destructive rounded ml-1">
                                                        <X className="size-3.5" />
                                                    </button>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="px-3 py-4 text-xs text-muted-foreground text-center italic">No models configured</div>
                                )}
                                <div className="border-t border-border bg-secondary/20 px-3 py-2 flex gap-2">
                                    <Input
                                        placeholder="Model name"
                                        value={newModelName}
                                        onChange={e => setNewModelName(e.target.value)}
                                        className="h-7 text-xs flex-1"
                                        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addModel(); } }}
                                    />
                                    <Input
                                        placeholder="Description (optional)"
                                        value={newModelDesc}
                                        onChange={e => setNewModelDesc(e.target.value)}
                                        className="h-7 text-xs flex-1"
                                        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addModel(); } }}
                                    />
                                    <Button type="button" size="sm" variant="outline" onClick={addModel} disabled={!newModelName.trim()} className="h-7 px-2">
                                        <Plus className="size-3.5" />
                                    </Button>
                                </div>
                            </div>
                        </div>
                    )}

                    <div className="flex gap-3 justify-end mt-2">
                        <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
                        <Button type="submit" disabled={loading || !name.trim()}>
                            {loading ? 'Updating...' : 'Save Changes'}
                        </Button>
                    </div>
                </form>
            </DialogContent>
        </Dialog>
    );
}
