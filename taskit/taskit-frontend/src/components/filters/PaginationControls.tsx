import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

interface PaginationControlsProps {
    count: number;
    page: number;
    pageSize: number;
    onPageChange: (page: number) => void;
    onPageSizeChange: (size: number) => void;
    hideRowsSelector?: boolean;
}

export function PaginationControls({
    count,
    page,
    pageSize,
    onPageChange,
    onPageSizeChange,
    hideRowsSelector = false,
}: PaginationControlsProps) {
    const totalPages = Math.max(1, Math.ceil(count / pageSize));
    const current = Math.min(page, totalPages);

    return (
        <div className="flex flex-wrap items-center justify-between gap-3 py-4">
            <div className="flex items-center gap-2">
                {!hideRowsSelector && (
                    <>
                        <span className="text-xs text-muted-foreground">Rows</span>
                        <Select value={String(pageSize)} onValueChange={(v) => onPageSizeChange(Number(v))}>
                            <SelectTrigger className="w-[90px] h-8" aria-label="Page size">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {[10, 25, 50, 100].map(size => (
                                    <SelectItem key={size} value={String(size)}>{size}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </>
                )}
            </div>
            <div className="flex items-center gap-1.5 sm:gap-3">
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onPageChange(current - 1)}
                    disabled={current <= 1}
                >
                    <ChevronLeft className="size-3.5 sm:mr-1" />
                    <span className="hidden sm:inline">Previous</span>
                </Button>
                <span className="text-xs sm:text-sm text-muted-foreground whitespace-nowrap" aria-current="page">
                    {current} / {totalPages}
                </span>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onPageChange(current + 1)}
                    disabled={current >= totalPages}
                >
                    <span className="hidden sm:inline">Next</span>
                    <ChevronRight className="size-3.5 sm:ml-1" />
                </Button>
            </div>
        </div>
    );
}
