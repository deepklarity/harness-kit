import { useState, useCallback, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface MarkdownRendererProps {
    text?: string;
    className?: string;
    emptyFallback?: ReactNode;
}

// ============================================================================
// Token Types
// ============================================================================

type Token =
    | { type: 'code'; content: string; language?: string }
    | { type: 'markdown'; content: string };

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * Tokenize markdown by separating code blocks from regular markdown
 */
function tokenizeByCodeBlocks(input: string): Token[] {
    const tokens: Token[] = [];
    // Match ```language\ncode``` or just ```\ncode```
    const blockRegex = /```(\w*)?\n?([\s\S]*?)```/g;
    let cursor = 0;
    let match: RegExpExecArray | null;

    while ((match = blockRegex.exec(input)) !== null) {
        const [raw, language, code] = match;
        const start = match.index;

        // Add markdown content before the code block
        if (start > cursor) {
            tokens.push({ type: 'markdown', content: input.slice(cursor, start) });
        }

        // Add code block
        tokens.push({
            type: 'code',
            content: code.trim(),
            language: language?.trim() || undefined,
        });

        cursor = start + raw.length;
    }

    // Add remaining content after last code block
    if (cursor < input.length) {
        tokens.push({ type: 'markdown', content: input.slice(cursor) });
    }

    return tokens;
}

// ============================================================================
// Inline Rendering
// ============================================================================

interface InlineToken {
    type: 'text' | 'bold' | 'italic' | 'strikethrough' | 'code' | 'link' | 'image';
    content: string;
    href?: string;
    alt?: string;
}

/**
 * Parse inline markdown elements (bold, italic, code, links, images)
 */
function parseInline(text: string): InlineToken[] {
    const tokens: InlineToken[] = [];
    let remaining = text;

    while (remaining.length > 0) {
        // Image: ![alt](url)
        const imageMatch = remaining.match(/^!\[([^\]]*)\]\(([^)]+)\)/);
        if (imageMatch) {
            tokens.push({
                type: 'image',
                content: imageMatch[2],
                alt: imageMatch[1],
            });
            remaining = remaining.slice(imageMatch[0].length);
            continue;
        }

        // Link: [text](url)
        const linkMatch = remaining.match(/^\[([^\]]+)\]\(([^)]+)\)/);
        if (linkMatch) {
            tokens.push({
                type: 'link',
                content: linkMatch[1],
                href: linkMatch[2],
            });
            remaining = remaining.slice(linkMatch[0].length);
            continue;
        }

        // Inline code: `code`
        const codeMatch = remaining.match(/^`([^`]+)`/);
        if (codeMatch) {
            tokens.push({
                type: 'code',
                content: codeMatch[1],
            });
            remaining = remaining.slice(codeMatch[0].length);
            continue;
        }

        // Bold: **text** or __text__
        const boldMatch = remaining.match(/^\*\*([^*]+)\*\*/);
        if (boldMatch) {
            tokens.push({
                type: 'bold',
                content: boldMatch[1],
            });
            remaining = remaining.slice(boldMatch[0].length);
            continue;
        }

        // Alternative bold: __text__
        const boldAltMatch = remaining.match(/^__([^_]+)__/);
        if (boldAltMatch) {
            tokens.push({
                type: 'bold',
                content: boldAltMatch[1],
            });
            remaining = remaining.slice(boldAltMatch[0].length);
            continue;
        }

        // Strikethrough: ~~text~~
        const strikeMatch = remaining.match(/^~~([^~]+)~~/);
        if (strikeMatch) {
            tokens.push({
                type: 'strikethrough',
                content: strikeMatch[1],
            });
            remaining = remaining.slice(strikeMatch[0].length);
            continue;
        }

        // Italic: *text* or _text_ (but not inside words)
        const italicMatch = remaining.match(/^(\*|_)([^*_]+)\1/);
        if (italicMatch) {
            tokens.push({
                type: 'italic',
                content: italicMatch[2],
            });
            remaining = remaining.slice(italicMatch[0].length);
            continue;
        }

        // Plain text - find next special character
        const nextSpecial = remaining.search(/[*_`!\[\\]/);
        if (nextSpecial === -1) {
            tokens.push({ type: 'text', content: remaining });
            break;
        } else if (nextSpecial === 0) {
            // Escape the special character and continue
            tokens.push({ type: 'text', content: remaining[0] });
            remaining = remaining.slice(1);
        } else {
            tokens.push({ type: 'text', content: remaining.slice(0, nextSpecial) });
            remaining = remaining.slice(nextSpecial);
        }
    }

    return tokens;
}

/**
 * Render inline tokens to React nodes
 */
function renderInline(tokens: InlineToken[], keyPrefix: string): ReactNode[] {
    const nodes: ReactNode[] = [];
    let index = 0;

    for (const token of tokens) {
        switch (token.type) {
            case 'text':
                nodes.push(token.content);
                break;

            case 'bold':
                nodes.push(
                    <strong key={`${keyPrefix}-bold-${index++}`} className="font-semibold text-foreground">
                        {renderInline(parseInline(token.content), `${keyPrefix}-bold-${index}`)}
                    </strong>
                );
                break;

            case 'italic':
                nodes.push(
                    <em key={`${keyPrefix}-italic-${index++}`} className="italic">
                        {renderInline(parseInline(token.content), `${keyPrefix}-italic-${index}`)}
                    </em>
                );
                break;

            case 'strikethrough':
                nodes.push(
                    <del key={`${keyPrefix}-strike-${index++}`} className="line-through text-muted-foreground">
                        {token.content}
                    </del>
                );
                break;

            case 'code':
                nodes.push(
                    <code
                        key={`${keyPrefix}-code-${index++}`}
                        className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em] text-destructive"
                    >
                        {token.content}
                    </code>
                );
                break;

            case 'link':
                nodes.push(
                    <a
                        key={`${keyPrefix}-link-${index++}`}
                        href={token.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:underline break-all"
                    >
                        {renderInline(parseInline(token.content), `${keyPrefix}-link-${index}`)}
                    </a>
                );
                break;

            case 'image':
                nodes.push(
                    <img
                        key={`${keyPrefix}-img-${index++}`}
                        src={token.href}
                        alt={token.alt}
                        className="max-w-full rounded-md border border-border my-2"
                        loading="lazy"
                    />
                );
                break;
        }
    }

    return nodes;
}

// ============================================================================
// Block Rendering
// ============================================================================

interface BlockToken {
    type: 'heading' | 'paragraph' | 'blockquote' | 'hr' | 'ul' | 'ol' | 'task' | 'table';
    content: string | string[];
    level?: number;
    language?: string;
}

/**
 * Parse block-level markdown elements
 */
function parseBlocks(content: string): BlockToken[] {
    const blocks: BlockToken[] = [];
    const lines = content.split('\n');
    let i = 0;

    while (i < lines.length) {
        const line = lines[i];

        // Skip empty lines
        if (!line.trim()) {
            i++;
            continue;
        }

        // Horizontal rule: ---, ***, ___
        if (/^([-*_])\1{2,}\s*$/.test(line)) {
            blocks.push({ type: 'hr', content: '' });
            i++;
            continue;
        }

        // Heading: # H1, ## H2, etc.
        const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
        if (headingMatch) {
            blocks.push({
                type: 'heading',
                content: headingMatch[2],
                level: headingMatch[1].length,
            });
            i++;
            continue;
        }

        // Blockquote: > text
        if (/^>\s?/.test(line)) {
            const quoteLines: string[] = [];
            while (i < lines.length && /^>\s?/.test(lines[i])) {
                quoteLines.push(lines[i].replace(/^>\s?/, ''));
                i++;
            }
            blocks.push({
                type: 'blockquote',
                content: quoteLines.join('\n'),
            });
            continue;
        }

        // Unordered list: - item, * item
        if (/^\s*[-*]\s+\[?[x ]\]?\s*/.test(line)) {
            const items: string[] = [];
            const taskItems: { text: string; checked: boolean }[] = [];
            let isTaskList = false;

            while (i < lines.length) {
                const listLine = lines[i];
                const taskMatch = listLine.match(/^\s*[-*]\s+\[([x ])\]\s*(.*)$/);
                const bulletMatch = listLine.match(/^\s*[-*]\s+(.+)$/);

                if (taskMatch) {
                    isTaskList = true;
                    taskItems.push({
                        text: taskMatch[2],
                        checked: taskMatch[1].toLowerCase() === 'x',
                    });
                    i++;
                } else if (bulletMatch && !isTaskList) {
                    items.push(bulletMatch[1]);
                    i++;
                } else {
                    break;
                }
            }

            if (isTaskList && taskItems.length > 0) {
                blocks.push({
                    type: 'task',
                    content: taskItems.map((t) => JSON.stringify(t)),
                });
            } else if (items.length > 0) {
                blocks.push({
                    type: 'ul',
                    content: items,
                });
            }
            continue;
        }

        // Ordered list: 1. item
        if (/^\s*\d+\.\s+/.test(line)) {
            const items: string[] = [];
            while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
                items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
                i++;
            }
            if (items.length > 0) {
                blocks.push({
                    type: 'ol',
                    content: items,
                });
            }
            continue;
        }

        // Table: | col | col |
        if (/^\|.*\|/.test(line)) {
            const rows: string[][] = [];

            while (i < lines.length && /^\|.*\|/.test(lines[i])) {
                const rowLine = lines[i];
                // Skip separator row (|---|---|)
                if (/^\|[\s|-]+\|/.test(rowLine)) {
                    i++;
                    continue;
                }
                const cells = rowLine
                    .slice(1, -1)
                    .split('|')
                    .map((cell) => cell.trim());
                rows.push(cells);
                i++;
            }

            if (rows.length > 0) {
                blocks.push({
                    type: 'table',
                    content: rows.map((r) => JSON.stringify(r)),
                });
            }
            continue;
        }

        // Paragraph: regular text
        const paragraphLines: string[] = [line];
        i++;
        while (
            i < lines.length &&
            lines[i].trim() &&
            !/^(#{1,6})\s+/.test(lines[i]) &&
            !/^\s*[-*]\s+/.test(lines[i]) &&
            !/^\s*\d+\.\s+/.test(lines[i]) &&
            !/^>\s?/.test(lines[i]) &&
            !/^([-*_])\1{2,}\s*$/.test(lines[i]) &&
            !/^\|.*\|/.test(lines[i])
        ) {
            paragraphLines.push(lines[i]);
            i++;
        }

        blocks.push({
            type: 'paragraph',
            content: paragraphLines.join('\n'),
        });
    }

    return blocks;
}

// ============================================================================
// Code Block Component
// ============================================================================

interface CodeBlockProps {
    code: string;
    language?: string;
}

function CodeBlock({ code, language }: CodeBlockProps) {
    const [copied, setCopied] = useState(false);

    const handleCopy = useCallback(async () => {
        try {
            await navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch (err) {
            console.error('Failed to copy code:', err);
        }
    }, [code]);

    return (
        <div className="my-4 rounded-lg border border-border overflow-hidden group">
            {/* Code block header */}
            <div className="flex items-center justify-between px-4 py-2 bg-muted/50 border-b border-border">
                {language ? (
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        {language}
                    </span>
                ) : (
                    <span className="text-xs text-muted-foreground">Code</span>
                )}
                <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs opacity-0 group-hover:opacity-100 transition-opacity"
                    onClick={handleCopy}
                >
                    {copied ? (
                        <>
                            <Check className="size-3 mr-1 text-green-500" />
                            Copied!
                        </>
                    ) : (
                        <>
                            <Copy className="size-3 mr-1" />
                            Copy
                        </>
                    )}
                </Button>
            </div>

            {/* Code content */}
            <pre className="m-0 overflow-x-auto bg-[#0d1117] p-4 text-sm text-slate-100 leading-relaxed">
                <code className="font-mono">{code}</code>
            </pre>
        </div>
    );
}

// ============================================================================
// Block Renderer
// ============================================================================

function renderBlock(block: BlockToken, index: number, keyPrefix: string): ReactNode {
    const blockKey = `${keyPrefix}-${block.type}-${index}`;

    switch (block.type) {
        case 'heading': {
            const level = block.level || 1;
            const classes = {
                1: 'text-2xl font-bold mt-6 mb-3 pb-2 border-b border-border',
                2: 'text-xl font-semibold mt-5 mb-2',
                3: 'text-lg font-semibold mt-4 mb-2',
                4: 'text-base font-semibold mt-3 mb-1',
                5: 'text-sm font-medium mt-2 mb-1',
                6: 'text-xs font-medium uppercase tracking-wide mt-2 mb-1 text-muted-foreground',
            }[level as 1 | 2 | 3 | 4 | 5 | 6];

            const headingContent = renderInline(parseInline(block.content as string), blockKey);
            
            switch (level) {
                case 1:
                    return <h1 key={blockKey} className={classes}>{headingContent}</h1>;
                case 2:
                    return <h2 key={blockKey} className={classes}>{headingContent}</h2>;
                case 3:
                    return <h3 key={blockKey} className={classes}>{headingContent}</h3>;
                case 4:
                    return <h4 key={blockKey} className={classes}>{headingContent}</h4>;
                case 5:
                    return <h5 key={blockKey} className={classes}>{headingContent}</h5>;
                case 6:
                    return <h6 key={blockKey} className={classes}>{headingContent}</h6>;
                default:
                    return <h1 key={blockKey} className={classes}>{headingContent}</h1>;
            }
        }

        case 'paragraph':
            return (
                <p key={blockKey} className="my-3 whitespace-pre-wrap break-words leading-relaxed">
                    {renderInline(parseInline(block.content as string), blockKey)}
                </p>
            );

        case 'blockquote':
            return (
                <blockquote
                    key={blockKey}
                    className="my-4 pl-4 border-l-4 border-primary bg-muted/30 py-3 pr-3 rounded-r-lg"
                >
                    <div className="italic text-muted-foreground">
                        {renderInline(parseInline(block.content as string), blockKey)}
                    </div>
                </blockquote>
            );

        case 'hr':
            return <hr key={blockKey} className="my-6 border-t-2 border-border" />;

        case 'ul':
            return (
                <ul key={blockKey} className="my-3 list-disc list-outside pl-6 space-y-1.5">
                    {(block.content as string[]).map((item, i) => (
                        <li key={`${blockKey}-item-${i}`} className="pl-1">
                            {renderInline(parseInline(item), `${blockKey}-item-${i}`)}
                        </li>
                    ))}
                </ul>
            );

        case 'ol':
            return (
                <ol key={blockKey} className="my-3 list-decimal list-outside pl-6 space-y-1.5">
                    {(block.content as string[]).map((item, i) => (
                        <li key={`${blockKey}-item-${i}`} className="pl-1">
                            {renderInline(parseInline(item), `${blockKey}-item-${i}`)}
                        </li>
                    ))}
                </ol>
            );

        case 'task':
            return (
                <ul key={blockKey} className="my-3 space-y-1.5">
                    {(block.content as string[]).map((itemJson, i) => {
                        try {
                            const { text, checked } = JSON.parse(itemJson) as {
                                text: string;
                                checked: boolean;
                            };
                            return (
                                <li
                                    key={`${blockKey}-task-${i}`}
                                    className="flex items-start gap-2 pl-1"
                                >
                                    <span
                                        className={`mt-1 flex-shrink-0 w-4 h-4 rounded border flex items-center justify-center ${
                                            checked
                                                ? 'bg-primary border-primary text-primary-foreground'
                                                : 'border-muted-foreground'
                                        }`}
                                    >
                                        {checked && <Check className="size-3" />}
                                    </span>
                                    <span
                                        className={
                                            checked ? 'line-through text-muted-foreground' : ''
                                        }
                                    >
                                        {renderInline(parseInline(text), `${blockKey}-task-${i}`)}
                                    </span>
                                </li>
                            );
                        } catch {
                            return null;
                        }
                    })}
                </ul>
            );

        case 'table': {
            const rows = (block.content as string[]).map((r) => {
                try {
                    return JSON.parse(r) as string[];
                } catch {
                    return [];
                }
            });

            if (rows.length === 0) return null;

            const headers = rows[0];
            const dataRows = rows.slice(1);

            return (
                <div key={blockKey} className="my-4 overflow-x-auto">
                    <table className="w-full border-collapse text-sm">
                        <thead>
                            <tr className="bg-muted/50">
                                {headers.map((header, i) => (
                                    <th
                                        key={`${blockKey}-th-${i}`}
                                        className="px-4 py-2 text-left font-semibold border border-border"
                                    >
                                        {renderInline(parseInline(header), `${blockKey}-th-${i}`)}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {dataRows.map((row, ri) => (
                                <tr
                                    key={`${blockKey}-tr-${ri}`}
                                    className={ri % 2 === 0 ? 'bg-transparent' : 'bg-muted/20'}
                                >
                                    {row.map((cell, ci) => (
                                        <td
                                            key={`${blockKey}-td-${ri}-${ci}`}
                                            className="px-4 py-2 border border-border align-top"
                                        >
                                            {renderInline(parseInline(cell), `${blockKey}-td-${ri}-${ci}`)}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            );
        }

        default:
            return null;
    }
}

// ============================================================================
// Main Component
// ============================================================================

export function MarkdownRenderer({ text, className, emptyFallback }: MarkdownRendererProps) {
    // Handle empty/missing content
    if (!text || !text.trim()) {
        return <>{emptyFallback ?? <em className="text-muted-foreground text-sm">No content provided.</em>}</>;
    }

    // Tokenize by code blocks first
    const tokens = tokenizeByCodeBlocks(text);

    return (
        <div className={`markdown-body ${className || ''}`}>
            {tokens.map((token, index) => {
                if (token.type === 'code') {
                    return (
                        <CodeBlock
                            key={`code-${index}`}
                            code={token.content}
                            language={token.language}
                        />
                    );
                }

                // Parse and render markdown blocks
                const blocks = parseBlocks(token.content);
                return (
                    <div key={`md-${index}`}>
                        {blocks.map((block, blockIndex) =>
                            renderBlock(block, blockIndex, `md-${index}`)
                        )}
                    </div>
                );
            })}
        </div>
    );
}
