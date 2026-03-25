import { useEffect, useMemo, useState } from 'react';
import type { DetectedIde } from '../types';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

const IDE_STYLES: Record<string, { bg: string; text: string; mark: string }> = {
    cursor: { bg: 'bg-sky-500/15', text: 'text-sky-300', mark: 'C' },
    vscode: { bg: 'bg-blue-500/15', text: 'text-blue-300', mark: 'VS' },
    zed: { bg: 'bg-orange-500/15', text: 'text-orange-300', mark: 'Z' },
};

export function IdeBadge({ ide, muted = false }: { ide: DetectedIde; muted?: boolean }) {
    const style = IDE_STYLES[ide.id] || { bg: 'bg-secondary', text: 'text-foreground', mark: ide.label.slice(0, 1).toUpperCase() };
    return (
        <span className={`inline-flex items-center gap-2 rounded-full border border-border/60 px-2 py-1 ${muted ? 'opacity-70' : ''}`}>
            <span className={`inline-flex min-w-6 justify-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${style.bg} ${style.text}`}>
                {style.mark}
            </span>
            <span className="text-xs font-medium">{ide.label}</span>
        </span>
    );
}

interface IdeSetupModalProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    detectedIdes: DetectedIde[];
    initialIdeId?: string | null;
    saving?: boolean;
    onSave: (ideId: string) => Promise<void> | void;
    title?: string;
    description?: string;
}

export function IdeSetupModal({
    open,
    onOpenChange,
    detectedIdes,
    initialIdeId,
    saving = false,
    onSave,
    title = 'Configure IDE',
    description = 'Choose the IDE TaskIt should use when opening the Odin project root.',
}: IdeSetupModalProps) {
    const defaultIdeId = useMemo(() => {
        if (initialIdeId && detectedIdes.some(ide => ide.id === initialIdeId)) return initialIdeId;
        return detectedIdes[0]?.id ?? '';
    }, [detectedIdes, initialIdeId]);
    const [selectedIdeId, setSelectedIdeId] = useState(defaultIdeId);

    useEffect(() => {
        if (!open) return;
        setSelectedIdeId(defaultIdeId);
    }, [defaultIdeId, open]);

    const selectedIde = detectedIdes.find(ide => ide.id === selectedIdeId) ?? null;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                </DialogHeader>

                {detectedIdes.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
                        <div className="font-medium text-foreground">No supported IDEs detected</div>
                        <div className="mt-2">
                            Install or expose one of the supported IDE CLIs on this machine: Cursor, VS Code, or Zed.
                        </div>
                    </div>
                ) : (
                    <div className="space-y-2">
                        {detectedIdes.map(ide => {
                            const selected = ide.id === selectedIdeId;
                            return (
                                <button
                                    key={ide.id}
                                    type="button"
                                    className={`flex w-full items-center justify-between rounded-lg border px-3 py-3 text-left transition-colors ${selected ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/40'}`}
                                    onClick={() => setSelectedIdeId(ide.id)}
                                >
                                    <IdeBadge ide={ide} />
                                    {selected && <Badge variant="outline" className="text-[10px]">Selected</Badge>}
                                </button>
                            );
                        })}
                    </div>
                )}

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button
                        onClick={() => selectedIde && onSave(selectedIde.id)}
                        disabled={!selectedIde || saving}
                    >
                        {saving ? 'Saving...' : 'Save IDE'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
