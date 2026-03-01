import { useState } from 'react';
import type { Spec } from '../types';
import { useService } from '../contexts/ServiceContext';
import { useToast } from '@/hooks/use-toast';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
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
import { Search, FileText, AlertTriangle, Eye, EyeOff, Trash2, ChevronLeft, ChevronRight } from 'lucide-react';

interface SpecListViewProps {
    specs: Spec[];
    onSpecClick: (spec: Spec) => void;
    onDataChange: () => void;
}

const SPECS_PER_PAGE = 50;

export function SpecListView({ specs, onSpecClick, onDataChange }: SpecListViewProps) {
    const service = useService();
    const { toast } = useToast();
    const [searchTerm, setSearchTerm] = useState('');
    const [showAbandoned, setShowAbandoned] = useState(false);
    const [specToClone, setSpecToClone] = useState<Spec | null>(null);
    const [specToDelete, setSpecToDelete] = useState<Spec | null>(null);
    const [cloning, setCloning] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [page, setPage] = useState(1);

    const handleClone = async () => {
        if (!specToClone) return;
        setCloning(true);
        try {
            await service.cloneSpec(specToClone.id);
            toast({
                title: "Success",
                description: `Spec "${specToClone.title}" cloned successfully`,
            });
            onDataChange();
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
            onDataChange();
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

    const filtered = specs.filter(s => {
        if (!showAbandoned && s.abandoned) return false;
        if (searchTerm) {
            const term = searchTerm.toLowerCase();
            return s.title.toLowerCase().includes(term) || s.id.toLowerCase().includes(term);
        }
        return true;
    });

    const totalPages = Math.max(1, Math.ceil(filtered.length / SPECS_PER_PAGE));
    const currentPage = Math.min(page, totalPages);
    const startIdx = (currentPage - 1) * SPECS_PER_PAGE;
    const pageSpecs = filtered.slice(startIdx, startIdx + SPECS_PER_PAGE);

    return (
        <div>
            <div className="flex items-center gap-3 mb-6">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                    <Input placeholder="Search specs..." value={searchTerm} onChange={e => setSearchTerm(e.target.value)} className="pl-8" />
                </div>
                <Button variant={showAbandoned ? 'default' : 'outline'} size="sm" className="gap-1.5"
                    onClick={() => setShowAbandoned(!showAbandoned)}>
                    {showAbandoned ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
                    {showAbandoned ? 'Showing Abandoned' : 'Show Abandoned'}
                </Button>
            </div>

            {filtered.length === 0 ? (
                <div className="text-center py-16 text-muted-foreground">
                    <FileText className="size-12 mx-auto mb-4 opacity-50" />
                    <div className="text-lg font-semibold text-muted-foreground mb-2">No specs found</div>
                    <p>Specs are created via Odin's planning workflow.</p>
                </div>
            ) : (
                <>
                <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-5">
                    {pageSpecs.map((spec, i) => (
                        <Card key={spec.id}
                            className="cursor-pointer bg-card/50 backdrop-blur-sm border-border hover:border-primary/30 hover:shadow-lg hover:-translate-y-0.5 transition-all animate-in-up group"
                            style={{ animationDelay: `${Math.min(i + 1, 6) * 50}ms` }}
                            onClick={() => onSpecClick(spec)}>
                            <CardContent className="p-5">
                                <div className="flex items-start justify-between mb-3">
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
                                            <Badge variant="destructive" className="text-[10px] gap-1 shrink-0">
                                                <AlertTriangle className="size-2.5" /> Abandoned
                                            </Badge>
                                        )}
                                        <Badge variant="secondary" className="text-[10px] shrink-0">
                                            {spec.taskCount} task{spec.taskCount !== 1 ? 's' : ''}
                                        </Badge>
                                    </div>
                                </div>
                                <div className="text-base font-semibold leading-snug mb-2">{spec.title}</div>
                                <div className="text-xs text-muted-foreground">
                                    Source: {spec.source}
                                </div>
                                {spec.content && (
                                    <div className="text-xs text-muted-foreground mt-2 line-clamp-2">{spec.content}</div>
                                )}
                            </CardContent>
                        </Card>
                    ))}
                </div>
                {totalPages > 1 && (
                    <div className="flex items-center justify-center gap-3 py-4">
                        <Button variant="outline" size="sm" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>
                            <ChevronLeft className="size-3.5 mr-1" /> Previous
                        </Button>
                        <span className="text-sm text-muted-foreground">
                            Page {currentPage} of {totalPages}
                        </span>
                        <Button variant="outline" size="sm" disabled={currentPage >= totalPages} onClick={() => setPage(currentPage + 1)}>
                            Next <ChevronRight className="size-3.5 ml-1" />
                        </Button>
                    </div>
                )}
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
        </div>
    );
}
