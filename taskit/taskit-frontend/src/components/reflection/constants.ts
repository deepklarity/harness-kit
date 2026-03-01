/** Shared reflection UI constants — single source of truth for verdict styling. */

export const VERDICT_STYLES: Record<string, { bg: string; text: string; border: string }> = {
    PASS: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/20' },
    NEEDS_WORK: { bg: 'bg-orange-500/10', text: 'text-orange-300', border: 'border-orange-500/20' },
    FAIL: { bg: 'bg-red-500/10', text: 'text-red-400', border: 'border-red-500/20' },
};

/** Tailwind prose classes for rendering markdown in reflection report sections. */
export const PROSE_CLASSES =
    'prose prose-sm prose-invert max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:text-foreground/90 prose-strong:text-foreground/90 prose-code:text-indigo-300 prose-code:bg-indigo-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-code:before:content-none prose-code:after:content-none';
