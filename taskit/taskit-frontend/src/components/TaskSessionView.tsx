import { useEffect, useRef, useState } from 'react';
import { Activity, Loader2, Radio, Circle } from 'lucide-react';
import { TraceViewer } from './TraceViewer';

const WS_BASE = (() => {
    const url = import.meta.env.VITE_HARNESS_TIME_API_URL || 'http://localhost:8000';
    return url.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://');
})();

export type SessionType = 'task_execution' | 'reflection' | null;

export interface SessionMeta {
    available: boolean;
    sessionType: SessionType;
    live: boolean;
    exists: boolean;
    taskId: number;
    reportId: number | null;
}

interface Props {
    taskId: number;
    /** Notifies the parent whenever server-side meta changes (for button label). */
    onMetaChange?: (meta: SessionMeta) => void;
}

type ConnStatus = 'connecting' | 'streaming' | 'idle' | 'ended' | 'error';

/**
 * Streams the active (or most-recent) session JSONL for a task.
 *
 * One-way read-only stream over `ws/tasks/<taskId>/session/`. Handles
 * rerun transitions via a `reset` frame from the server: when the file
 * inode changes the buffer is cleared and streaming restarts.
 */
export function TaskSessionView({ taskId, onMetaChange }: Props) {
    const [traceText, setTraceText] = useState('');
    const [meta, setMeta] = useState<SessionMeta | null>(null);
    const [status, setStatus] = useState<ConnStatus>('connecting');
    const [error, setError] = useState<string | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const bufferRef = useRef('');
    const metaCbRef = useRef(onMetaChange);
    metaCbRef.current = onMetaChange;

    useEffect(() => {
        bufferRef.current = '';
        setTraceText('');
        setStatus('connecting');
        setError(null);

        const ws = new WebSocket(`${WS_BASE}/ws/tasks/${taskId}/session/`);
        wsRef.current = ws;

        ws.onopen = () => {
            setStatus('streaming');
        };

        ws.onmessage = (evt) => {
            let msg: any;
            try {
                msg = JSON.parse(evt.data);
            } catch {
                return;
            }
            switch (msg.type) {
                case 'meta': {
                    const next: SessionMeta = {
                        available: !!msg.available,
                        sessionType: msg.session_type ?? null,
                        live: !!msg.live,
                        exists: !!msg.exists,
                        taskId: Number(msg.task_id),
                        reportId: msg.report_id ?? null,
                    };
                    setMeta(next);
                    metaCbRef.current?.(next);
                    if (!next.available) {
                        setStatus('idle');
                    } else if (next.live) {
                        setStatus('streaming');
                    } else {
                        setStatus('idle');
                    }
                    break;
                }
                case 'chunk': {
                    if (typeof msg.data === 'string' && msg.data.length > 0) {
                        bufferRef.current += msg.data;
                        setTraceText(bufferRef.current);
                    }
                    break;
                }
                case 'reset': {
                    bufferRef.current = '';
                    setTraceText('');
                    const next: SessionMeta = {
                        available: true,
                        sessionType: msg.session_type ?? meta?.sessionType ?? null,
                        live: !!msg.live,
                        exists: true,
                        taskId,
                        reportId: msg.report_id ?? null,
                    };
                    setMeta(next);
                    metaCbRef.current?.(next);
                    setStatus('streaming');
                    break;
                }
                case 'eof': {
                    setStatus('ended');
                    break;
                }
                case 'error': {
                    setError(msg.message || 'Session stream error');
                    setStatus('error');
                    break;
                }
            }
        };

        ws.onerror = () => {
            setStatus('error');
            setError((prev) => prev || 'WebSocket connection error');
        };

        ws.onclose = () => {
            setStatus((prev) => (prev === 'streaming' ? 'ended' : prev));
        };

        return () => {
            wsRef.current = null;
            try {
                ws.close();
            } catch {
                // ignore
            }
        };
    }, [taskId]);

    const sessionLabel =
        meta?.sessionType === 'reflection'
            ? 'Reflection'
            : meta?.sessionType === 'task_execution'
                ? 'Task Execution'
                : 'Session';

    return (
        <div className="flex flex-col h-full min-h-0 w-full">
            <div className="flex items-center justify-between px-4 py-2 border-b border-border/40 shrink-0">
                <div className="flex items-center gap-2">
                    <Activity className="size-4 text-violet-400" />
                    <span className="text-sm font-semibold">{sessionLabel}</span>
                    <StatusBadge status={status} live={!!meta?.live} />
                </div>
                <div className="text-[11px] text-muted-foreground/80 font-mono">
                    {meta?.available === false && 'no session yet'}
                    {meta?.available && !meta.live && 'last run'}
                    {meta?.available && meta.live && 'live'}
                </div>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
                {error && (
                    <div className="mb-3 text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded px-3 py-2 font-mono">
                        {error}
                    </div>
                )}
                {traceText.length === 0 ? (
                    <div className="text-xs text-muted-foreground/70 font-mono p-6 text-center border border-dashed border-border/40 rounded-lg">
                        {status === 'connecting' && 'Connecting…'}
                        {status === 'streaming' && 'Waiting for first trace event…'}
                        {status === 'idle' && meta?.available === false && 'No session has run yet for this task.'}
                        {status === 'idle' && meta?.available && 'Session completed with no trace output.'}
                        {status === 'ended' && 'Session ended.'}
                        {status === 'error' && 'Stream ended with an error.'}
                    </div>
                ) : (
                    <TraceViewer traceText={traceText} className="max-h-none mt-0" />
                )}
            </div>
        </div>
    );
}

function StatusBadge({ status, live }: { status: ConnStatus; live: boolean }) {
    if (status === 'connecting') {
        return (
            <span className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-700/50 text-zinc-300">
                <Loader2 className="size-2.5 animate-spin" /> connecting
            </span>
        );
    }
    if (status === 'streaming' && live) {
        return (
            <span className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                <Radio className="size-2.5 animate-pulse" /> live
            </span>
        );
    }
    if (status === 'ended') {
        return (
            <span className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-700/50 text-zinc-300">
                <Circle className="size-2.5" /> ended
            </span>
        );
    }
    if (status === 'error') {
        return (
            <span className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/30">
                error
            </span>
        );
    }
    return (
        <span className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-700/50 text-zinc-300">
            <Circle className="size-2.5" /> idle
        </span>
    );
}
