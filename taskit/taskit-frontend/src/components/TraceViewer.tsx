import { useState, useMemo } from 'react';
import {
    ChevronRight, Copy, Check, Zap,
    Wrench, Terminal, Activity, Box, User, Bot,
    FileCode, FileText, Search, ChevronUp, ExternalLink,
    Clock, AlertTriangle
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Types & Parsing
// ---------------------------------------------------------------------------

interface TraceEvent {
    index: number;
    type: string;
    subtype?: string;
    raw: Record<string, unknown>;
}

type FilterKey = 'user' | 'assistant' | 'tool' | 'system' | 'usage' | 'other';



function classifyEvent(obj: Record<string, unknown>): FilterKey {
    // Odin event classification (action-based)
    if ('action' in obj && 'run_id' in obj) {
        const action = obj.action as string;
        if (action.startsWith('task_') || action === 'execution_result_posted') return 'tool';
        if (action.startsWith('decompose') || action === 'decomposition_complete') return 'assistant';
        if (action.startsWith('plan_') || action.startsWith('run_') || action === 'quota_fetched' || action === 'dep_warning') return 'system';
        return 'other';
    }

    // Event classification (type-based, works across all formats)
    const type = obj.type as string | undefined;
    if (!type) {
        if ('modelUsage' in obj) return 'usage';
        return 'other';
    }
    if (type === 'user') return 'user';
    if (type === 'assistant' || type === 'result') return 'assistant';
    if (type === 'tool_use' || type === 'tool_result') return 'tool';
    if (type === 'system' || type === 'init') return 'system';
    if (type === 'step_finish' || type === 'step_start') return 'usage';
    if (type === 'content_block_delta' || type === 'content_block_start' || type === 'content_block_stop') return 'system';
    if (type === 'message_start' || type === 'message_stop' || type === 'message_delta' || type === 'message') return 'system';
    // Codex types
    if (type === 'thread.started' || type === 'turn.started') return 'system';
    if (type === 'item.completed' || type === 'item.started') {
        const item = obj.item as Record<string, unknown> | undefined;
        if (item?.type === 'agent_message') return 'assistant';
        if (item?.type === 'mcp_tool_call' || item?.type === 'command_execution') return 'tool';
        if (item?.type === 'reasoning') return 'system';
        return 'other';
    }
    if (type === 'turn.completed') return 'usage';
    // MiniMax
    if (type === 'text') return 'assistant';
    return 'other';
}

function parseTrace(raw: string): TraceEvent[] {
    const events: TraceEvent[] = [];
    for (const line of raw.split('\n')) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
            const obj = JSON.parse(trimmed);
            if (typeof obj === 'object' && obj !== null) {
                events.push({
                    index: events.length + 1,
                    type: (obj.type as string) || Object.keys(obj)[0] || 'unknown',
                    subtype: obj.subtype as string | undefined,
                    raw: obj,
                });
            }
        } catch {
            events.push({
                index: events.length + 1,
                type: 'text',
                raw: { text: trimmed },
            });
        }
    }
    return events;
}

function formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
}


interface TokenSummary {
    totalInput: number;
    totalOutput: number;
    cacheRead: number;
    cacheWrite: number;
    models: string[];
}

function extractTokenSummary(events: TraceEvent[]): TokenSummary | null {
    // Claude Code: modelUsage summary object
    for (const ev of events) {
        if ('modelUsage' in ev.raw) {
            const usage = ev.raw.modelUsage as Record<string, Record<string, number>>;
            let totalInput = 0, totalOutput = 0, cacheRead = 0, cacheWrite = 0;
            const models: string[] = [];
            for (const [model, data] of Object.entries(usage)) {
                models.push(model);
                totalInput += data.inputTokens || 0;
                totalOutput += data.outputTokens || 0;
                cacheRead += data.cacheReadInputTokens || 0;
                cacheWrite += data.cacheCreationInputTokens || 0;
            }
            if (totalInput || totalOutput) {
                return { totalInput, totalOutput, cacheRead, cacheWrite, models };
            }
        }
    }

    // Gemini/GLM (MCP Agent): type:"result" with stats object
    for (const ev of events) {
        if (ev.raw.type === 'result' && ev.raw.stats) {
            const stats = ev.raw.stats as Record<string, number>;
            return {
                totalInput: stats.input_tokens || stats.input || 0,
                totalOutput: stats.output_tokens || stats.output || 0,
                cacheRead: stats.cached || 0,
                cacheWrite: 0,
                models: [],
            };
        }
    }

    // Codex (OpenAI): turn.completed with usage object
    for (const ev of events) {
        if (ev.raw.type === 'turn.completed' && ev.raw.usage) {
            const usage = ev.raw.usage as Record<string, number>;
            return {
                totalInput: usage.input_tokens || 0,
                totalOutput: usage.output_tokens || 0,
                cacheRead: usage.cached_input_tokens || 0,
                cacheWrite: 0,
                models: [],
            };
        }
    }

    // Claude Code / Qwen: per-assistant-message usage
    let totalInput = 0, totalOutput = 0, cacheRead = 0, cacheWrite = 0;
    const models: string[] = [];
    for (const ev of events) {
        // Qwen/Claude per-message usage (in message.usage)
        const msg = ev.raw.message as Record<string, unknown> | undefined;
        if (msg?.usage) {
            const u = msg.usage as Record<string, number>;
            totalInput += u.input_tokens || 0;
            totalOutput += u.output_tokens || 0;
            cacheRead += u.cache_read_input_tokens || 0;
            cacheWrite += u.cache_creation_input_tokens || 0;
            const model = msg.model as string | undefined;
            if (model && !models.includes(model)) models.push(model);
        }
    }
    if (totalInput || totalOutput) return { totalInput, totalOutput, cacheRead, cacheWrite, models };

    // MiniMax / Claude Code: step_finish with tokens in part or at top level
    totalInput = 0; totalOutput = 0; cacheRead = 0; cacheWrite = 0;
    for (const ev of events) {
        if (ev.type === 'step_finish') {
            const part = ev.raw.part as Record<string, unknown> | undefined;
            const tokens = (part?.tokens || ev.raw.tokens) as Record<string, unknown> | undefined;
            if (tokens) {
                totalInput += (tokens.input as number) || 0;
                totalOutput += (tokens.output as number) || 0;
                const cache = tokens.cache as Record<string, number> | undefined;
                if (cache) {
                    cacheRead += cache.read || 0;
                    cacheWrite += cache.write || 0;
                }
            }
        }
    }
    if (totalInput || totalOutput) return { totalInput, totalOutput, cacheRead, cacheWrite, models: [] };
    return null;
}

// ---------------------------------------------------------------------------
// Timeline Parsing & Rendering
// ---------------------------------------------------------------------------

export type TimelineNode =
    | { type: 'system_init'; model: string; tools: string[]; raw?: Record<string, unknown>[]; }
    | { type: 'tool'; toolUseId: string; toolName: string; input: Record<string, unknown>; result?: unknown; isError?: boolean; raw?: Record<string, unknown>[]; model?: string; }
    | { type: 'text'; role: string; text: string; raw?: Record<string, unknown>[]; model?: string; }
    | { type: 'odin_phase'; phase: 'planning' | 'execution'; label: string; raw?: Record<string, unknown>[]; }
    | { type: 'odin_task'; taskId: string; agent: string; title: string; status: 'assigned' | 'started' | 'completed' | 'failed' | 'blocked' | 'interrupted'; durationMs?: number; output?: string; model?: string; errorReason?: string; raw?: Record<string, unknown>[]; }
    | { type: 'odin_event'; action: string; label: string; detail?: string; timestamp?: string; raw?: Record<string, unknown>[]; };

// ---------------------------------------------------------------------------
// Trace Format Detection
// ---------------------------------------------------------------------------

type TraceFormat = 'claude_code' | 'odin' | 'mcp_agent' | 'codex' | 'minimax' | 'unknown';

function detectTraceFormat(events: TraceEvent[]): TraceFormat {
    const sample = events.slice(0, 10);
    // Odin: action-based orchestration events
    if (sample.some(ev => 'action' in ev.raw && 'run_id' in ev.raw)) return 'odin';
    // Codex (OpenAI): item.completed / thread.started wrappers
    if (sample.some(ev => {
        const t = ev.raw.type as string;
        return t === 'thread.started' || t === 'turn.started' || t === 'item.completed' || t === 'item.started';
    })) return 'codex';
    // MiniMax (Codex CLI): camelCase sessionID + part object
    if (sample.some(ev => 'sessionID' in ev.raw && 'part' in ev.raw)) return 'minimax';
    // MCP Agent (Gemini/GLM): flat events with top-level tool_name or type:"init" with session_id
    if (sample.some(ev => 'tool_name' in ev.raw || (ev.raw.type === 'init' && 'session_id' in ev.raw))) return 'mcp_agent';
    // Claude Code (also handles Qwen — same nested message.content[] structure)
    if (sample.some(ev => Array.isArray(ev.raw.content) || ev.raw.type === 'system' || ev.raw.type === 'step_finish' || 'modelUsage' in ev.raw ||
        (ev.raw.message && Array.isArray((ev.raw.message as Record<string, unknown>).content)))) return 'claude_code';
    return 'unknown';
}

// ---------------------------------------------------------------------------
// Claude Code Timeline Builder (existing logic, extracted)
// ---------------------------------------------------------------------------

function buildClaudeCodeTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];
    const currentToolResultRequests: Record<string, TimelineNode> = {};

    for (const ev of events) {
        const ctg = classifyEvent(ev.raw);
        if (ctg === 'usage' || ctg === 'system') {
            if (ev.raw.type === 'system' && ev.raw.subtype === 'init') {
                nodes.push({
                    type: 'system_init',
                    model: String(ev.raw.model || 'Unknown'),
                    tools: (ev.raw.tools as string[]) || [],
                    raw: [ev.raw]
                });
            }
            continue;
        }

        const raw = ev.raw;
        let contentArr: Record<string, unknown>[] = [];
        let role = ((raw.message as Record<string, unknown>)?.role || raw.role || 'unknown') as string;
        const model = (raw.message as Record<string, unknown>)?.model as string | undefined;

        if (raw.message && Array.isArray((raw.message as Record<string, unknown>).content)) {
            contentArr = (raw.message as Record<string, unknown>).content as Record<string, unknown>[];
        } else if (Array.isArray(raw.content)) {
            contentArr = raw.content as Record<string, unknown>[];
            role = raw.role as string;
        } else if (raw.content) {
            contentArr = [raw.content as Record<string, unknown>];
            role = raw.role as string;
        } else {
            continue;
        }

        for (const block of contentArr) {
            if (block.type === 'text') {
                nodes.push({ type: 'text', role, text: String(block.text), raw: [ev.raw], model });
            } else if (block.type === 'tool_use') {
                const node: TimelineNode = {
                    type: 'tool',
                    toolUseId: String(block.id),
                    toolName: String(block.name),
                    input: (block.input || {}) as Record<string, unknown>,
                    raw: [ev.raw],
                    model
                };
                nodes.push(node);
                currentToolResultRequests[node.toolUseId] = node;
            } else if (block.type === 'tool_result' && block.tool_use_id) {
                const trId = String(block.tool_use_id);
                if (currentToolResultRequests[trId]) {
                    const linked = currentToolResultRequests[trId];
                    if (linked.type === 'tool') {
                        linked.result = block.content;
                        linked.isError = !!block.is_error;
                    }
                    linked.raw?.push(ev.raw);
                }
            }
        }
    }
    return nodes;
}

// ---------------------------------------------------------------------------
// Odin Timeline Builder
// ---------------------------------------------------------------------------

function formatDuration(ms: number): string {
    if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`;
    return `${(ms / 1000).toFixed(1)}s`;
}

function buildOdinTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];
    const taskNodes = new Map<string, TimelineNode & { type: 'odin_task' }>();
    let inExecution = false;
    let planningPhaseInserted = false;

    for (const ev of events) {
        const raw = ev.raw;
        const action = raw.action as string;
        const metadata = (raw.metadata || {}) as Record<string, unknown>;
        const taskId = raw.task_id as string | undefined;

        // Phase headers
        if (!planningPhaseInserted && (action === 'plan_started' || action === 'run_started')) {
            nodes.push({ type: 'odin_phase', phase: 'planning', label: 'Planning' });
            planningPhaseInserted = true;
        }

        if (!inExecution && action === 'task_started') {
            nodes.push({ type: 'odin_phase', phase: 'execution', label: 'Execution' });
            inExecution = true;
        }

        // Task lifecycle events → odin_task nodes
        if (action === 'task_assigned' && taskId) {
            const node: TimelineNode & { type: 'odin_task' } = {
                type: 'odin_task',
                taskId,
                agent: (raw.agent as string) || 'unknown',
                title: (metadata.title as string) || `Task ${taskId}`,
                status: 'assigned',
                raw: [raw],
            };
            taskNodes.set(taskId, node);
            nodes.push(node);
        } else if (action === 'task_started' && taskId) {
            const existing = taskNodes.get(taskId);
            if (existing) {
                existing.status = 'started';
                existing.raw?.push(raw);
            } else {
                const node: TimelineNode & { type: 'odin_task' } = {
                    type: 'odin_task',
                    taskId,
                    agent: (raw.agent as string) || 'unknown',
                    title: `Task ${taskId}`,
                    status: 'started',
                    raw: [raw],
                };
                taskNodes.set(taskId, node);
                nodes.push(node);
            }
        } else if ((action === 'task_completed' || action === 'task_failed') && taskId) {
            const existing = taskNodes.get(taskId);
            if (existing) {
                existing.status = action === 'task_completed' ? 'completed' : 'failed';
                existing.durationMs = raw.duration_ms as number | undefined;
                existing.output = raw.output as string | undefined;
                existing.raw?.push(raw);
            }
        } else if (action === 'task_blocked' && taskId) {
            const existing = taskNodes.get(taskId);
            if (existing) {
                existing.status = 'blocked';
                existing.errorReason = (metadata.reason as string) || 'Blocked by dependencies';
                existing.raw?.push(raw);
            }
        } else if (action === 'task_interrupted' && taskId) {
            const existing = taskNodes.get(taskId);
            if (existing) {
                existing.status = 'interrupted';
                existing.raw?.push(raw);
            }
        } else if (action === 'execution_result_posted') {
            const erpTaskId = metadata.task_id as string;
            if (erpTaskId) {
                const existing = taskNodes.get(erpTaskId);
                if (existing) {
                    existing.model = metadata.model as string | undefined;
                    if (!existing.durationMs) existing.durationMs = metadata.duration_ms as number | undefined;
                    existing.raw?.push(raw);
                }
            }

            // Informational events → odin_event nodes
        } else if (action === 'decompose_started') {
            nodes.push({
                type: 'odin_event', action,
                label: `Decomposing with ${raw.agent || 'agent'}...`,
                timestamp: raw.timestamp as string, raw: [raw],
            });
        } else if (action === 'decompose_completed') {
            const dur = raw.duration_ms as number | undefined;
            nodes.push({
                type: 'odin_event', action,
                label: 'Decomposition complete',
                detail: dur ? formatDuration(dur) : undefined,
                timestamp: raw.timestamp as string, raw: [raw],
            });
        } else if (action === 'decomposition_complete') {
            nodes.push({
                type: 'odin_event', action,
                label: `${metadata.sub_task_count || '?'} sub-tasks created`,
                timestamp: raw.timestamp as string, raw: [raw],
            });
        } else if (action === 'plan_completed') {
            nodes.push({
                type: 'odin_event', action,
                label: `Plan complete — ${metadata.task_count || '?'} tasks`,
                detail: metadata.spec_id as string | undefined,
                timestamp: raw.timestamp as string, raw: [raw],
            });
        } else if (action === 'quota_fetched') {
            nodes.push({
                type: 'odin_event', action,
                label: 'Agent quotas fetched',
                timestamp: raw.timestamp as string, raw: [raw],
            });
        } else if (action === 'dep_warning') {
            nodes.push({
                type: 'odin_event', action,
                label: `Dependency warning: ${metadata.symbolic_dep || 'unknown'}`,
                detail: metadata.task as string | undefined,
                timestamp: raw.timestamp as string, raw: [raw],
            });
        } else if (action === 'run_completed') {
            nodes.push({
                type: 'odin_event', action,
                label: `Run complete — ${metadata.task_count || '?'} tasks`,
                timestamp: raw.timestamp as string, raw: [raw],
            });
        } else if (action === 'plan_started' || action === 'run_started') {
            nodes.push({
                type: 'odin_event', action,
                label: action === 'plan_started' ? 'Plan started' : 'Run started',
                timestamp: raw.timestamp as string, raw: [raw],
            });
        } else {
            // Future unknown Odin actions
            nodes.push({
                type: 'odin_event', action: action || 'unknown',
                label: action?.replace(/_/g, ' ') || 'Unknown event',
                timestamp: raw.timestamp as string, raw: [raw],
            });
        }
    }

    return nodes;
}

// ---------------------------------------------------------------------------
// MCP Agent (Gemini/GLM) Timeline Builder
// ---------------------------------------------------------------------------

function buildMcpAgentTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];
    const pendingTools: Record<string, TimelineNode & { type: 'tool' }> = {};
    let lastDeltaNode: (TimelineNode & { type: 'text' }) | null = null;

    for (const ev of events) {
        const raw = ev.raw;
        const type = raw.type as string;

        if (type === 'init') {
            nodes.push({
                type: 'system_init',
                model: String(raw.model || 'Unknown'),
                tools: [],
                raw: [raw],
            });
            lastDeltaNode = null;
            continue;
        }

        if (type === 'message') {
            const role = raw.role as string || 'unknown';
            const text = raw.content as string || '';
            const isDelta = !!raw.delta;

            // Merge consecutive assistant deltas into one text node
            if (isDelta && lastDeltaNode && lastDeltaNode.role === role) {
                lastDeltaNode.text += text;
                lastDeltaNode.raw?.push(raw);
                continue;
            }

            const node: TimelineNode & { type: 'text' } = { type: 'text', role, text, raw: [raw] };
            nodes.push(node);
            lastDeltaNode = isDelta ? node : null;
            continue;
        }

        if (type === 'tool_use') {
            const toolId = raw.tool_id as string || '';
            const node: TimelineNode & { type: 'tool' } = {
                type: 'tool',
                toolUseId: toolId,
                toolName: raw.tool_name as string || 'unknown',
                input: (raw.parameters || {}) as Record<string, unknown>,
                raw: [raw],
            };
            nodes.push(node);
            if (toolId) pendingTools[toolId] = node;
            lastDeltaNode = null;
            continue;
        }

        if (type === 'tool_result') {
            const toolId = raw.tool_id as string || '';
            const pending = pendingTools[toolId];
            if (pending) {
                pending.result = raw.output;
                pending.isError = raw.status === 'error';
                pending.raw?.push(raw);
            }
            lastDeltaNode = null;
            continue;
        }

        // 'result' events are token summaries — skip for timeline
        // Other unknown events — skip
    }
    return nodes;
}

// ---------------------------------------------------------------------------
// Codex (OpenAI) Timeline Builder
// ---------------------------------------------------------------------------

function buildCodexTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];
    // Track in-progress tool calls to attach results on completion
    const pendingItems: Record<string, TimelineNode & { type: 'tool' }> = {};

    for (const ev of events) {
        const raw = ev.raw;
        const type = raw.type as string;
        const item = raw.item as Record<string, unknown> | undefined;

        if (type === 'thread.started' || type === 'turn.started') {
            // Bookkeeping — skip
            continue;
        }

        if (type === 'item.started' && item) {
            const itemType = item.type as string;
            const itemId = item.id as string || '';

            if (itemType === 'mcp_tool_call') {
                const node: TimelineNode & { type: 'tool' } = {
                    type: 'tool',
                    toolUseId: itemId,
                    toolName: item.tool as string || 'unknown',
                    input: (item.arguments || {}) as Record<string, unknown>,
                    raw: [raw],
                };
                nodes.push(node);
                pendingItems[itemId] = node;
            } else if (itemType === 'command_execution') {
                const node: TimelineNode & { type: 'tool' } = {
                    type: 'tool',
                    toolUseId: itemId,
                    toolName: 'Bash',
                    input: { command: item.command as string || '' },
                    raw: [raw],
                };
                nodes.push(node);
                pendingItems[itemId] = node;
            }
            continue;
        }

        if (type === 'item.completed' && item) {
            const itemType = item.type as string;
            const itemId = item.id as string || '';

            if (itemType === 'reasoning') {
                // Reasoning/thinking — skip or show as collapsed
                continue;
            }

            if (itemType === 'agent_message') {
                nodes.push({
                    type: 'text',
                    role: 'assistant',
                    text: item.text as string || '',
                    raw: [raw],
                });
                continue;
            }

            // Completed tool calls — attach result to pending or create new
            if (itemType === 'mcp_tool_call' || itemType === 'command_execution') {
                const pending = pendingItems[itemId];
                if (pending) {
                    if (itemType === 'mcp_tool_call') {
                        const result = item.result as Record<string, unknown> | null;
                        pending.result = result?.structured_content || result?.content;
                        pending.isError = item.status === 'failed';
                    } else {
                        pending.result = item.aggregated_output as string || '';
                        pending.isError = (item.exit_code as number) !== 0;
                    }
                    pending.raw?.push(raw);
                } else {
                    // No matching started event — create standalone node
                    const node: TimelineNode & { type: 'tool' } = {
                        type: 'tool',
                        toolUseId: itemId,
                        toolName: itemType === 'command_execution' ? 'Bash' : (item.tool as string || 'unknown'),
                        input: itemType === 'command_execution'
                            ? { command: item.command as string || '' }
                            : (item.arguments || {}) as Record<string, unknown>,
                        raw: [raw],
                    };
                    if (itemType === 'mcp_tool_call') {
                        const result = item.result as Record<string, unknown> | null;
                        node.result = result?.structured_content || result?.content;
                        node.isError = item.status === 'failed';
                    } else {
                        node.result = item.aggregated_output as string || '';
                        node.isError = (item.exit_code as number) !== 0;
                    }
                    nodes.push(node);
                }
                continue;
            }
        }

        // turn.completed — token summary; skip for timeline
    }
    return nodes;
}

// ---------------------------------------------------------------------------
// MiniMax Timeline Builder
// ---------------------------------------------------------------------------

function buildMinimaxTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];

    for (const ev of events) {
        const raw = ev.raw;
        const type = raw.type as string;
        const part = raw.part as Record<string, unknown> | undefined;
        if (!part) continue;

        const partType = part.type as string;

        if (type === 'step_start' || type === 'step_finish') {
            // Bookkeeping — skip for timeline (tokens extracted separately)
            continue;
        }

        if (type === 'tool_use' && partType === 'tool') {
            const state = (part.state || {}) as Record<string, unknown>;
            const node: TimelineNode & { type: 'tool' } = {
                type: 'tool',
                toolUseId: part.callID as string || part.id as string || '',
                toolName: part.tool as string || 'unknown',
                input: (state.input || {}) as Record<string, unknown>,
                result: state.output,
                isError: state.status === 'error',
                raw: [raw],
            };
            nodes.push(node);
            continue;
        }

        if (type === 'text' && partType === 'text') {
            nodes.push({
                type: 'text',
                role: 'assistant',
                text: part.text as string || '',
                raw: [raw],
            });
            continue;
        }
    }
    return nodes;
}

// ---------------------------------------------------------------------------
// Timeline Builder (format dispatch)
// ---------------------------------------------------------------------------

function buildTimeline(events: TraceEvent[]): TimelineNode[] {
    const format = detectTraceFormat(events);
    if (format === 'odin') return buildOdinTimeline(events);
    if (format === 'mcp_agent') return buildMcpAgentTimeline(events);
    if (format === 'codex') return buildCodexTimeline(events);
    if (format === 'minimax') return buildMinimaxTimeline(events);
    return buildClaudeCodeTimeline(events);
}

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

    return null;
}

function TimelineView({ events }: { events: TraceEvent[] }) {
    const nodes = useMemo(() => buildTimeline(events), [events]);

    return (
        <div className="flex flex-col flex-1 p-5 overflow-y-auto space-y-6 bg-[#0e0e11] custom-scrollbar">
            {nodes.map((n, i) => (
                <TimelineNodeRenderer key={i} node={n} />
            ))}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Main TraceViewer
// ---------------------------------------------------------------------------

export function TraceViewer({ traceText }: { traceText: string }) {
    const [copyState, setCopyState] = useState<'idle' | 'copied'>('idle');
    const [viewMode, setViewMode] = useState<'timeline' | 'raw'>('timeline');

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
        <div className="mt-2 rounded-xl border border-zinc-800/80 bg-[#0e0e11] overflow-hidden shadow-sm flex flex-col max-h-[700px]">
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
                    {traceText}
                </pre>
            )}

            {viewMode === 'timeline' && (
                <TimelineView events={events} />
            )}

        </div>
    );
}
