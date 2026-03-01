import { useState } from 'react';
import type { Board } from '@/types';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from '@/components/ui/dialog';
import { Check, Copy, Terminal } from 'lucide-react';

interface OdinGuideModalProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    board: Board | null;
}

function CopyButton({ text }: { text: string }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = () => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
    };

    return (
        <button
            type="button"
            onClick={handleCopy}
            className="shrink-0 p-1 rounded hover:bg-accent/60 text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Copy to clipboard"
        >
            {copied ? <Check className="size-3.5 text-green-500" /> : <Copy className="size-3.5" />}
        </button>
    );
}

function CommandBlock({ command }: { command: string }) {
    return (
        <div className="flex items-center gap-2 rounded bg-muted/50 border px-3 py-2 font-mono text-xs">
            <code className="flex-1 select-all break-all">{command}</code>
            <CopyButton text={command} />
        </div>
    );
}

export function OdinGuideModal({ open, onOpenChange, board }: OdinGuideModalProps) {
    const workingDir = board?.workingDir;
    const needsInit = board && !board.odinInitialized;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Terminal className="size-5" />
                        Create a Spec via CLI
                    </DialogTitle>
                    <DialogDescription>
                        Specs and tasks are created from your terminal using the Odin CLI.
                    </DialogDescription>
                </DialogHeader>

                <OdinGuideContent
                    workingDir={workingDir}
                    needsInit={!!needsInit}
                />

                <DialogFooter showCloseButton />
            </DialogContent>
        </Dialog>
    );
}

interface OdinGuideContentProps {
    workingDir?: string | null;
    needsInit: boolean;
}

export function OdinGuideContent({ workingDir, needsInit }: OdinGuideContentProps) {
    if (!workingDir) {
        return (
            <div className="text-sm text-muted-foreground py-2">
                <p className="mb-2 font-medium text-foreground">Set a project directory first</p>
                <p>
                    Go to <span className="font-semibold">Settings</span> and set a working directory for this board, then come back here.
                </p>
            </div>
        );
    }

    let step = 1;

    return (
        <div className="flex flex-col gap-4 text-sm">
            {needsInit && (
                <StepBlock n={step++} title="Initialize Odin in your project">
                    <CommandBlock command={`cd ${workingDir} && odin init`} />
                </StepBlock>
            )}

            <StepBlock n={step++} title="Write a spec file">
                <p className="text-muted-foreground text-xs mb-2">
                    Create a markdown file describing what you want built:
                </p>
                <div className="rounded bg-muted/50 border px-3 py-2 font-mono text-xs text-muted-foreground whitespace-pre-line">
                    {'# Hello World\n\nWrite "Hello World" to test.html.'}
                </div>
            </StepBlock>

            <StepBlock n={step++} title="Run odin plan">
                <CommandBlock command={`cd ${workingDir} && odin plan spec.md`} />
                <p className="text-muted-foreground text-xs mt-1.5">
                    This parses the spec and creates tasks on this board. They'll appear here automatically.
                </p>
            </StepBlock>
        </div>
    );
}

function StepBlock({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
    return (
        <div>
            <div className="flex items-center gap-2 mb-2">
                <span className="flex items-center justify-center size-5 rounded-full bg-primary text-primary-foreground text-[10px] font-bold shrink-0">{n}</span>
                <span className="font-medium">{title}</span>
            </div>
            <div className="ml-7">
                {children}
            </div>
        </div>
    );
}
