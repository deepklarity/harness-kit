import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { Board, Spec } from '@/types';
import { useService } from '@/contexts/ServiceContext';
import { useToast } from '@/hooks/use-toast';
import { usePolling } from '@/hooks/usePolling';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { DollarSign, Plus, Terminal, Trash2 } from 'lucide-react';
import { formatCost } from '@/utils/costEstimation';
import { OdinGuideModal, OdinGuideContent } from '@/components/OdinGuideModal';
import { FilterBar, MultiSelectFilter, PaginationControls, SearchBar, SortControl, DateRangeFilter } from '@/components/filters';

interface SpecsPageProps {
    selectedBoard?: string;
    refreshKey?: number;
    currentBoard?: Board | null;
    onSpecClick: (spec: Spec) => void;
    onDataChange?: () => void;
}

function splitParam(value: string | null): string[] {
    if (!value) return [];
    return value.split(',').map(v => v.trim()).filter(Boolean);
}

export function SpecsPage({ selectedBoard, refreshKey = 0, currentBoard, onSpecClick, onDataChange }: SpecsPageProps) {
    const service = useService();
    const { toast } = useToast();
    const [searchParams, setSearchParams] = useSearchParams();
    const [specs, setSpecs] = useState<Spec[]>([]);
    const [count, setCount] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [specToClone, setSpecToClone] = useState<Spec | null>(null);
    const [specToDelete, setSpecToDelete] = useState<Spec | null>(null);
    const [cloning, setCloning] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [guideOpen, setGuideOpen] = useState(false);
    const [pollingEnabled, setPollingEnabled] = useState(false);
    const hasLoadedOnce = useRef(false);

    const handleClone = async () => {
        if (!specToClone) return;
        setCloning(true);
        try {
            await service.cloneSpec(specToClone.id);
            toast({
                title: "Success",
                description: `Spec "${specToClone.title}" cloned successfully`,
            });
            load();
            onDataChange?.();
        } catch (err) {
            console.error("Failed to clone spec", err);
            toast({
                title: "Error",
                description: "Failed to clone spec",
                variant: "destructive"
            });
        } finally {
            setCloning(false);
            setSpecToClone(null);
        }
    };

    const handleDelete = async () => {
        if (!specToDelete) return;
        setDeleting(true);
        try {
            await service.deleteSpec(specToDelete.id);
            toast({
                title: "Success",
                description: `Spec "${specToDelete.title}" deleted successfully`,
            });
            load();
            onDataChange?.();
        } catch (err) {
            console.error("Failed to delete spec", err);
            toast({
                title: "Error",
                description: "Failed to delete spec",
                variant: "destructive"
            });
        } finally {
            setDeleting(false);
            setSpecToDelete(null);
        }
    };

    const query = useMemo(() => {
        const page = Number(searchParams.get('page') || '1');
        const pageSize = Number(searchParams.get('page_size') || '25');
        return {
            q: searchParams.get('q') || '',
            status: splitParam(searchParams.get('status')) as Array<'active' | 'abandoned'>,
            created_from: searchParams.get('created_from') || undefined,
            created_to: searchParams.get('created_to') || undefined,
            sort: searchParams.get('sort') || undefined,
            page: Number.isNaN(page) ? 1 : page,
            page_size: Number.isNaN(pageSize) ? 25 : pageSize,
        };
    }, [searchParams]);

    const setParam = useCallback((key: string, value?: string) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            const current = next.get(key) || '';
            const nextValue = value || '';
            const changed = current !== nextValue;
            if (!value) next.delete(key);
            else next.set(key, value);
            if (key !== 'page' && changed) next.set('page', '1');
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    const load = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
        if (!silent) setLoading(true);
        setError(null);
        try {
            const resp = await service.fetchSpecsPage({
                ...query,
                board: selectedBoard,
            });
            setSpecs(resp.results);
            setCount(resp.count);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load specs');
        } finally {
            if (!silent) setLoading(false);
        }
    }, [service, query, selectedBoard]);

    useEffect(() => {
        let active = true;
        const silent = hasLoadedOnce.current;
        void load({ silent }).finally(() => {
            if (active) {
                setPollingEnabled(true);
                hasLoadedOnce.current = true;
            }
        });
        return () => { active = false; };
    }, [load, refreshKey]);

    usePolling(() => load({ silent: true }), {
        enabled: pollingEnabled,
        intervalMs: Number(import.meta.env.VITE_POLL_INTERVAL_MS || 15000),
        immediate: false,
    });

    const isEmpty = !loading && specs.length === 0;

    return (
        <div>
            <FilterBar resultCount={loading ? undefined : count} resultLabel={count === 1 ? 'spec' : 'specs'} onClearAll={() => setSearchParams(prev => {
                const next = new URLSearchParams();
                const board = prev.get('board');
                if (board) next.set('board', board);
                return next;
            }, { replace: true })}
                trailing={
                    <Button size="sm" variant="outline" onClick={() => setGuideOpen(true)}>
                        <Plus className="size-3.5 mr-1" />
                        Spec
                    </Button>
                }
            >
                <SearchBar
                    value={query.q}
                    onSearchChange={(value) => setParam('q', value || undefined)}
                    placeholder="Search specs by title/content..."
                    ariaLabel="Search specs"
                />
                <MultiSelectFilter
                    label="Status"
                    options={[
                        { label: 'Active', value: 'active' },
                        { label: 'Abandoned', value: 'abandoned' },
                    ]}
                    selected={query.status}
                    onChange={(next) => setParam('status', next.length ? next.join(',') : undefined)}
                />
                <DateRangeFilter
                    label="Created"
                    from={query.created_from}
                    to={query.created_to}
                    onChange={(fromVal, toVal) => {
                        setSearchParams(prev => {
                            const next = new URLSearchParams(prev);
                            if (fromVal) next.set('created_from', fromVal); else next.delete('created_from');
                            if (toVal) next.set('created_to', toVal); else next.delete('created_to');
                            next.set('page', '1');
                            return next;
                        }, { replace: true });
                    }}
                />
                <SortControl
                    value={query.sort}
                    onChange={(value) => setParam('sort', value)}
                    options={[
                        { label: 'Created date', value: 'created_at' },
                        { label: 'Title', value: 'title' },
                        { label: 'Task count', value: 'task_count' },
                    ]}
                />
            </FilterBar>

            {error && <div className="text-sm text-destructive mb-3">{error}</div>}
            {loading ? (
                <div className="text-sm text-muted-foreground py-8">Loading specs...</div>
            ) : isEmpty ? (
                <Card className="max-w-lg mx-auto mt-8">
                    <CardContent className="p-6">
                        <div className="flex items-center gap-2 mb-4">
                            <Terminal className="size-5 text-muted-foreground" />
                            <h3 className="text-base font-semibold">No specs yet</h3>
                        </div>
                        <p className="text-sm text-muted-foreground mb-4">
                            Create a spec from your terminal and it will appear here automatically.
                        </p>
                        <OdinGuideContent
                            workingDir={currentBoard?.workingDir}
                            needsInit={!!currentBoard && !currentBoard.odinInitialized}
                        />
                    </CardContent>
                </Card>
            ) : (
                <>
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-5">
                        {specs.map(spec => (
                            <Card key={spec.id} className="cursor-pointer hover:border-primary/40 transition-colors" onClick={() => onSpecClick(spec)}>
                                <CardContent className="p-4">
                                    <div className="flex items-center justify-between mb-2">
                                        <Badge variant="outline" className="text-[10px] font-mono">#{spec.id}</Badge>
                                        <div className="flex gap-1.5 items-center">
                                            <Button variant="ghost" size="sm" className="h-6 px-2 text-[10px] text-muted-foreground hover:text-primary transition-colors"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setSpecToClone(spec);
                                                }}>
                                                Clone
                                            </Button>
                                            <Button variant="ghost" size="sm" className="h-6 px-2 text-[10px] text-muted-foreground hover:text-destructive transition-colors"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setSpecToDelete(spec);
                                                }}>
                                                <Trash2 className="size-3 mr-1" />
                                                Delete
                                            </Button>
                                            {spec.abandoned && (
                                                <Badge variant="destructive" className="text-[10px]">
                                                    Abandoned
                                                </Badge>
                                            )}
                                            <Badge variant="secondary" className="text-[10px]">
                                                {spec.taskCount} task{spec.taskCount !== 1 ? 's' : ''}
                                            </Badge>
                                        </div>
                                    </div>
                                    <div className="text-base font-semibold mb-2">{spec.title}</div>
                                    {(spec.costSummary?.total_cost_usd || spec.costSummary?.reflection_cost_usd) ? (
                                        <div className="flex items-center gap-3 text-[10px] font-mono mb-1.5">
                                            <span className="flex items-center gap-1 text-emerald-400">
                                                <DollarSign className="size-3" />
                                                {formatCost(spec.costSummary.total_cost_usd)}
                                            </span>
                                            {(spec.costSummary.reflection_cost_usd ?? 0) > 0 && (
                                                <span className="text-violet-400">
                                                    +{formatCost(spec.costSummary.reflection_cost_usd)} reflect
                                                </span>
                                            )}
                                        </div>
                                    ) : null}
                                    <div className="text-xs text-muted-foreground line-clamp-2">{spec.content || 'No content'}</div>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                    <PaginationControls
                        count={count}
                        page={query.page}
                        pageSize={query.page_size}
                        onPageChange={(page) => setParam('page', String(page))}
                        onPageSizeChange={(size) => setParam('page_size', String(size))}
                    />
                </>
            )}

            <AlertDialog open={!!specToClone} onOpenChange={(open) => !open && setSpecToClone(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Clone Spec</AlertDialogTitle>
                        <AlertDialogDescription>
                            Are you sure you want to clone spec "{specToClone?.title}"?
                            This will copy the spec and all its associated tasks.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={cloning}>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={(e) => {
                            e.preventDefault();
                            handleClone();
                        }} disabled={cloning} className="bg-primary text-primary-foreground">
                            {cloning ? 'Cloning...' : 'Clone'}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            <AlertDialog open={!!specToDelete} onOpenChange={(open) => !open && setSpecToDelete(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete Spec</AlertDialogTitle>
                        <AlertDialogDescription>
                            Are you sure you want to delete spec "{specToDelete?.title}"?
                            This action cannot be undone and will delete all associated tasks.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={(e) => {
                            e.preventDefault();
                            handleDelete();
                        }} disabled={deleting} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
                            {deleting ? 'Deleting...' : 'Delete'}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            <OdinGuideModal open={guideOpen} onOpenChange={setGuideOpen} board={currentBoard ?? null} />
        </div>
    );
}
