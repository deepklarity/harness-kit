import { cn } from '@/lib/utils';

interface SwitchProps {
    checked: boolean;
    onCheckedChange: (checked: boolean) => void;
    disabled?: boolean;
    id?: string;
    className?: string;
    'aria-label'?: string;
}

export function Switch({ checked, onCheckedChange, disabled, id, className, 'aria-label': ariaLabel }: SwitchProps) {
    return (
        <button
            type="button"
            role="switch"
            aria-checked={checked}
            aria-label={ariaLabel}
            id={id}
            disabled={disabled}
            onClick={() => !disabled && onCheckedChange(!checked)}
            className={cn(
                'relative inline-flex h-4 w-7 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
                checked ? 'bg-primary' : 'bg-input',
                className,
            )}
        >
            <span
                className={cn(
                    'pointer-events-none block h-3 w-3 rounded-full bg-background shadow-lg ring-0 transition-transform',
                    checked ? 'translate-x-3' : 'translate-x-0',
                )}
            />
        </button>
    );
}
