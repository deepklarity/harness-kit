import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

interface PaginationControlsProps {
    count: number;
    page: number;
    pageSize: number;
    onPageChange: (page: number) => void;
    onPageSizeChange: (size: number) => void;
}

export function PaginationControls({
    count,
    page,
    pageSize,
    onPageChange,
    onPageSizeChange,
}: PaginationControlsProps) {
    const totalPages = Math.max(1, Math.ceil(count / pageSize));
    const current = Math.min(page, totalPages);

    return (
        <div className="flex items-center justify-between gap-3 py-4">
            <div className="flex items-center gap-2">
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
            </div>
            <div className="flex items-center gap-3">
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onPageChange(current - 1)}
                    disabled={current <= 1}
                >
                    <ChevronLeft className="size-3.5 mr-1" /> Previous
                </Button>
                <span className="text-sm text-muted-foreground" aria-current="page">
                    Page {current} of {totalPages}
                </span>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onPageChange(current + 1)}
                    disabled={current >= totalPages}
                >
                    Next <ChevronRight className="size-3.5 ml-1" />
                </Button>
            </div>
        </div>
    );
}
