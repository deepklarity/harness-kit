import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { ChevronRight } from 'lucide-react';
import { PROSE_CLASSES } from './constants';

interface ReportSectionProps {
    title: string;
    content: string;
    defaultOpen?: boolean;
    mono?: boolean;
}

export function ReportSection({ title, content, defaultOpen = true, mono = false }: ReportSectionProps) {
    const [isOpen, setIsOpen] = useState(defaultOpen);

    return (
        <div>
            <button
                className="flex items-center gap-1.5 w-full px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider hover:bg-secondary/30 transition-colors"
                onClick={() => setIsOpen(!isOpen)}
            >
                <ChevronRight className={`size-3 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                {title}
            </button>
            {isOpen && (
                mono ? (
                    <div className="px-4 pb-4 text-xs text-foreground/70 whitespace-pre-wrap leading-relaxed font-mono max-h-[60vh] overflow-y-auto bg-secondary/10">
                        {content}
                    </div>
                ) : (
                    <div className={`px-4 pb-4 text-sm text-foreground/80 leading-relaxed max-h-[60vh] overflow-y-auto ${PROSE_CLASSES}`}>
                        <ReactMarkdown>{content}</ReactMarkdown>
                    </div>
                )
            )}
        </div>
    );
}
