import { useCallback, useEffect, useRef, useState } from 'react';
import { ImagePlus, X } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

interface ImageDropZoneProps {
    files: File[];
    onFilesChange: (files: File[]) => void;
    maxFiles?: number;
    maxSizeMb?: number;
}

export function ImageDropZone({ files, onFilesChange, maxFiles = 4, maxSizeMb = 10 }: ImageDropZoneProps) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [dragOver, setDragOver] = useState(false);
    const [previews, setPreviews] = useState<string[]>([]);
    const [lightbox, setLightbox] = useState<{ url: string; name: string } | null>(null);

    // Rebuild preview URLs whenever files change
    useEffect(() => {
        const urls = files.map(f => URL.createObjectURL(f));
        setPreviews(urls);
        return () => { urls.forEach(u => URL.revokeObjectURL(u)); };
    }, [files]);

    const addFiles = useCallback((incoming: FileList | File[]) => {
        const maxBytes = maxSizeMb * 1024 * 1024;
        const valid: File[] = [];
        for (const f of Array.from(incoming)) {
            if (!f.type.startsWith('image/')) continue;
            if (f.size > maxBytes) continue;
            if (files.length + valid.length >= maxFiles) break;
            valid.push(f);
        }
        if (valid.length > 0) onFilesChange([...files, ...valid]);
    }, [files, onFilesChange, maxFiles, maxSizeMb]);

    const removeFile = (idx: number) => {
        onFilesChange(files.filter((_, i) => i !== idx));
    };

    const onDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setDragOver(false);
        addFiles(e.dataTransfer.files);
    };

    const onPaste = useCallback((e: ClipboardEvent) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        const imageFiles: File[] = [];
        for (const item of Array.from(items)) {
            if (item.kind === 'file' && item.type.startsWith('image/')) {
                const f = item.getAsFile();
                if (f) imageFiles.push(f);
            }
        }
        if (imageFiles.length > 0) addFiles(imageFiles);
    }, [addFiles]);

    useEffect(() => {
        window.addEventListener('paste', onPaste);
        return () => window.removeEventListener('paste', onPaste);
    }, [onPaste]);

    return (
        <div className="flex flex-col gap-2">
            <div
                className={`relative border-2 border-dashed rounded-lg p-3 flex flex-col items-center justify-center gap-1.5 cursor-pointer transition-colors text-center
                    ${dragOver ? 'border-cyan-500 bg-cyan-500/10' : 'border-border/50 hover:border-border hover:bg-muted/30'}`}
                onClick={() => inputRef.current?.click()}
                onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDrop}
            >
                <ImagePlus className="size-4 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">
                    Drop images, click to browse, or paste (Cmd+V)
                </span>
                <span className="text-[10px] text-muted-foreground/60">
                    Max {maxFiles} images · {maxSizeMb}MB each · images only
                </span>
                <input
                    ref={inputRef}
                    type="file"
                    multiple
                    accept="image/*"
                    className="hidden"
                    onChange={e => { if (e.target.files) addFiles(e.target.files); e.target.value = ''; }}
                />
            </div>

            {previews.length > 0 && (
                <div className="flex flex-wrap gap-2">
                    {previews.map((url, idx) => (
                        <div key={idx} className="relative group">
                            <img
                                src={url}
                                alt={files[idx]?.name}
                                className="size-16 object-cover rounded border border-border/40 cursor-zoom-in"
                                onClick={e => { e.stopPropagation(); setLightbox({ url, name: files[idx]?.name ?? '' }); }}
                            />
                            <button
                                type="button"
                                onClick={e => { e.stopPropagation(); removeFile(idx); }}
                                className="absolute -top-1.5 -right-1.5 size-4 rounded-full bg-background border border-border flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-destructive hover:border-destructive"
                            >
                                <X className="size-2.5" />
                            </button>
                            <span className="absolute bottom-0 left-0 right-0 text-[8px] text-white/80 bg-black/50 px-0.5 truncate rounded-b">
                                {files[idx]?.name}
                            </span>
                        </div>
                    ))}
                </div>
            )}

            {lightbox && (
                <Dialog open={true} onOpenChange={() => setLightbox(null)}>
                    <DialogContent className="max-w-[90vw] max-h-[90vh] p-0 bg-black/95 border-border/20 overflow-hidden flex items-center justify-center">
                        <DialogHeader className="sr-only">
                            <DialogTitle>{lightbox.name}</DialogTitle>
                        </DialogHeader>
                        <img
                            src={lightbox.url}
                            alt={lightbox.name}
                            className="max-w-full max-h-[85vh] object-contain"
                        />
                        <span className="absolute bottom-3 left-1/2 -translate-x-1/2 text-xs text-white/60 font-mono bg-black/60 px-3 py-1 rounded-full">
                            {lightbox.name}
                        </span>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    );
}
