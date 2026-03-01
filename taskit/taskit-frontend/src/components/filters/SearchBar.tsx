import { useEffect, useState } from 'react';
import { Search } from 'lucide-react';
import { Input } from '@/components/ui/input';

interface SearchBarProps {
    value: string;
    onSearchChange: (value: string) => void;
    placeholder?: string;
    debounceMs?: number;
    ariaLabel?: string;
}

export function SearchBar({
    value,
    onSearchChange,
    placeholder = 'Search...',
    debounceMs = 300,
    ariaLabel = 'Search',
}: SearchBarProps) {
    const [internal, setInternal] = useState(value);

    useEffect(() => {
        setInternal(value);
    }, [value]);

    useEffect(() => {
        // Only emit when user input differs from current external value.
        // This prevents pagination resets caused by re-emitting the same query.
        if (internal === value) return;
        const timer = window.setTimeout(() => onSearchChange(internal), debounceMs);
        return () => window.clearTimeout(timer);
    }, [internal, value, debounceMs, onSearchChange]);

    return (
        <div className="relative min-w-[220px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" aria-hidden="true" />
            <Input
                value={internal}
                onChange={(e) => setInternal(e.target.value)}
                className="pl-8"
                placeholder={placeholder}
                aria-label={ariaLabel}
            />
        </div>
    );
}
