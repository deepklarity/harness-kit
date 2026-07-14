import { describe, it, expect } from 'vitest';
import {
    parseTrace,
    detectTraceFormat,
    detectTraceFormatWithMeta,
    buildTimeline,
    buildTimelineWithMeta,
    buildClaudeCodeTimeline,
    buildMinimaxTimeline,
    buildKiloTimeline,
    buildGenericTimeline,
    extractTokenSummary,
    type TraceEvent,
} from './traceFormat';
import opencodeFixture from './__fixtures__/trace_task_116_opencode.jsonl?raw';
import kiloFixture from './__fixtures__/trace_kilo_synthetic.jsonl?raw';
import claudeFixture from './__fixtures__/trace_claude_synthetic.jsonl?raw';

const FIXTURES: Record<string, string> = {
    'trace_task_116_opencode.jsonl': opencodeFixture,
    'trace_kilo_synthetic.jsonl': kiloFixture,
    'trace_claude_synthetic.jsonl': claudeFixture,
};

function loadFixture(name: string): string {
    const fixture = FIXTURES[name];
    if (fixture === undefined) throw new Error(`Unknown fixture: ${name}`);
    return fixture;
}

// ─────────────────────────────────────────────────────────────────────────
// REGRESSION GUARDS — task #111/#116 empty-Timeline bug
// ─────────────────────────────────────────────────────────────────────────

describe('TraceViewer timeline — regression: opencode/glm empty panel bug', () => {
    // The task #116 trace is pinned as a fixture so this exact scenario
    // remains covered. It has 27 logfmt (non-JSON) lines followed by minimax
    // JSON events. Before the fix, detectTraceFormat only sampled the first
    // 10 events (all logfmt) → fell through to "unknown" → 0 timeline nodes.
    it('task #116 fixture: parseTrace produces >0 events with both logfmt and JSON shapes', () => {
        const raw = loadFixture('trace_task_116_opencode.jsonl');
        const events = parseTrace(raw);
        expect(events.length).toBeGreaterThan(0);

        // First batch should be logfmt-wrapped "text" events (no sessionID)
        const firstBatch = events.slice(0, 27);
        expect(firstBatch.every(e => e.type === 'text')).toBe(true);
        expect(firstBatch.every(e => !('sessionID' in e.raw))).toBe(true);

        // JSON events with sessionID+part start later
        const laterEvents = events.slice(27);
        expect(laterEvents.length).toBeGreaterThan(0);
        expect(laterEvents.some(e => 'sessionID' in e.raw && 'part' in e.raw)).toBe(true);
    });

    it('task #116 fixture: detectTraceFormat now correctly identifies minimax even when JSON events are beyond the first 10', () => {
        const raw = loadFixture('trace_task_116_opencode.jsonl');
        const events = parseTrace(raw);
        const format = detectTraceFormat(events);
        expect(format).toBe('minimax');
    });

    it('task #116 fixture: detectTraceFormatWithMeta reports inspected position past the sample window', () => {
        const raw = loadFixture('trace_task_116_opencode.jsonl');
        const events = parseTrace(raw);
        const meta = detectTraceFormatWithMeta(events);
        expect(meta.format).toBe('minimax');
        // The minimax signature must be found past index 10 — that's the
        // whole point of widening the detection window.
        expect(meta.inspected).toBeGreaterThan(10);
        expect(meta.inspected).toBeLessThanOrEqual(meta.sampleWindow);
    });

    it('task #116 fixture: buildTimeline yields >0 nodes (no empty panel)', () => {
        const raw = loadFixture('trace_task_116_opencode.jsonl');
        const events = parseTrace(raw);
        const nodes = buildTimeline(events);
        // The minimax builder must produce at least one node per tool_use and
        // text event. Our fixture has 3 tool_use, 2 text, plus 3 step_*.
        expect(nodes.length).toBeGreaterThan(0);

        // Specifically, tool_use events should yield tool nodes with the
        // correct tool name carried over from the opencode part.tool field.
        const toolNodes = nodes.filter(n => n.type === 'tool') as Extract<typeof nodes[number], { type: 'tool' }>[];
        expect(toolNodes.length).toBeGreaterThan(0);
        expect(toolNodes.map(n => n.toolName)).toEqual(
            expect.arrayContaining(['taskit_taskit_add_comment', 'todowrite'])
        );
    });

    it('task #116 fixture: buildTimelineWithMeta reports no fallback for minimax', () => {
        const raw = loadFixture('trace_task_116_opencode.jsonl');
        const events = parseTrace(raw);
        const result = buildTimelineWithMeta(events);
        expect(result.usedFallback).toBe(false);
        expect(result.format).toBe('minimax');
        expect(result.nodes.length).toBeGreaterThan(0);
    });

    it('task #116 fixture: extractTokenSummary reads minimax step_finish tokens', () => {
        const raw = loadFixture('trace_task_116_opencode.jsonl');
        const events = parseTrace(raw);
        const summary = extractTokenSummary(events);
        expect(summary).not.toBeNull();
        expect(summary!.totalInput).toBeGreaterThan(0);
        expect(summary!.totalOutput).toBeGreaterThan(0);
    });
});

// ─────────────────────────────────────────────────────────────────────────
// STRUCTURAL GUARANTEES — Timeline never renders empty when events exist
// ─────────────────────────────────────────────────────────────────────────

describe('TraceViewer timeline — structural empty-panel guarantee', () => {
    it('empty events: buildTimelineWithMeta returns no nodes, no fallback', () => {
        const result = buildTimelineWithMeta([]);
        expect(result.nodes).toEqual([]);
        expect(result.usedFallback).toBe(false);
    });

    it('all-logfmt events: detection falls through but generic fallback fires', () => {
        // 100 lines of pure logfmt → no JSON events → detection = "unknown"
        // → buildClaudeCodeTimeline produces 0 nodes → generic fallback.
        const raw = Array.from({ length: 100 }, (_, i) =>
            `timestamp=2026-07-05T18:00:${String(i % 60).padStart(2, '0')}.000Z level=INFO message="bootstrapping" run=abc step=${i}`
        ).join('\n');
        const events = parseTrace(raw);
        expect(events.length).toBeGreaterThan(0);
        const result = buildTimelineWithMeta(events);
        // Structural guarantee: never 0 nodes when events > 0
        expect(result.nodes.length).toBeGreaterThan(0);
        expect(result.usedFallback).toBe(true);
        expect(result.fallbackReason).toBeDefined();
    });

    it('unrecognized JSON events: generic fallback produces one node per event', () => {
        // Made-up JSON shape that no builder recognizes — must still yield
        // at least one node per event via the generic fallback.
        const raw = [
            '{"kind":"synthetic","step":1}',
            '{"kind":"synthetic","step":2}',
            '{"kind":"synthetic","step":3}',
        ].join('\n');
        const events = parseTrace(raw);
        const result = buildTimelineWithMeta(events);
        expect(result.nodes.length).toBe(3);
        expect(result.usedFallback).toBe(true);
        for (const n of result.nodes) {
            expect(n.type).toBe('generic_event');
        }
    });

    it('buildGenericTimeline is a pure 1:1 mapping', () => {
        const events: TraceEvent[] = [
            { index: 1, type: 'text', raw: { text: 'hello' } },
            { index: 2, type: 'tool_use', raw: { tool: 'Bash' } },
            { index: 3, type: 'step_finish', raw: {} },
        ];
        const nodes = buildGenericTimeline(events);
        expect(nodes).toHaveLength(3);
        expect(nodes.map(n => n.type)).toEqual(['generic_event', 'generic_event', 'generic_event']);
    });
});

// ─────────────────────────────────────────────────────────────────────────
// FORMAT DETECTION — pin behavior for each harness family
// ─────────────────────────────────────────────────────────────────────────

describe('detectTraceFormat — per-harness routing', () => {
    it('claude_code: detects from system init + message.content[]', () => {
        const events = parseTrace(JSON.stringify({
            type: 'system', subtype: 'init', model: 'claude-sonnet-4-5', tools: []
        }));
        expect(detectTraceFormat(events)).toBe('claude_code');
    });

    it('odin: detects from action + run_id fields', () => {
        const events = parseTrace(JSON.stringify({
            action: 'task_started', run_id: 'abc', task_id: '1'
        }));
        expect(detectTraceFormat(events)).toBe('odin');
    });

    it('minimax: detects from sessionID + part even when buried past first 10', () => {
        // First 12 events are logfmt; minimax signature is at index 13
        const lines: string[] = [];
        for (let i = 0; i < 12; i++) {
            lines.push(`timestamp=2026-07-05 level=INFO message="log ${i}"`);
        }
        lines.push(JSON.stringify({
            type: 'tool_use', sessionID: 'ses_x', part: { type: 'tool', tool: 'Bash' }
        }));
        const events = parseTrace(lines.join('\n'));
        expect(detectTraceFormat(events)).toBe('minimax');
    });

    it('mcp_agent: detects from tool_name + init session_id', () => {
        const events = parseTrace(JSON.stringify({
            type: 'init', session_id: 's_1', model: 'gemini-2.5'
        }));
        expect(detectTraceFormat(events)).toBe('mcp_agent');
    });

    it('codex: detects from item.completed wrapper', () => {
        const events = parseTrace(JSON.stringify({
            type: 'item.completed', item: { type: 'agent_message', text: 'hi' }
        }));
        expect(detectTraceFormat(events)).toBe('codex');
    });

    it('unknown: returns unknown for unrecognized shape', () => {
        const events = parseTrace(JSON.stringify({ kind: 'mystery' }));
        expect(detectTraceFormat(events)).toBe('unknown');
    });
});

// ─────────────────────────────────────────────────────────────────────────
// FORMAT-SPECIFIC BUILDERS — verify each path produces >0 nodes for a
// valid input, never silently empty
// ─────────────────────────────────────────────────────────────────────────

describe('Format-specific timeline builders', () => {
    it('kilo: yields text + tool nodes for kilo flat-event protocol', () => {
        const raw = loadFixture('trace_kilo_synthetic.jsonl');
        const events = parseTrace(raw);
        const nodes = buildKiloTimeline(events);
        expect(nodes.length).toBeGreaterThan(0);
        // tool_use should yield a tool node
        expect(nodes.some(n => n.type === 'tool')).toBe(true);
        // text events should yield text nodes
        expect(nodes.some(n => n.type === 'text')).toBe(true);
    });

    it('minimax: yields tool + text nodes for opencode camelCase protocol', () => {
        const raw = loadFixture('trace_task_116_opencode.jsonl');
        const events = parseTrace(raw);
        const nodes = buildMinimaxTimeline(events);
        expect(nodes.length).toBeGreaterThan(0);
        expect(nodes.filter(n => n.type === 'tool').length).toBeGreaterThan(0);
        expect(nodes.filter(n => n.type === 'text').length).toBeGreaterThan(0);
        // step_start/step_finish are bookkeeping — should be skipped
        expect(nodes.some(n => n.type === 'tool' && (n as { toolName: string }).toolName === 'step_start')).toBe(false);
    });

    it('claude_code: yields text + tool nodes for claude content[] protocol', () => {
        const raw = loadFixture('trace_claude_synthetic.jsonl');
        const events = parseTrace(raw);
        const nodes = buildClaudeCodeTimeline(events);
        expect(nodes.length).toBeGreaterThan(0);
        expect(nodes.some(n => n.type === 'text')).toBe(true);
        expect(nodes.some(n => n.type === 'tool')).toBe(true);
        expect(nodes.some(n => n.type === 'system_init')).toBe(true);
    });

    it('claude_code: extractTokenSummary reads modelUsage aggregate', () => {
        const raw = loadFixture('trace_claude_synthetic.jsonl');
        const events = parseTrace(raw);
        const summary = extractTokenSummary(events);
        expect(summary).not.toBeNull();
        expect(summary!.totalInput).toBe(1500);
        expect(summary!.totalOutput).toBe(420);
        expect(summary!.models).toContain('claude-sonnet-4-5');
    });
});

// ─────────────────────────────────────────────────────────────────────────
// PARSE TRACE — wrapping logfmt lines as text events
// ─────────────────────────────────────────────────────────────────────────

describe('parseTrace', () => {
    it('parses each JSON line as an event', () => {
        const raw = '{"type":"tool_use","tool":"Bash"}\n{"type":"text","text":"hi"}';
        const events = parseTrace(raw);
        expect(events).toHaveLength(2);
        expect(events[0].type).toBe('tool_use');
        expect(events[1].type).toBe('text');
    });

    it('wraps non-JSON lines as type:text events', () => {
        const raw = 'timestamp=2026-07-05 level=INFO message=init\n{"type":"tool_use"}';
        const events = parseTrace(raw);
        expect(events).toHaveLength(2);
        expect(events[0].type).toBe('text');
        expect(events[0].raw).toEqual({ text: 'timestamp=2026-07-05 level=INFO message=init' });
        expect(events[1].type).toBe('tool_use');
    });

    it('skips empty lines', () => {
        const raw = '\n{"type":"text","text":"a"}\n\n\n{"type":"text","text":"b"}\n';
        const events = parseTrace(raw);
        expect(events).toHaveLength(2);
        expect(events.map(e => e.index)).toEqual([1, 2]);
    });

    it('preserves the original raw payload on each event', () => {
        const raw = '{"type":"tool_use","tool":"Bash","input":{"command":"ls"}}';
        const events = parseTrace(raw);
        expect(events[0].raw).toMatchObject({ type: 'tool_use', tool: 'Bash' });
    });
});