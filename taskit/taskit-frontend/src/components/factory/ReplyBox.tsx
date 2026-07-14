import { useState } from 'react';

interface ReplyBoxProps {
    onSubmit: (content: string) => Promise<void>;
    placeholder?: string;
    buttonLabel?: string;
}

export function ReplyBox({ onSubmit, placeholder = 'Reply…', buttonLabel = 'Reply' }: ReplyBoxProps) {
    const [content, setContent] = useState('');
    const [submitting, setSubmitting] = useState(false);

    const handleSubmit = async () => {
        const trimmed = content.trim();
        if (!trimmed || submitting) return;
        setSubmitting(true);
        try {
            await onSubmit(trimmed);
            setContent('');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="flex items-center gap-2">
            <input
                type="text"
                value={content}
                onChange={e => setContent(e.target.value)}
                placeholder={placeholder}
                className="min-w-0 flex-1 rounded-md border bg-background px-2 py-1 text-sm"
            />
            <button
                type="button"
                onClick={handleSubmit}
                disabled={submitting || !content.trim()}
                className="shrink-0 rounded-md border px-2 py-1 text-xs transition-colors hover:bg-accent/50 disabled:opacity-50"
            >
                {buttonLabel}
            </button>
        </div>
    );
}
