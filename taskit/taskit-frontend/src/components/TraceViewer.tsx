import { useState, useMemo, useRef, useCallback } from 'react';
import {
    ChevronRight, Copy, Check, Zap,
    Wrench, Terminal, Activity, Box, User, Bot,
    FileCode, FileText, Search, ChevronUp, ChevronDown, ExternalLink,
    Clock, AlertTriangle, X
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/utils'
import {
    parseTrace,
    buildTimelineWithMeta,
    extractTokenSummary,
    formatTokens,
    formatDuration,
    type TraceEvent,
    type TimelineNode,
} from '@/utils/traceFormat'

// Re-export for callers that imported these names from TraceViewer.
export type { TraceEvent, TimelineNode };

// ---------------------------------------------------------------------------
// Timeline Parsing & Rendering
// ---------------------------------------------------------------------------


const ODIN_STATUS_CONFIG: Record<string, { color: string; label: string }> = {
    assigned: { color: 'text-zinc-400 bg-zinc-800', label: 'Assigned' },
    started: { color: 'text-blue-400 bg-blue-500/10', label: 'Running' },
    completed: { color: 'text-emerald-400 bg-emerald-500/10', label: 'Done' },
    failed: { color: 'text-red-400 bg-red-500/10', label: 'Failed' },
    blocked: { color: 'text-amber-400 bg-amber-500/10', label: 'Blocked' },
    interrupted: { color: 'text-orange-400 bg-orange-500/10', label: 'Interrupted' },
};

function OdinTaskRenderer({ node }: { node: TimelineNode & { type: 'odin_task' } }) {
    const [showOutput, setShowOutput] = useState(false);
    const sc = ODIN_STATUS_CONFIG[node.status] || ODIN_STATUS_CONFIG.assigned;
    const durationStr = node.durationMs ? formatDuration(node.durationMs) : null;

    return (
        <div className={cn(
            "flex gap-4 relative group",
            (node.status === 'failed' || node.status === 'blocked') && "bg-red-500/[0.03] rounded-md p-2 -mx-2"
        )}>
            <div className="flex flex-col items-center mt-1 w-5 shrink-0">
                <Activity className={cn("size-4",
                    node.status === 'failed' ? "text-red-400" :
                        node.status === 'completed' ? "text-emerald-400" :
                            "text-zinc-400"
                )} strokeWidth={1.5} />
            </div>
            <div className="flex flex-col w-full max-w-4xl min-w-0">
                <div className="flex items-center gap-2 min-h-[24px] flex-wrap">
                    <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                        {node.agent}
                    </span>
                    <span className="text-[13px] font-medium text-[#c9c9c9] flex-1 truncate">
                        {node.title}
                    </span>
                    <span className={cn("text-[9px] font-mono font-semibold px-1.5 py-0.5 rounded", sc.color)}>
                        {sc.label}
                    </span>
                    {durationStr && (
                        <span className="text-[10px] font-mono text-zinc-500 flex items-center gap-0.5">
                            <Clock className="size-2.5" /> {durationStr}
                        </span>
                    )}
                    {node.model && (
                        <span className="text-[9px] font-mono text-zinc-600 bg-zinc-900 px-1 py-0.5 rounded">
                            {node.model}
                        </span>
                    )}
                </div>
                <span className="text-[9px] font-mono text-zinc-600 mt-0.5">
                    task #{node.taskId}
                </span>
                {node.errorReason && (
                    <div className="text-[11px] text-red-400/80 mt-1 font-mono flex items-center gap-1">
                        <AlertTriangle className="size-3" /> {node.errorReason}
                    </div>
                )}
                {node.output && (
                    <div className="mt-1.5">
                        <button
                            onClick={() => setShowOutput(!showOutput)}
                            className="cursor-pointer text-[10px] text-zinc-500 hover:text-zinc-300 transition-colors select-none flex items-center gap-1"
                        >
                            <ChevronRight className={cn("size-3 text-zinc-600 transition-transform", showOutput && "rotate-90")} />
                            Output
                        </button>
                        {showOutput && (
                            <pre className="text-[10px] text-zinc-400 bg-[rgba(10,10,11,0.5)] border border-[#2b2b2e] rounded-md p-2.5 mt-1 overflow-auto max-h-[200px] whitespace-pre-wrap font-mono leading-relaxed">
                                {node.output}
                            </pre>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

/** Normalize tool names across different agent formats to canonical names */
function normalizeToolName(name: string): string {
    // Strip MCP prefixes: "mcp__taskit__add_comment" → "taskit_add_comment"
    const stripped = name.replace(/^mcp__/, '');
    return stripped;
}

/** Check if a tool name is a TaskIt MCP tool */
function isTaskItTool(name: string): boolean {
    const n = normalizeToolName(name);
    return n.startsWith('taskit_') || n.startsWith('taskit_taskit_');
}

/** Get a short display label for TaskIt tools */
function taskItToolLabel(name: string): string {
    const n = normalizeToolName(name);
    // taskit_taskit_add_comment → add_comment, taskit_add_comment → add_comment
    const action = n.replace(/^taskit_taskit_/, '').replace(/^taskit_/, '');
    return `TaskIt ${action.replace(/_/g, ' ')}`;
}

function ToolTimelineNodeRenderer({ node }: { node: TimelineNode & { type: 'tool' } }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const toolName = normalizeToolName(node.toolName);

    let label = 'Ran tool';
    let Icon = Wrench;
    let commandLine = '';
    let fileEdited = '';
    let resultText = '';
    let isBashOrCommand = false;
    let additions = 0, deletions = 0;

    if (typeof node.result === 'string') {
        resultText = node.result;
    } else if (Array.isArray(node.result)) {
        resultText = node.result.map(r => r.text || r.content || JSON.stringify(r)).join('\n');
    } else if (node.result) {
        resultText = JSON.stringify(node.result, null, 2);
    }

    if (toolName === 'Bash' || toolName === 'bash' || toolName === 'run_command' || toolName === 'run_shell_command') {
        label = 'Ran command';
        Icon = Terminal;
        isBashOrCommand = true;
        commandLine = String(node.input?.command || node.input?.CommandLine || '');
    } else if (toolName === 'Edit' || toolName === 'Write' || toolName === 'write' || toolName === 'edit' || toolName === 'write_file' || toolName === 'replace_file_content' || toolName === 'multi_replace_file_content' || toolName === 'NotebookEdit') {
        fileEdited = String(node.input?.file_path || node.input?.TargetFile || node.input?.path || 'file');

        if (node.input?.ReplacementContent) {
            additions = String(node.input.ReplacementContent).split('\n').length;
            deletions = (node.input.EndLine as number || 0) - (node.input.StartLine as number || 0) + 1;
        } else if (node.input?.content && typeof node.input.content === 'string') {
            additions = node.input.content.split('\n').length;
        } else if (node.input?.CodeContent) {
            additions = String(node.input.CodeContent).split('\n').length;
        } else if (node.input?.ReplacementChunks) {
            // heuristic
            additions = (node.input.ReplacementChunks as any[]).reduce((acc, curr) => acc + (curr.ReplacementContent?.split('\n').length || 0), 0);
            deletions = (node.input.ReplacementChunks as any[]).reduce((acc, curr) => acc + ((curr.EndLine || 0) - (curr.StartLine || 0) + 1), 0);
        }

        label = `Edited ${fileEdited.split('/').pop()}`;
        Icon = FileCode;
        resultText = ''; // mostly hide output for Edit
    } else if (toolName === 'Read' || toolName === 'read_file' || toolName === 'view_file' || toolName === 'view_file_outline') {
        fileEdited = String(node.input?.file_path || node.input?.AbsolutePath || node.input?.path || 'file');
        label = `Read ${fileEdited.split('/').pop()}`;
        Icon = FileText;
    } else if (toolName === 'Glob' || toolName === 'glob' || toolName === 'Grep' || toolName === 'grep_search' || toolName === 'list_dir' || toolName === 'list_directory' || toolName === 'ToolSearch') {
        label = `Searched files`;
        Icon = Search;
        commandLine = String(node.input?.pattern || node.input?.Query || node.input?.SearchDirectory || 'search');
    } else if (toolName === 'command_status') {
        label = 'Checked command status';
        Icon = Terminal;
        resultText = ''; // don't overwhelm
    } else if (toolName === 'AskUserQuestion') {
        label = 'Asked user a question';
        Icon = User;
    } else if (isTaskItTool(node.toolName)) {
        label = taskItToolLabel(node.toolName);
        Icon = Box;
        // Compact: just show the content field if present
        if (node.input?.content) {
            commandLine = String(node.input.content).substring(0, 120);
        }
    } else {
        label = `Used ${toolName}`;
        Icon = Wrench;
        commandLine = JSON.stringify(node.input);
    }

    const statText = (additions || deletions) ? (
        <span className="text-[12px] font-mono gap-1.5 flex ml-2 opacity-90 inline-flex items-center">
            {additions > 0 && <span className="text-emerald-500">+{additions}</span>}
            {deletions > 0 && <span className="text-red-500">-{deletions}</span>}
        </span>
    ) : null;

    return (
        <div className="flex gap-4 relative group">
            <div className="flex flex-col items-center mt-1 w-5 shrink-0">
                <Icon className="size-4 text-zinc-400" strokeWidth={1.5} />
            </div>

            <div className="flex flex-col w-full max-w-4xl min-w-0">
                <div
                    className="flex items-center justify-between min-h-[24px] cursor-pointer select-none group/title"
                    onClick={() => setIsExpanded(!isExpanded)}
                >
                    <span className="text-[13px] font-medium text-[#c9c9c9] flex items-center group-hover/title:text-white transition-colors">
                        <ChevronRight className={cn("size-3.5 mr-1.5 text-zinc-500 transition-transform", isExpanded && "rotate-90")} />
                        {label}
                        {statText}
                        {node.model && <span className="text-[9px] font-mono text-zinc-600 bg-zinc-900 px-1.5 py-0.5 rounded ml-2 group-hover/title:bg-zinc-800 transition-colors">{node.model}</span>}
                        {node.toolName === 'Edit' && <span className="text-[#3b82f6] text-[10px] ml-2 font-mono bg-blue-500/10 px-1.5 py-0.5 rounded opacity-0 group-hover/title:opacity-100 transition-opacity">edit</span>}
                    </span>

                    <div className="flex gap-3 items-center opacity-80">
                        {isBashOrCommand && <span className="text-[11px] text-[#999] cursor-pointer flex items-center hover:text-white transition-colors" onClick={(e) => e.stopPropagation()}>Relocate <ExternalLink className="size-3 ml-1" /></span>}
                        {!isBashOrCommand && node.isError && <span className="text-[11px] text-[#f87171] font-mono">Exit code 1</span>}
                    </div>
                </div>

                {isExpanded && ((commandLine || resultText) && node.toolName !== 'command_status' && node.toolName !== 'Edit') && (
                    <div className="border border-[#2b2b2e] rounded-md bg-[rgba(10,10,11,0.5)] mt-1.5 overflow-hidden flex flex-col font-mono text-[11px] ml-5">
                        <div className="p-3 pb-4 max-h-[300px] overflow-y-auto custom-scrollbar flex-1 whitespace-pre-wrap leading-relaxed text-[#c9c9c9]">
                            {commandLine && <div className="mb-2 text-[#999] opacity-80 select-none">... $ {commandLine}</div>}
                            <div className={cn(node.isError && "text-[#f87171]", "break-words")}>
                                {resultText}
                            </div>
                        </div>

                        {(isBashOrCommand || node.isError) && (
                            <div className="flex items-center justify-between px-3 py-1.5 border-t border-[#2b2b2e] bg-[rgba(20,20,23,0.8)] text-[#7a7a7d] text-[10px] shrink-0">
                                <span className="flex items-center gap-1 cursor-pointer hover:text-[#d1d1d1] transition-colors select-none">Always run <ChevronUp className="size-3" /></span>
                                {node.isError ? <span className="text-[#f87171]">Exit code 1</span> : <span></span>}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

function CollapsibleTextNode({ node }: { node: TimelineNode & { type: 'text' } }) {
    const isUser = node.role === 'user';
    const isLong = node.text.length > 300;
    const [expanded, setExpanded] = useState(false);

    // User prompts are collapsed by default if long
    const shouldCollapse = isUser && isLong;

    return (
        <div className="flex gap-4">
            <div className="flex flex-col items-center mt-1 w-5 shrink-0">
                <div className={cn("flex items-center justify-center size-5 rounded-md", isUser ? "bg-zinc-800" : "bg-transparent")}>
                    {isUser ? <User className="size-3 text-zinc-400" /> : <Bot className="size-4 text-blue-400" />}
                </div>
            </div>
            <div className="flex flex-col gap-1 w-full max-w-3xl min-w-0">
                {node.model && !isUser && (
                    <div className="text-[9px] font-mono text-zinc-500 -mb-1 mt-0.5">{node.model}</div>
                )}
                {shouldCollapse && !expanded ? (
                    <button
                        onClick={() => setExpanded(true)}
                        className="cursor-pointer text-[11px] text-zinc-500 hover:text-zinc-300 transition-colors select-none flex items-center gap-1 py-1"
                    >
                        <ChevronRight className="size-3 text-zinc-600" />
                        User prompt ({Math.ceil(node.text.length / 4)} chars)
                    </button>
                ) : (
                    <div className="text-[13px] text-zinc-300 py-1 max-w-none prose prose-invert prose-p:leading-relaxed prose-pre:my-2 prose-pre:bg-zinc-900 prose-pre:p-3 prose-pre:rounded-md prose-code:text-amber-200">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{node.text}</ReactMarkdown>
                    </div>
                )}
            </div>
        </div>
    );
}

function TimelineNodeRenderer({ node }: { node: TimelineNode }) {
    if (node.type === 'text') {
        return <CollapsibleTextNode node={node} />;
    }

    if (node.type === 'tool') {
        return <ToolTimelineNodeRenderer node={node} />;
    }

    // --- Odin Phase Header ---
    if (node.type === 'odin_phase') {
        return (
            <div className="flex items-center gap-3 py-1">
                <div className="h-px flex-1 bg-zinc-800" />
                <span className={cn(
                    "text-[10px] font-bold tracking-widest uppercase px-2",
                    node.phase === 'planning' ? "text-violet-400" : "text-emerald-400"
                )}>
                    {node.label}
                </span>
                <div className="h-px flex-1 bg-zinc-800" />
            </div>
        );
    }

    // --- Odin Task ---
    if (node.type === 'odin_task') {
        return <OdinTaskRenderer node={node} />;
    }

    // --- Odin Event (informational one-liner) ---
    if (node.type === 'odin_event') {
        const isWarning = node.action === 'dep_warning';
        const isCompletion = node.action.endsWith('_completed') || node.action.endsWith('_complete');

        return (
            <div className="flex gap-4 items-center">
                <div className="flex flex-col items-center w-5 shrink-0">
                    {isWarning
                        ? <AlertTriangle className="size-3 text-amber-500" />
                        : <Box className={cn("size-3", isCompletion ? "text-emerald-500" : "text-zinc-600")} />
                    }
                </div>
                <div className="flex items-center gap-2 min-h-[20px] flex-wrap">
                    <span className={cn(
                        "text-[11px] font-mono",
                        isWarning ? "text-amber-400" : "text-zinc-400"
                    )}>
                        {node.label}
                    </span>
                    {node.detail && (
                        <span className="text-[10px] font-mono text-zinc-600">
                            {node.detail}
                        </span>
                    )}
                </div>
            </div>
        );
    }

    if (node.type === 'system_init') {
        const toolList = node.tools.join(', ');
        return (
            <div className="flex gap-4 items-start">
                <div className="flex flex-col items-center mt-1 w-5 shrink-0">
                    <Terminal className="size-4 text-purple-400" />
                </div>
                <div className="flex flex-col gap-1 w-full max-w-3xl min-w-0">
                    <div className="text-[13px] text-zinc-300 font-mono py-1">
                        System Init — <span className="text-purple-400">{node.model}</span>
                    </div>
                    {node.tools.length > 0 && (
                        <div className="text-[11px] text-zinc-500 font-mono bg-[rgba(10,10,11,0.5)] border border-[#2b2b2e] rounded-md p-2.5 mt-1 overflow-auto max-h-[150px] whitespace-pre-wrap leading-relaxed">
                            Available Tools: {toolList}
                        </div>
                    )}
                </div>
            </div>
        );
    }

    if (node.type === 'generic_event') {
        const rawText = node.event.raw.text;
        const textValue = typeof rawText === 'string' ? rawText : null;
        const preview = textValue && textValue.length > 0
            ? (textValue.length > 200 ? textValue.slice(0, 200) + '…' : textValue)
            : null;
        return (
            <div className="flex gap-4 items-start">
                <div className="flex flex-col items-center mt-1 w-5 shrink-0">
                    <Box className="size-3 text-zinc-500" />
                </div>
                <div className="flex flex-col gap-0.5 w-full max-w-3xl min-w-0">
                    <span className="text-[11px] font-mono text-zinc-400">
                        <span className="text-zinc-600">#{node.event.index}</span> {node.summary}
                    </span>
                    {preview && (
                        <span className="text-[10px] font-mono text-zinc-500 whitespace-pre-wrap break-words line-clamp-2">
                            {preview}
                        </span>
                    )}
                </div>
            </div>
        );
    }

    return null;
}

function TimelineView({ events }: { events: TraceEvent[] }) {
    const result = useMemo(() => buildTimelineWithMeta(events), [events]);
    const nodes = result.nodes;

    return (
        <div className="flex flex-col flex-1 p-5 overflow-y-auto space-y-6 bg-[#0e0e11] custom-scrollbar">
            {result.usedFallback && (
                <div className="text-[11px] text-amber-300/90 font-mono border border-amber-500/30 bg-amber-500/[0.08] rounded-md px-3 py-2 flex items-start gap-2">
                    <AlertTriangle className="size-3.5 mt-0.5 shrink-0 text-amber-400" />
                    <div className="flex flex-col gap-0.5">
                        <span className="font-semibold">Timeline unavailable for this format</span>
                        <span className="text-amber-200/70 text-[10px]">
                            {result.fallbackReason || `Detected format "${result.format}" — no structured timeline builder matched.`}
                            {' '}Showing raw events below; use the Raw tab for the original JSONL.
                        </span>
                    </div>
                </div>
            )}
            {nodes.map((n, i) => (
                <TimelineNodeRenderer key={i} node={n} />
            ))}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Inline search bar (shared by TraceViewer + TerminalOutputView)
// ---------------------------------------------------------------------------

function useTextSearch(text: string) {
    const [query, setQuery] = useState('');
    const [activeIdx, setActiveIdx] = useState(0);
    const [open, setOpen] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    const matches = useMemo(() => {
        if (!query || query.length < 2) return [];
        const q = query.toLowerCase();
        const results: number[] = [];
        let pos = 0;
        const lower = text.toLowerCase();
        while ((pos = lower.indexOf(q, pos)) !== -1) {
            results.push(pos);
            pos += q.length;
        }
        return results;
    }, [text, query]);

    const toggle = useCallback(() => {
        setOpen(v => {
            if (!v) setTimeout(() => inputRef.current?.focus(), 0);
            else { setQuery(''); setActiveIdx(0); }
            return !v;
        });
    }, []);

    const next = useCallback(() => setActiveIdx(i => (i + 1) % Math.max(matches.length, 1)), [matches.length]);
    const prev = useCallback(() => setActiveIdx(i => (i - 1 + matches.length) % Math.max(matches.length, 1)), [matches.length]);

    return { query, setQuery, activeIdx, setActiveIdx, matches, open, toggle, next, prev, inputRef };
}

function SearchBar({ search }: { search: ReturnType<typeof useTextSearch> }) {
    if (!search.open) return null;
    return (
        <div className="flex items-center gap-1.5 px-3 py-1 bg-[#141417] border-b border-[#2b2b2e] shrink-0">
            <Search className="size-3 text-zinc-500" />
            <input
                ref={search.inputRef}
                value={search.query}
                onChange={e => { search.setQuery(e.target.value); search.setActiveIdx(0); }}
                onKeyDown={e => { if (e.key === 'Enter') e.shiftKey ? search.prev() : search.next(); if (e.key === 'Escape') search.toggle(); }}
                placeholder="Search..."
                className="bg-transparent text-[11px] text-zinc-300 placeholder:text-zinc-600 outline-none flex-1 font-mono min-w-0"
            />
            {search.query.length >= 2 && (
                <span className="text-[9px] text-zinc-500 font-mono shrink-0">
                    {search.matches.length > 0 ? `${search.activeIdx + 1}/${search.matches.length}` : 'No matches'}
                </span>
            )}
            <button onClick={search.prev} className="text-zinc-500 hover:text-zinc-300 transition-colors" title="Previous (Shift+Enter)">
                <ChevronUp className="size-3" />
            </button>
            <button onClick={search.next} className="text-zinc-500 hover:text-zinc-300 transition-colors" title="Next (Enter)">
                <ChevronDown className="size-3" />
            </button>
            <button onClick={search.toggle} className="text-zinc-500 hover:text-zinc-300 transition-colors" title="Close">
                <X className="size-3" />
            </button>
        </div>
    );
}

/** Render text with search matches highlighted. Scrolls active match into view. */
function HighlightedText({ text, search }: { text: string; search: ReturnType<typeof useTextSearch> }) {
    const activeRef = useRef<HTMLSpanElement>(null);
    const { query, matches, activeIdx } = search;

    // Scroll active match into view
    const prevIdx = useRef(-1);
    if (activeRef.current && activeIdx !== prevIdx.current) {
        activeRef.current.scrollIntoView({ block: 'center', behavior: 'smooth' });
        prevIdx.current = activeIdx;
    }

    if (!query || query.length < 2 || matches.length === 0) {
        return <>{text}</>;
    }

    const parts: React.ReactNode[] = [];
    let last = 0;
    const qLen = query.length;
    matches.forEach((pos, i) => {
        if (pos > last) parts.push(text.slice(last, pos));
        const isActive = i === activeIdx;
        parts.push(
            <span
                key={i}
                ref={isActive ? activeRef : undefined}
                className={isActive ? 'bg-amber-400/40 text-amber-200 rounded-sm' : 'bg-zinc-600/50 text-zinc-200 rounded-sm'}
            >
                {text.slice(pos, pos + qLen)}
            </span>
        );
        last = pos + qLen;
    });
    if (last < text.length) parts.push(text.slice(last));
    return <>{parts}</>;
}

// ---------------------------------------------------------------------------
// Main TraceViewer
// ---------------------------------------------------------------------------

export function TraceViewer({ traceText, className }: { traceText: string; className?: string }) {
    const [copyState, setCopyState] = useState<'idle' | 'copied'>('idle');
    const [viewMode, setViewMode] = useState<'timeline' | 'raw'>('timeline');
    const search = useTextSearch(traceText);

    const events = useMemo(() => parseTrace(traceText), [traceText]);
    const tokenSummary = useMemo(() => extractTokenSummary(events), [events]);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(traceText);
            setCopyState('copied');
            setTimeout(() => setCopyState('idle'), 1400);
        } catch { }
    };

    if (events.length === 0) {
        return (
            <div className="text-xs text-muted-foreground/60 font-mono p-4 text-center border-dashed border border-border/40 rounded-lg mt-4">
                No trace data available
            </div>
        );
    }

    return (
        <div className={cn("mt-2 rounded-xl border border-zinc-800/80 bg-[#0e0e11] overflow-hidden shadow-sm flex flex-col max-h-[700px]", className)}>
            {/* Header bar */}
            <div className="flex flex-wrap items-center justify-between px-3 py-1.5 bg-[#141417] border-b border-[#2b2b2e] shrink-0">
                <div className="flex items-center gap-2">
                    <Activity className="size-3.5 text-violet-400" />
                    <span className="text-[11px] font-semibold text-zinc-300 tracking-wide">
                        Trace Explorer
                    </span>
                </div>
                <div className="flex items-center gap-3">
                    <div className="flex bg-[#1f1f22] p-0.5 rounded border border-[#2b2b2e] shadow-sm">
                        <button onClick={() => setViewMode('timeline')} className={cn("text-[9px] px-2 py-0.5 rounded font-mono transition-colors", viewMode === 'timeline' ? "bg-zinc-700 text-zinc-100 shadow" : "text-zinc-500 hover:text-zinc-300")}>Timeline</button>
                        <button onClick={() => setViewMode('raw')} className={cn("text-[9px] px-2 py-0.5 rounded font-mono transition-colors", viewMode === 'raw' ? "bg-zinc-700 text-zinc-100 shadow" : "text-zinc-500 hover:text-zinc-300")}>Raw</button>
                    </div>
                    <button
                        className={cn("text-[9px] font-mono flex items-center gap-1 transition-colors px-1.5 py-0.5 rounded", search.open ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800")}
                        onClick={search.toggle}
                        title="Search (Ctrl+F)"
                    >
                        <Search className="size-2.5" /> Search
                    </button>
                    <button
                        className="text-[9px] text-zinc-400 hover:text-zinc-200 border border-transparent hover:bg-zinc-800 font-mono flex items-center gap-1 transition-colors px-1.5 py-0.5 rounded"
                        onClick={handleCopy}
                    >
                        {copyState === 'copied'
                            ? <><Check className="size-2.5 text-emerald-400" /> Copied</>
                            : <><Copy className="size-2.5" /> Copy</>
                        }
                    </button>
                </div>
            </div>
            <SearchBar search={search} />

            {/* Token summary bar */}
            {tokenSummary && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-1 bg-[#141417] border-b border-[#2b2b2e] text-[9px] font-mono shrink-0">
                    <div className="flex items-center gap-1.5">
                        <Zap className="size-3 text-emerald-400" />
                        <span className="text-emerald-400/90">
                            {formatTokens(tokenSummary.totalInput)} <span className="text-zinc-500">in</span>
                        </span>
                        <span className="text-zinc-700">/</span>
                        <span className="text-blue-400/90">
                            {formatTokens(tokenSummary.totalOutput)} <span className="text-zinc-500">out</span>
                        </span>
                    </div>

                    {(tokenSummary.cacheRead > 0 || tokenSummary.cacheWrite > 0) && (
                        <div className="flex items-center gap-2 border-l border-zinc-800 pl-3">
                            {tokenSummary.cacheRead > 0 && (
                                <span className="text-amber-400/80">
                                    <span className="text-zinc-500">cache read:</span> {formatTokens(tokenSummary.cacheRead)}
                                </span>
                            )}
                            {tokenSummary.cacheWrite > 0 && (
                                <span className="text-orange-400/80">
                                    <span className="text-zinc-500">cache write:</span> {formatTokens(tokenSummary.cacheWrite)}
                                </span>
                            )}
                        </div>
                    )}
                    {tokenSummary.models.length > 0 && (
                        <div className="flex items-center gap-1.5 border-l border-zinc-800 pl-3">
                            <span className="text-zinc-500">models:</span>
                            <span className="text-zinc-400 truncate max-w-[150px]">
                                {tokenSummary.models.join(', ')}
                            </span>
                        </div>
                    )}
                </div>
            )}

            {viewMode === 'raw' && (
                <pre className="text-[10px] font-mono text-zinc-400 p-3 overflow-auto flex-1 whitespace-pre-wrap leading-relaxed custom-scrollbar bg-[#0e0e11]">
                    <HighlightedText text={traceText} search={search} />
                </pre>
            )}

            {viewMode === 'timeline' && (
                <TimelineView events={events} />
            )}

        </div>
    );
}

// ---------------------------------------------------------------------------
// TerminalOutputView — static terminal-style container for interactive mode
// ---------------------------------------------------------------------------

export function TerminalOutputView({ text, label }: { text: string; label?: string }) {
    const [copyState, setCopyState] = useState<'idle' | 'copied'>('idle');
    const search = useTextSearch(text);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(text);
            setCopyState('copied');
            setTimeout(() => setCopyState('idle'), 1400);
        } catch { }
    };

    if (!text.trim()) return null;

    return (
        <div className="mt-2 rounded-xl border border-zinc-800/80 bg-[#0e0e11] overflow-hidden shadow-sm flex flex-col max-h-[500px]">
            <div className="flex items-center justify-between px-3 py-1.5 bg-[#141417] border-b border-[#2b2b2e] shrink-0">
                <div className="flex items-center gap-2">
                    <div className="flex gap-1.5">
                        <span className="size-2.5 rounded-full bg-[#ff5f57]" />
                        <span className="size-2.5 rounded-full bg-[#ffbd2e]" />
                        <span className="size-2.5 rounded-full bg-[#27c93f]" />
                    </div>
                    <Terminal className="size-3.5 text-violet-400" />
                    <span className="text-[11px] font-semibold text-zinc-300 tracking-wide">
                        {label || 'Session Transcript'}
                    </span>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        className={cn("text-[9px] font-mono flex items-center gap-1 transition-colors px-1.5 py-0.5 rounded", search.open ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800")}
                        onClick={search.toggle}
                        title="Search"
                    >
                        <Search className="size-2.5" /> Search
                    </button>
                    <button
                        className="text-[9px] text-zinc-400 hover:text-zinc-200 border border-transparent hover:bg-zinc-800 font-mono flex items-center gap-1 transition-colors px-1.5 py-0.5 rounded"
                        onClick={handleCopy}
                    >
                        {copyState === 'copied'
                            ? <><Check className="size-2.5 text-emerald-400" /> Copied</>
                            : <><Copy className="size-2.5" /> Copy</>
                        }
                    </button>
                </div>
            </div>
            <SearchBar search={search} />
            <pre className="text-[11px] font-mono text-zinc-300 p-3 overflow-auto flex-1 whitespace-pre-wrap break-words leading-relaxed custom-scrollbar bg-[#0e0e11]">
                <HighlightedText text={text} search={search} />
            </pre>
        </div>
    );
}
