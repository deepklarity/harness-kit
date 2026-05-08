import { Plus, Terminal } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface BoardEmptyStateProps {
    onCreateSpec: () => void;
    workingDir?: string | null;
}

const SPEC_EXAMPLE = `# Hello World

Write "Hello World" to test.html.`;

function StepBadge({ n }: { n: number }) {
    return (
        <span
            className="flex items-center justify-center size-6 rounded-full bg-primary text-primary-foreground text-xs font-semibold shrink-0"
            aria-hidden="true"
        >
            {n}
        </span>
    );
}

function MockTerminal() {
    return (
        <div className="rounded-lg overflow-hidden border border-border" style={{ background: '#1A1A1A' }}>
            <div className="flex items-center gap-2 px-3 py-2 border-b border-white/10">
                <span className="size-2.5 rounded-full bg-[#FF5F57]" aria-hidden="true" />
                <span className="size-2.5 rounded-full bg-[#FEBC2E]" aria-hidden="true" />
                <span className="size-2.5 rounded-full bg-[#28C840]" aria-hidden="true" />
                <span className="ml-2 text-[11px] text-white/50 font-mono">odin plan — live</span>
            </div>
            <div className="px-4 py-3 font-mono text-xs leading-relaxed text-white/85">
                <div><span className="text-white/40">$</span> odin plan hello.md</div>
                <div className="text-emerald-400">✓ Spec parsed</div>
                <div className="text-emerald-400">✓ Generated 1 task</div>
                <div className="flex items-center text-primary">
                    <span>→ Pushing to board</span>
                    <span
                        className="inline-block w-1.5 h-3.5 bg-emerald-400 ml-1 animate-pulse"
                        aria-hidden="true"
                    />
                </div>
            </div>
        </div>
    );
}

export function BoardEmptyState({ onCreateSpec, workingDir }: BoardEmptyStateProps) {
    const cliCommand = `cd ${workingDir || '/path/to/project'} && odin plan spec.md`;

    return (
        <div className="max-w-2xl mx-auto mt-8">
            <div className="bg-card border border-border rounded-2xl p-7 shadow-none">
                <div className="flex items-start justify-between gap-4 mb-6">
                    <div className="flex items-start gap-3 min-w-0">
                        <Terminal className="size-5 text-muted-foreground mt-0.5 shrink-0" aria-hidden="true" />
                        <div className="min-w-0">
                            <h3 className="text-[18px] font-medium leading-tight">Get started with Odin</h3>
                            <p className="text-sm text-muted-foreground mt-1.5 leading-relaxed">
                                Describe what you want built. Odin parses it into tasks and they appear on this board automatically.
                            </p>
                        </div>
                    </div>
                    <Button onClick={onCreateSpec} className="gap-1.5 shrink-0">
                        <Plus className="size-4" aria-hidden="true" />
                        New spec
                    </Button>
                </div>

                <div className="space-y-6">
                    <section>
                        <div className="flex items-center gap-3 mb-2">
                            <StepBadge n={1} />
                            <h4 className="font-semibold text-sm">
                                Click{' '}
                                <code className="px-1.5 py-0.5 rounded bg-muted/70 border border-border font-mono text-[12px]">
                                    New → Spec
                                </code>{' '}
                                and describe your build
                            </h4>
                        </div>
                        <p className="text-sm text-muted-foreground ml-9 mb-3">
                            Markdown describing what to build.
                        </p>
                        <pre className="ml-9 rounded-lg bg-muted/60 border border-border p-3 font-mono text-xs leading-relaxed overflow-x-auto whitespace-pre-line">
                            {SPEC_EXAMPLE}
                        </pre>
                    </section>

                    <section>
                        <div className="flex items-center gap-3 mb-2">
                            <StepBadge n={2} />
                            <h4 className="font-semibold text-sm">
                                Click{' '}
                                <code className="px-1.5 py-0.5 rounded bg-muted/70 border border-border font-mono text-[12px]">
                                    Create spec
                                </code>{' '}
                                to enter plan mode
                            </h4>
                        </div>
                        <p className="text-sm text-muted-foreground ml-9 mb-3">
                            Odin parses it and streams tasks live.
                        </p>
                        <div className="ml-9">
                            <MockTerminal />
                        </div>
                    </section>
                </div>

                <div className="mt-7 pt-5 border-t border-border">
                    <details className="group">
                        <summary className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                            <Terminal className="size-4" aria-hidden="true" />
                            <span>
                                Prefer the CLI? Use{' '}
                                <code className="px-1 rounded bg-muted/60 font-mono text-[12px]">odin plan</code>{' '}
                                from your terminal
                            </span>
                        </summary>
                        <div className="mt-3 ml-6">
                            <pre className="rounded-lg bg-muted/60 border border-border p-3 font-mono text-xs select-all overflow-x-auto">
                                {cliCommand}
                            </pre>
                        </div>
                    </details>
                </div>
            </div>
        </div>
    );
}
