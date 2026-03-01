import { useRef, useState, useCallback, useEffect } from 'react';
import {
    Bold,
    Italic,
    Code2,
    Heading1,
    Heading2,
    List,
    ListOrdered,
    Quote,
    Link,
    Minus,
    CheckSquare,
    Eye,
    EyeOff,
    Undo2,
    Redo2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { MarkdownRenderer } from './MarkdownRenderer';

interface MarkdownEditorProps {
    value: string;
    onChange: (value: string) => void;
    rows?: number;
    className?: string;
    placeholder?: string;
}

interface ToolbarAction {
    label: string;
    icon: typeof Bold;
    shortcut: string;
    action: 'bold' | 'italic' | 'heading1' | 'heading2' | 'code' | 'bullet' | 'number' | 'quote' | 'link' | 'hr' | 'task' | 'codeBlock';
}

const TOOLBAR_ACTIONS: ToolbarAction[] = [
    { label: 'Bold', icon: Bold, shortcut: 'Ctrl+B', action: 'bold' },
    { label: 'Italic', icon: Italic, shortcut: 'Ctrl+I', action: 'italic' },
    { label: 'Heading 1', icon: Heading1, shortcut: 'Ctrl+1', action: 'heading1' },
    { label: 'Heading 2', icon: Heading2, shortcut: 'Ctrl+2', action: 'heading2' },
    { label: 'Code Block', icon: Code2, shortcut: 'Ctrl+Alt+C', action: 'codeBlock' },
    { label: 'Inline Code', icon: Code2, shortcut: 'Ctrl+E', action: 'code' },
    { label: 'Bullet List', icon: List, shortcut: 'Ctrl+Shift+8', action: 'bullet' },
    { label: 'Numbered List', icon: ListOrdered, shortcut: 'Ctrl+Shift+7', action: 'number' },
    { label: 'Task List', icon: CheckSquare, shortcut: 'Ctrl+Shift+9', action: 'task' },
    { label: 'Quote', icon: Quote, shortcut: 'Ctrl+Shift+>', action: 'quote' },
    { label: 'Link', icon: Link, shortcut: 'Ctrl+K', action: 'link' },
    { label: 'Divider', icon: Minus, shortcut: 'Ctrl+Shift+-', action: 'hr' },
];

export function MarkdownEditor({ value, onChange, rows = 12, className, placeholder }: MarkdownEditorProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [showPreview, setShowPreview] = useState(false);
    const [history, setHistory] = useState<string[]>([value]);
    const [historyIndex, setHistoryIndex] = useState(0);

    // Update history when value changes externally
    useEffect(() => {
        if (history[historyIndex] !== value) {
            const newHistory = history.slice(0, historyIndex + 1);
            newHistory.push(value);
            setHistory(newHistory.slice(-50)); // Keep last 50 states
            setHistoryIndex(Math.min(historyIndex, newHistory.length - 1));
        }
    }, [value]);

    const addToHistory = useCallback((newValue: string) => {
        onChange(newValue);
        const newHistory = history.slice(0, historyIndex + 1);
        newHistory.push(newValue);
        setHistory(newHistory.slice(-50));
        setHistoryIndex(newHistory.length - 1);
    }, [history, historyIndex, onChange]);

    const handleUndo = useCallback(() => {
        if (historyIndex > 0) {
            setHistoryIndex(historyIndex - 1);
            onChange(history[historyIndex - 1]);
        }
    }, [history, historyIndex, onChange]);

    const handleRedo = useCallback(() => {
        if (historyIndex < history.length - 1) {
            setHistoryIndex(historyIndex + 1);
            onChange(history[historyIndex + 1]);
        }
    }, [history, historyIndex, onChange]);

    const insertText = useCallback((before: string, after = '', placeholderText = '') => {
        const textarea = textareaRef.current;
        if (!textarea) return;

        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const selectedText = value.slice(start, end);
        const textToInsert = selectedText || placeholderText;
        const insertion = `${before}${textToInsert}${after}`;
        const newValue = `${value.slice(0, start)}${insertion}${value.slice(end)}`;

        addToHistory(newValue);

        requestAnimationFrame(() => {
            textarea.focus();
            const newStart = start + before.length;
            const newEnd = newStart + textToInsert.length;
            textarea.setSelectionRange(newStart, newEnd);
        });
    }, [value, addToHistory]);

    const applyFormatting = useCallback((action: ToolbarAction['action']) => {
        switch (action) {
            case 'bold':
                insertText('**', '**', 'bold text');
                break;
            case 'italic':
                insertText('*', '*', 'italic text');
                break;
            case 'heading1':
                insertText('# ', '', 'Heading 1');
                break;
            case 'heading2':
                insertText('## ', '', 'Heading 2');
                break;
            case 'code':
                insertText('`', '`', 'code');
                break;
            case 'codeBlock':
                insertText('```\n', '\n```', 'const code = "here";');
                break;
            case 'bullet':
                insertText('- ', '', 'List item');
                break;
            case 'number':
                insertText('1. ', '', 'List item');
                break;
            case 'task':
                insertText('- [ ] ', '', 'Task item');
                break;
            case 'quote':
                insertText('> ', '', 'Quote text');
                break;
            case 'link':
                insertText('[', '](https://example.com)', 'link text');
                break;
            case 'hr':
                insertText('\n---\n', '');
                break;
        }
    }, [insertText]);

    // Handle keyboard shortcuts
    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        const isMod = e.ctrlKey || e.metaKey;
        const isShift = e.shiftKey;

        if (isMod && e.key === 'z') {
            e.preventDefault();
            if (e.shiftKey) {
                handleRedo();
            } else {
                handleUndo();
            }
            return;
        }

        if (isMod && e.key === 'y') {
            e.preventDefault();
            handleRedo();
            return;
        }

        // Auto-complete markdown
        if (e.key === ' ' || e.key === 'Enter') {
            const textarea = textareaRef.current;
            if (!textarea) return;

            const cursor = textarea.selectionStart;
            const lineStart = value.lastIndexOf('\n', cursor - 1) + 1;
            const line = value.slice(lineStart, cursor);

            // Auto-complete list items
            if (e.key === 'Enter') {
                if (/^- \[([x ]) \]$/.test(line)) {
                    e.preventDefault();
                    const isChecked = line.includes('[x]') ? '[x]' : '[ ]';
                    insertText(`- ${isChecked} `, '');
                    return;
                }
                if (/^[-*]\s$/.test(line)) {
                    e.preventDefault();
                    insertText('- ', '');
                    return;
                }
                if (/^\d+\.\s$/.test(line)) {
                    e.preventDefault();
                    const num = parseInt(line.match(/^\d+/)?.[0] || '1') + 1;
                    insertText(`${num}. `, '');
                    return;
                }
                if (/^>\s$/.test(line)) {
                    e.preventDefault();
                    insertText('> ', '');
                    return;
                }
            }
        }

        // Markdown shortcuts
        if (e.key === 'b' && isMod) {
            e.preventDefault();
            applyFormatting('bold');
        } else if (e.key === 'i' && isMod) {
            e.preventDefault();
            applyFormatting('italic');
        } else if (e.key === '1' && isMod && !isShift) {
            e.preventDefault();
            applyFormatting('heading1');
        } else if (e.key === '2' && isMod && !isShift) {
            e.preventDefault();
            applyFormatting('heading2');
        } else if (e.key === 'k' && isMod) {
            e.preventDefault();
            applyFormatting('link');
        } else if (e.key === 'e' && isMod) {
            e.preventDefault();
            applyFormatting('code');
        }
    }, [applyFormatting, handleUndo, handleRedo, value, insertText]);

    // Auto-close code blocks and other markdown pairs
    const handleInput = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const textarea = textareaRef.current;
        if (!textarea) return;

        const cursor = textarea.selectionStart;
        const newValue = e.target.value;
        const char = newValue[cursor - 1];
        const prevChar = newValue[cursor - 2];

        // Auto-close backticks
        if (char === '`' && prevChar !== '`') {
            const before = newValue.slice(0, cursor);
            const after = newValue.slice(cursor);
            const updated = `${before}\`${after}`;
            addToHistory(updated);
            requestAnimationFrame(() => {
                textarea.setSelectionRange(cursor, cursor);
            });
        }

        onChange(newValue);
    }, [onChange, addToHistory]);

    return (
        <div className={`relative border border-border rounded-lg overflow-hidden bg-card ${className}`}>
            {/* Toolbar */}
            <div className="flex flex-wrap items-center gap-0.5 p-1.5 border-b border-border bg-muted/30">
                <div className="flex items-center gap-0.5 pr-2 border-r border-border">
                    <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        onClick={handleUndo}
                        disabled={historyIndex <= 0}
                        title="Undo (Ctrl+Z)"
                    >
                        <Undo2 className="size-3.5" />
                    </Button>
                    <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        onClick={handleRedo}
                        disabled={historyIndex >= history.length - 1}
                        title="Redo (Ctrl+Y)"
                    >
                        <Redo2 className="size-3.5" />
                    </Button>
                </div>

                <div className="flex items-center gap-0.5 px-2">
                    {TOOLBAR_ACTIONS.slice(0, 4).map(action => (
                        <Button
                            key={action.label}
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0"
                            onClick={() => applyFormatting(action.action)}
                            title={`${action.label} (${action.shortcut})`}
                        >
                            <action.icon className="size-3.5" />
                        </Button>
                    ))}
                </div>

                <div className="flex items-center gap-0.5 px-2 border-l border-border pl-2">
                    {TOOLBAR_ACTIONS.slice(4, 9).map(action => (
                        <Button
                            key={action.label}
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0"
                            onClick={() => applyFormatting(action.action)}
                            title={`${action.label} (${action.shortcut})`}
                        >
                            <action.icon className="size-3.5" />
                        </Button>
                    ))}
                </div>

                <div className="flex items-center gap-0.5 px-2 border-l border-border pl-2">
                    {TOOLBAR_ACTIONS.slice(9).map(action => (
                        <Button
                            key={action.label}
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0"
                            onClick={() => applyFormatting(action.action)}
                            title={`${action.label} (${action.shortcut})`}
                        >
                            <action.icon className="size-3.5" />
                        </Button>
                    ))}
                </div>

                <div className="flex-1" />

                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 gap-1.5"
                    onClick={() => setShowPreview(!showPreview)}
                    title={showPreview ? 'Hide preview' : 'Show preview'}
                >
                    {showPreview ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                    <span className="text-xs">{showPreview ? 'Hide' : 'Preview'}</span>
                </Button>
            </div>

            {/* Editor + Preview */}
            <div className={`flex ${showPreview ? 'flex-col md:flex-row' : 'flex-col'}`}>
                <textarea
                    ref={textareaRef}
                    value={value}
                    onChange={handleInput}
                    onKeyDown={handleKeyDown}
                    rows={showPreview ? Math.max(6, rows) : rows}
                    placeholder={placeholder}
                    className={`flex-1 w-full p-3 bg-transparent resize-none focus:outline-none font-mono text-sm leading-relaxed ${
                        showPreview ? 'md:w-1/2 border-r border-border' : ''
                    }`}
                />

                {showPreview && (
                    <div className="flex-1 w-full p-4 bg-muted/20 overflow-auto md:w-1/2 min-h-[200px]">
                        <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 sticky top-0 bg-muted/20">
                            Preview
                        </div>
                        <MarkdownRenderer text={value || '*Nothing to preview*'} />
                    </div>
                )}
            </div>

            {/* Status bar */}
            <div className="flex items-center justify-between px-3 py-1.5 border-t border-border bg-muted/30 text-xs text-muted-foreground">
                <div className="flex items-center gap-3">
                    <span>Markdown</span>
                    <span>•</span>
                    <span>{value.length} chars</span>
                    <span>•</span>
                    <span>{value.split(/\s+/).filter(Boolean).length} words</span>
                </div>
                <div className="hidden sm:flex items-center gap-2">
                    <span className="text-muted-foreground/60">Shortcuts:</span>
                    <kbd className="px-1.5 py-0.5 bg-background rounded border text-[10px]">Ctrl+B</kbd>
                    <span className="text-muted-foreground/60">bold</span>
                    <kbd className="px-1.5 py-0.5 bg-background rounded border text-[10px]">Ctrl+I</kbd>
                    <span className="text-muted-foreground/60">italic</span>
                </div>
            </div>
        </div>
    );
}
