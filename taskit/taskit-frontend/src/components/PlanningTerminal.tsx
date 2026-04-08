import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { Maximize2, Minimize2, Loader2, Square } from 'lucide-react';
import '@xterm/xterm/css/xterm.css';

const WS_BASE = (() => {
    const url = import.meta.env.VITE_HARNESS_TIME_API_URL || 'http://localhost:8000';
    return url.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://');
})();

const HEADER_H = 40; // px — header bar height

interface PlanningTerminalProps {
    specId: string;
    onComplete: () => void;
}

type ConnStatus = 'connecting' | 'running' | 'done' | 'error';

export function PlanningTerminal({ specId, onComplete }: PlanningTerminalProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const termRef = useRef<HTMLDivElement>(null);
    const termInstance = useRef<Terminal | null>(null);
    const fitAddonRef = useRef<FitAddon | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const onCompleteRef = useRef(onComplete);
    onCompleteRef.current = onComplete;

    const [isFullscreen, setIsFullscreen] = useState(false);
    const [connStatus, setConnStatus] = useState<ConnStatus>('connecting');
    // Ref tracks whether planning_complete was received. Used in onclose to distinguish
    // a normal post-completion close from an unexpected disconnection — avoids the stale
    // closure problem (connStatus state is not reactive inside the WS handler).
    const planningCompleteRef = useRef(false);

    // Track fullscreen state via the browser API
    useEffect(() => {
        const handleFsChange = () => {
            setIsFullscreen(document.fullscreenElement === containerRef.current);
        };
        document.addEventListener('fullscreenchange', handleFsChange);
        return () => document.removeEventListener('fullscreenchange', handleFsChange);
    }, []);

    const toggleFullscreen = () => {
        if (!document.fullscreenElement) {
            containerRef.current?.requestFullscreen().catch(() => {});
        } else {
            document.exitFullscreen().catch(() => {});
        }
    };

    // Keyboard shortcut: Escape handled by browser natively.
    // F11 / custom: let the container's keydown bubble.

    useEffect(() => {
        if (!termRef.current) return;

        const term = new Terminal({
            cursorBlink: true,
            fontFamily: '"Cascadia Code", "Fira Mono", "Menlo", monospace',
            fontSize: 13,
            lineHeight: 1.25,
            scrollback: 10000,
            allowTransparency: false,
            theme: {
                background: '#0d0d0d',
                foreground: '#e8e8e8',
                cursor: '#e8e8e8',
                selectionBackground: '#ffffff33',
                black: '#1a1a1a',
                brightBlack: '#555555',
                red: '#ff5f57',
                brightRed: '#ff6e67',
                green: '#27c93f',
                brightGreen: '#5af78e',
                yellow: '#ffbd2e',
                brightYellow: '#ffea7f',
                blue: '#4a90d9',
                brightBlue: '#5fc3ff',
                magenta: '#bd93f9',
                brightMagenta: '#ff79c6',
                cyan: '#8be9fd',
                brightCyan: '#9aedfe',
                white: '#e8e8e8',
                brightWhite: '#ffffff',
            },
        });
        const fitAddon = new FitAddon();
        term.loadAddon(fitAddon);
        term.open(termRef.current);
        fitAddon.fit();
        termInstance.current = term;
        fitAddonRef.current = fitAddon;

        const ws = new WebSocket(`${WS_BASE}/ws/planning/${specId}/`);
        wsRef.current = ws;

        ws.onopen = () => {
            setConnStatus('running');
            ws.send(JSON.stringify({
                type: 'start',
                rows: term.rows,
                cols: term.cols,
            }));
        };

        ws.onmessage = (e) => {
            if (typeof e.data === 'string') {
                try {
                    const msg = JSON.parse(e.data) as { type: string; exit_code?: number };
                    if (msg.type === 'planning_complete') {
                        planningCompleteRef.current = true;
                        if (msg.exit_code && msg.exit_code !== 0) {
                            setConnStatus('error');
                            term.write(`\r\n\x1b[31m[Planning failed — exit code ${msg.exit_code}]\x1b[0m\r\n`);
                        } else {
                            setConnStatus('done');
                            term.write('\r\n\x1b[32m[Planning complete]\x1b[0m\r\n');
                        }
                        onCompleteRef.current();
                    }
                } catch {
                    term.write(e.data);
                }
            } else {
                (e.data as Blob).arrayBuffer().then((buf) => {
                    term.write(new Uint8Array(buf));
                });
            }
        };

        ws.onclose = (e) => {
            // Use the ref (not the state) to avoid stale closure — connStatus inside this
            // handler is always the value from the initial render ('connecting').
            if (!planningCompleteRef.current && e.code !== 1000) {
                setConnStatus('error');
                term.write(`\r\n\x1b[31m[Connection closed: ${e.code} ${e.reason || ''}]\x1b[0m\r\n`);
            }
        };

        ws.onerror = () => {
            setConnStatus('error');
            term.write('\r\n\x1b[31m[WebSocket error — is daphne running?]\x1b[0m\r\n');
        };

        term.onData((data) => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'input', data }));
            }
        });

        const observer = new ResizeObserver(() => {
            fitAddon.fit();
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
            }
        });
        observer.observe(termRef.current);

        return () => {
            ws.close();
            term.dispose();
            observer.disconnect();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [specId]);

    const statusDot: Record<ConnStatus, { color: string; label: string }> = {
        connecting: { color: '#ffbd2e', label: 'Connecting…' },
        running:    { color: '#27c93f', label: 'Running' },
        done:       { color: '#8be9fd', label: 'Complete' },
        error:      { color: '#ff5f57', label: 'Error' },
    };
    const dot = statusDot[connStatus];

    return (
        <div
            ref={containerRef}
            className="rounded-lg overflow-hidden border border-white/10 flex flex-col"
            style={{ background: '#0d0d0d' }}
        >
            {/* ── Header bar ── */}
            <div
                className="flex items-center justify-between px-3 border-b border-white/10 shrink-0"
                style={{ height: HEADER_H, background: '#161616' }}
            >
                {/* Left: traffic-light-style dots + label */}
                <div className="flex items-center gap-2.5">
                    <div className="flex gap-1.5">
                        <span className="size-3 rounded-full bg-[#ff5f57]" />
                        <span className="size-3 rounded-full bg-[#ffbd2e]" />
                        <span className="size-3 rounded-full bg-[#27c93f]" />
                    </div>
                    <span className="text-[11px] text-white/40 font-mono select-none">
                        odin plan
                    </span>
                </div>

                {/* Right: status + stop + fullscreen toggle */}
                <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5">
                        {connStatus === 'running' ? (
                            <Loader2 className="size-3 text-white/40 animate-spin" />
                        ) : (
                            <span
                                className="size-2 rounded-full"
                                style={{ background: dot.color }}
                            />
                        )}
                        <span className="text-[11px] font-mono" style={{ color: dot.color }}>
                            {dot.label}
                        </span>
                    </div>

                    {connStatus === 'running' && (
                        <button
                            onClick={() => {
                                const ws = wsRef.current;
                                if (ws && ws.readyState === WebSocket.OPEN) {
                                    ws.send(JSON.stringify({ type: 'stop' }));
                                }
                            }}
                            className="flex items-center gap-1 text-[11px] font-mono text-red-400 hover:text-red-300 transition-colors px-1.5 py-0.5 rounded border border-red-500/30 hover:border-red-500/50 hover:bg-red-500/10"
                            title="Stop planning"
                        >
                            <Square className="size-2.5 fill-current" />
                            Stop
                        </button>
                    )}

                    <button
                        onClick={toggleFullscreen}
                        className="text-white/40 hover:text-white/80 transition-colors p-1 rounded"
                        title={isFullscreen ? 'Exit fullscreen (Esc)' : 'Fullscreen'}
                    >
                        {isFullscreen
                            ? <Minimize2 className="size-3.5" />
                            : <Maximize2 className="size-3.5" />
                        }
                    </button>
                </div>
            </div>

            {/* ── Terminal ── */}
            <div
                ref={termRef}
                className="w-full"
                style={{
                    height: isFullscreen ? `calc(100vh - ${HEADER_H}px)` : 460,
                    minHeight: 0,
                }}
            />
        </div>
    );
}
