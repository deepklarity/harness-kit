import { Dialog, DialogContent } from '@/components/ui/dialog';

interface KeyboardShortcutsModalProps {
    open: boolean;
    onClose: () => void;
}

const isMac = () => navigator.platform.toUpperCase().includes('MAC');

function Kbd({ children }: { children: React.ReactNode }) {
    return (
        <kbd className="inline-flex h-5 items-center rounded border border-border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground pointer-events-none">
            {children}
        </kbd>
    );
}

type ShortcutRow = { label: string; keys: string[] };
type ShortcutGroup = { heading: string; rows: ShortcutRow[] };

export function KeyboardShortcutsModal({ open, onClose }: KeyboardShortcutsModalProps) {
    const modKey = isMac() ? '⌘' : 'Ctrl';

    const groups: ShortcutGroup[] = [
        {
            heading: 'NAVIGATION',
            rows: [
                { label: 'Go to Board', keys: ['G', 'B'] },
                { label: 'Go to Specs', keys: ['G', 'S'] },
                { label: 'Go to Stats', keys: ['G', 'D'] },
                { label: 'Go to Notifications', keys: ['G', 'N'] },
                { label: 'Go to Settings', keys: ['G', 'T'] },
            ],
        },
        {
            heading: 'TASKS',
            rows: [
                { label: 'Create new task', keys: ['N'] },
            ],
        },
        {
            heading: 'SYSTEM',
            rows: [
                { label: 'Open command palette', keys: [`${modKey}K`] },
                { label: 'Show keyboard shortcuts', keys: ['?'] },
            ],
        },
    ];

    return (
        <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
            <DialogContent className="sm:max-w-[400px] p-0 gap-0 overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                    <h2 className="text-sm font-semibold">Keyboard Shortcuts</h2>
                </div>
                <div className="divide-y divide-border">
                    {groups.map((group) => (
                        <div key={group.heading}>
                            <div className="px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 bg-muted/30">
                                {group.heading}
                            </div>
                            {group.rows.map((row) => (
                                <div key={row.label} className="flex items-center justify-between px-4 py-2 text-sm">
                                    <span className="text-foreground/80">{row.label}</span>
                                    <div className="flex items-center gap-1">
                                        {row.keys.map((k, i) => (
                                            <Kbd key={i}>{k}</Kbd>
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>
                    ))}
                </div>
                <div className="px-4 py-2.5 border-t border-border text-[11px] text-muted-foreground">
                    Press <Kbd>esc</Kbd> to close
                </div>
            </DialogContent>
        </Dialog>
    );
}
