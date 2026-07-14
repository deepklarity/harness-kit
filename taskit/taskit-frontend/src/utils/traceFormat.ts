export interface TraceEvent {
    index: number;
    type: string;
    subtype?: string;
    raw: Record<string, unknown>;
}

export type FilterKey = 'user' | 'assistant' | 'tool' | 'system' | 'usage' | 'other';

export function classifyEvent(obj: Record<string, unknown>): FilterKey {
    if ('action' in obj && 'run_id' in obj) {
        const action = obj.action as string;
        if (action.startsWith('task_') || action === 'execution_result_posted') return 'tool';
        if (action.startsWith('decompose') || action === 'decomposition_complete') return 'assistant';
        if (action.startsWith('plan_') || action.startsWith('run_') || action === 'quota_fetched' || action === 'dep_warning') return 'system';
        return 'other';
    }

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
    if (type === 'thread.started' || type === 'turn.started') return 'system';
    if (type === 'item.completed' || type === 'item.started') {
        const item = obj.item as Record<string, unknown> | undefined;
        if (item?.type === 'agent_message') return 'assistant';
        if (item?.type === 'mcp_tool_call' || item?.type === 'command_execution') return 'tool';
        if (item?.type === 'reasoning') return 'system';
        return 'other';
    }
    if (type === 'turn.completed') return 'usage';
    if (type === 'text') return 'assistant';
    return 'other';
}

export function parseTrace(raw: string): TraceEvent[] {
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

export interface TokenSummary {
    totalInput: number;
    totalOutput: number;
    cacheRead: number;
    cacheWrite: number;
    models: string[];
}

export function formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
}

export function extractTokenSummary(events: TraceEvent[]): TokenSummary | null {
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

    let totalInput = 0, totalOutput = 0, cacheRead = 0, cacheWrite = 0;
    const models: string[] = [];
    for (const ev of events) {
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

export type TimelineNode =
    | { type: 'system_init'; model: string; tools: string[]; raw?: Record<string, unknown>[]; }
    | { type: 'tool'; toolUseId: string; toolName: string; input: Record<string, unknown>; result?: unknown; isError?: boolean; raw?: Record<string, unknown>[]; model?: string; }
    | { type: 'text'; role: string; text: string; raw?: Record<string, unknown>[]; model?: string; }
    | { type: 'odin_phase'; phase: 'planning' | 'execution'; label: string; raw?: Record<string, unknown>[]; }
    | { type: 'odin_task'; taskId: string; agent: string; title: string; status: 'assigned' | 'started' | 'completed' | 'failed' | 'blocked' | 'interrupted'; durationMs?: number; output?: string; model?: string; errorReason?: string; raw?: Record<string, unknown>[]; }
    | { type: 'odin_event'; action: string; label: string; detail?: string; timestamp?: string; raw?: Record<string, unknown>[]; }
    | { type: 'generic_event'; event: TraceEvent; summary: string; };

export type TraceFormat = 'claude_code' | 'odin' | 'mcp_agent' | 'codex' | 'minimax' | 'kilo' | 'unknown';

export interface DetectionResult {
    format: TraceFormat;
    /** Count of events inspected before a signature matched (capped at sampleWindow). */
    inspected: number;
    sampleWindow: number;
}

const DETECTION_SAMPLE_WINDOW = 200;

export function detectTraceFormat(events: TraceEvent[]): TraceFormat {
    return detectTraceFormatWithMeta(events).format;
}

export function detectTraceFormatWithMeta(events: TraceEvent[]): DetectionResult {
    const window = events.slice(0, DETECTION_SAMPLE_WINDOW);
    const NO_MATCH = Number.POSITIVE_INFINITY;
    const inspect = (predicate: (ev: TraceEvent) => boolean): number => {
        const limit = Math.min(events.length, DETECTION_SAMPLE_WINDOW);
        for (let i = 0; i < limit; i++) {
            if (predicate(events[i])) return i + 1;
        }
        return NO_MATCH;
    };

    const odinIdx = inspect(ev => 'action' in ev.raw && 'run_id' in ev.raw);
    if (odinIdx !== NO_MATCH) return { format: 'odin', inspected: odinIdx, sampleWindow: DETECTION_SAMPLE_WINDOW };

    const codexIdx = inspect(ev => {
        const t = ev.raw.type as string;
        return t === 'thread.started' || t === 'turn.started' || t === 'item.completed' || t === 'item.started';
    });
    if (codexIdx !== NO_MATCH) return { format: 'codex', inspected: codexIdx, sampleWindow: DETECTION_SAMPLE_WINDOW };

    const minimaxIdx = inspect(ev => 'sessionID' in ev.raw && 'part' in ev.raw);
    if (minimaxIdx !== NO_MATCH) return { format: 'minimax', inspected: minimaxIdx, sampleWindow: DETECTION_SAMPLE_WINDOW };

    const kiloIdx = inspect(ev => {
        const t = ev.raw.type as string;
        if (t !== 'text' && t !== 'tool_use' && t !== 'tool_result' && t !== 'step_start' && t !== 'step_finish') return false;
        if ('sessionID' in ev.raw || 'part' in ev.raw) return false;
        if ('tool_name' in ev.raw || 'session_id' in ev.raw) return false;
        if (Array.isArray(ev.raw.content) || (typeof ev.raw.message === 'object')) return false;
        return true;
    });
    if (kiloIdx !== NO_MATCH) return { format: 'kilo', inspected: kiloIdx, sampleWindow: DETECTION_SAMPLE_WINDOW };

    const mcpIdx = inspect(ev => 'tool_name' in ev.raw || (ev.raw.type === 'init' && 'session_id' in ev.raw));
    if (mcpIdx !== NO_MATCH) return { format: 'mcp_agent', inspected: mcpIdx, sampleWindow: DETECTION_SAMPLE_WINDOW };

    const claudeIdx = inspect(ev =>
        Array.isArray(ev.raw.content) || ev.raw.type === 'system' || ev.raw.type === 'step_finish' || 'modelUsage' in ev.raw ||
        (!!ev.raw.message && Array.isArray((ev.raw.message as Record<string, unknown>).content))
    );
    if (claudeIdx !== NO_MATCH) return { format: 'claude_code', inspected: claudeIdx, sampleWindow: DETECTION_SAMPLE_WINDOW };

    void window;
    return { format: 'unknown', inspected: Math.min(events.length, DETECTION_SAMPLE_WINDOW), sampleWindow: DETECTION_SAMPLE_WINDOW };
}

export function buildClaudeCodeTimeline(events: TraceEvent[]): TimelineNode[] {
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
                        linked.raw?.push(ev.raw);
                    }
                }
            }
        }
    }
    return nodes;
}

export function formatDuration(ms: number): string {
    if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`;
    return `${(ms / 1000).toFixed(1)}s`;
}

export function buildOdinTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];
    const taskNodes = new Map<string, TimelineNode & { type: 'odin_task' }>();
    let inExecution = false;
    let planningPhaseInserted = false;

    for (const ev of events) {
        const raw = ev.raw;
        const action = raw.action as string;
        const metadata = (raw.metadata || {}) as Record<string, unknown>;
        const taskId = raw.task_id as string | undefined;

        if (!planningPhaseInserted && (action === 'plan_started' || action === 'run_started')) {
            nodes.push({ type: 'odin_phase', phase: 'planning', label: 'Planning' });
            planningPhaseInserted = true;
        }

        if (!inExecution && action === 'task_started') {
            nodes.push({ type: 'odin_phase', phase: 'execution', label: 'Execution' });
            inExecution = true;
        }

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
            nodes.push({
                type: 'odin_event', action: action || 'unknown',
                label: action?.replace(/_/g, ' ') || 'Unknown event',
                timestamp: raw.timestamp as string, raw: [raw],
            });
        }
    }

    return nodes;
}

export function buildMcpAgentTimeline(events: TraceEvent[]): TimelineNode[] {
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
    }
    return nodes;
}

export function buildCodexTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];
    const pendingItems: Record<string, TimelineNode & { type: 'tool' }> = {};

    for (const ev of events) {
        const raw = ev.raw;
        const type = raw.type as string;
        const item = raw.item as Record<string, unknown> | undefined;

        if (type === 'thread.started' || type === 'turn.started') {
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
    }
    return nodes;
}

export function buildMinimaxTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];

    for (const ev of events) {
        const raw = ev.raw;
        const type = raw.type as string;
        const part = raw.part as Record<string, unknown> | undefined;
        if (!part) continue;

        const partType = part.type as string;

        if (type === 'step_start' || type === 'step_finish') {
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

/**
 * Kilo timeline: flat events where `content` is a top-level string
 * (not nested under `part` like opencode). tool_use carries `tool` and
 * `input`; tool_result carries `output` and `status`.
 */
export function buildKiloTimeline(events: TraceEvent[]): TimelineNode[] {
    const nodes: TimelineNode[] = [];
    const pending: Record<string, TimelineNode & { type: 'tool' }> = {};

    for (const ev of events) {
        const raw = ev.raw;
        const type = raw.type as string;

        if (type === 'step_start' || type === 'step_finish') {
            continue;
        }

        if (type === 'text') {
            const role = (raw.role as string) || 'assistant';
            const text = (raw.content as string) || '';
            if (text) {
                nodes.push({ type: 'text', role, text, raw: [raw] });
            }
            continue;
        }

        if (type === 'tool_use') {
            const toolId = (raw.tool_id as string) || `kilo-${nodes.length}`;
            const node: TimelineNode & { type: 'tool' } = {
                type: 'tool',
                toolUseId: toolId,
                toolName: (raw.tool as string) || (raw.name as string) || 'unknown',
                input: (raw.input || raw.parameters || {}) as Record<string, unknown>,
                raw: [raw],
            };
            nodes.push(node);
            pending[toolId] = node;
            continue;
        }

        if (type === 'tool_result') {
            const toolId = (raw.tool_id as string) || '';
            const matched = pending[toolId];
            if (matched) {
                matched.result = raw.output;
                matched.isError = raw.status === 'error';
                matched.raw?.push(raw);
            }
            continue;
        }
    }
    return nodes;
}

/**
 * Generic fallback timeline: emits one `generic_event` node per event.
 * Guarantees at least one node per non-empty event stream. Used when the
 * detected format's builder produces zero nodes (silent-empty-panel guard).
 */
export function buildGenericTimeline(events: TraceEvent[]): TimelineNode[] {
    return events.map(ev => {
        const summary = describeGenericEvent(ev);
        return {
            type: 'generic_event',
            event: ev,
            summary,
        } as TimelineNode & { type: 'generic_event' };
    });
}

function describeGenericEvent(ev: TraceEvent): string {
    const r = ev.raw;
    const type = ev.type;

    if (type === 'text' && typeof r.text === 'string') {
        const preview = r.text.length > 80 ? r.text.slice(0, 80) + '…' : r.text;
        return `text: ${preview}`;
    }
    if (type === 'tool_use') {
        const tool = (r.tool as string) || (r.tool_name as string) || (r.name as string) || 'unknown';
        return `tool_use: ${tool}`;
    }
    if (type === 'tool_result') {
        const tool = (r.tool as string) || (r.tool_name as string) || (r.name as string) || 'unknown';
        return `tool_result: ${tool}`;
    }
    if (type === 'assistant' && r.message) {
        const msg = r.message as Record<string, unknown>;
        const content = Array.isArray(msg.content) ? msg.content : [];
        const firstText = content.find((c: Record<string, unknown>) => c.type === 'text') as Record<string, unknown> | undefined;
        const preview = firstText?.text ? String(firstText.text).slice(0, 80) : `${content.length} content blocks`;
        return `assistant: ${preview}`;
    }
    if (type === 'user') return `user message`;
    if (type === 'step_start') return `step_start`;
    if (type === 'step_finish') return `step_finish`;
    if (type === 'system') return `system`;
    if (type === 'init') return `init`;
    if (type === 'result') return `result`;
    if (type === 'message') return `message (${(r.role as string) || 'unknown'})`;
    if (type === 'item.completed' || type === 'item.started') {
        const item = r.item as Record<string, unknown> | undefined;
        return `${type}: ${item?.type || 'unknown'}`;
    }
    if ('action' in r) return `action: ${r.action}`;
    return `${type}`;
}

export interface BuildTimelineResult {
    nodes: TimelineNode[];
    format: TraceFormat;
    usedFallback: boolean;
    fallbackReason?: string;
}

export function buildTimeline(events: TraceEvent[]): TimelineNode[] {
    return buildTimelineWithMeta(events).nodes;
}

export function buildTimelineWithMeta(events: TraceEvent[]): BuildTimelineResult {
    if (events.length === 0) {
        return { nodes: [], format: 'unknown', usedFallback: false };
    }

    const format = detectTraceFormat(events);
    let nodes: TimelineNode[] = [];
    switch (format) {
        case 'odin': nodes = buildOdinTimeline(events); break;
        case 'mcp_agent': nodes = buildMcpAgentTimeline(events); break;
        case 'codex': nodes = buildCodexTimeline(events); break;
        case 'minimax': nodes = buildMinimaxTimeline(events); break;
        case 'kilo': nodes = buildKiloTimeline(events); break;
        case 'claude_code':
        case 'unknown':
        default:
            nodes = buildClaudeCodeTimeline(events);
    }

    if (nodes.length === 0) {
        return {
            nodes: buildGenericTimeline(events),
            format,
            usedFallback: true,
            fallbackReason: `Detected format "${format}" produced no nodes — rendering generic fallback`,
        };
    }
    return { nodes, format, usedFallback: false };
}