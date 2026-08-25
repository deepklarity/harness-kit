import React, { useState, useEffect, useRef } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import {
    Download,
    CheckCircle2,
    AlertCircle,
    AlertTriangle,
    Info,
    Sparkles,
    Loader2,
    FileCode,
    Layers,
    UploadCloud,
    Folder,
    ExternalLink,
    FileText,
    Layout,
    Server,
    ShieldAlert,
    GitBranch,
    Trash2,
    X,
    XCircle
} from 'lucide-react';
import type { IntegrationService } from '../services/integration/IntegrationService';
import type { InstalledSkill, RejectedSkill } from '../types';

interface SkillImporterCardProps {
    service: IntegrationService;
}

export function SkillImporterCard({ service }: SkillImporterCardProps) {
    const [skills, setSkills] = useState<InstalledSkill[]>([]);
    const [rejectedSkills, setRejectedSkills] = useState<RejectedSkill[]>([]);
    const [loading, setLoading] = useState(true);
    const [importing, setImporting] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [deletingName, setDeletingName] = useState<string | null>(null);
    const [deletingAll, setDeletingAll] = useState(false);
    const [clearingAll, setClearingAll] = useState(false);
    const [togglingName, setTogglingName] = useState<string | null>(null);
    const [forcingName, setForcingName] = useState<string | null>(null);
    const [sourceUrl, setSourceUrl] = useState('');
    const [activeTab, setActiveTab] = useState<'url' | 'upload'>('url');
    const [dragOver, setDragOver] = useState(false);
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    
    const [messages, setMessages] = useState<string[]>([]);
    const [skipped, setSkipped] = useState<{ name: string; reason: string }[]>([]);
    const [error, setError] = useState<string | null>(null);

    const fileInputRef = useRef<HTMLInputElement>(null);
    const [skillTableTab, setSkillTableTab] = useState<'installed' | 'uninstalled'>('installed');

    const loadSkills = async () => {
        try {
            setLoading(true);
            const res = await service.fetchSkills();
            setSkills(res.skills || []);
            if (res.rejected_skills) {
                setRejectedSkills(res.rejected_skills);
            }
        } catch (err: any) {
            setError(err?.message || 'Failed loading installed skills.');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadSkills();
    }, [service]);

    // Auto-dismiss success/info notifications after 3 seconds
    useEffect(() => {
        if (messages.length > 0 || skipped.length > 0) {
            const timer = setTimeout(() => {
                setMessages([]);
                setSkipped([]);
            }, 3000);
            return () => clearTimeout(timer);
        }
    }, [messages, skipped]);

    // Auto-dismiss errors after 5 seconds
    useEffect(() => {
        if (error) {
            const timer = setTimeout(() => {
                setError(null);
            }, 5000);
            return () => clearTimeout(timer);
        }
    }, [error]);



    const handleImportUrl = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!sourceUrl.trim()) return;

        try {
            setImporting(true);
            setError(null);
            setMessages([]);
            setSkipped([]);
            const res = await service.importSkills(sourceUrl.trim());
            setSkills(res.skills || []);
            setMessages(res.messages || []);
            const rawSkipped = res.skipped || [];
            const formattedSkipped = rawSkipped.map((s: any) => typeof s === 'string' ? { name: s, reason: s } : { name: s.name, reason: s.reason || '' });
            setSkipped(formattedSkipped);

            if (Array.isArray(res.rejected_skills)) {
                setRejectedSkills(res.rejected_skills);
            }

            if (res.imported && res.imported.length > 0) {
                setSourceUrl('');
            }
        } catch (err: any) {
            const bodyMsg = err?.body?.error || err?.body?.message;
            setError(bodyMsg || err?.message || err?.error || 'Failed importing skills.');
        } finally {
            setImporting(false);
        }
    };

    const handleFileUpload = async (fileToUpload?: File) => {
        const targetFile = fileToUpload || selectedFile;
        if (!targetFile) return;

        const MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024;
        if (targetFile.size > MAX_FILE_SIZE_BYTES) {
            setError(`File '${targetFile.name}' (${(targetFile.size / (1024 * 1024)).toFixed(1)} MB) exceeds the maximum allowed upload size of 15 MB.`);
            return;
        }

        try {
            setUploading(true);
            setError(null);
            setMessages([]);
            setSkipped([]);
            const res = await service.uploadSkill(targetFile);
            setSkills(res.skills || []);
            setMessages(res.messages || []);
            const rawSkipped = res.skipped || [];
            const formattedSkipped = rawSkipped.map((s: any) => typeof s === 'string' ? { name: s, reason: s } : { name: s.name, reason: s.reason || '' });
            setSkipped(formattedSkipped);

            if (Array.isArray(res.rejected_skills)) {
                setRejectedSkills(res.rejected_skills);
            }

            setSelectedFile(null);
            if (fileInputRef.current) {
                fileInputRef.current.value = '';
            }
        } catch (err: any) {
            const bodyMsg = err?.body?.error || err?.body?.message;
            setError(bodyMsg || err?.message || err?.error || 'Failed uploading skill file.');
        } finally {
            setUploading(false);
        }
    };

    const handleForceInstall = async (rejectedSkill: RejectedSkill) => {
        try {
            setForcingName(rejectedSkill.name);
            setError(null);
            let res;
            if (activeTab === 'upload' && selectedFile) {
                res = await service.uploadSkill(selectedFile, true);
            } else {
                let targetSource = (rejectedSkill.source || '').trim();
                if (!targetSource || targetSource.startsWith('File Upload:')) {
                    targetSource = sourceUrl.trim() || 'https://github.com/mattpocock/skills';
                }
                if (targetSource.includes('/') && !targetSource.startsWith('http://') && !targetSource.startsWith('https://') && !targetSource.startsWith('/')) {
                    targetSource = `https://github.com/${targetSource}`;
                }
                res = await service.importSkills(targetSource, true, rejectedSkill.name);
            }
            if (res && Array.isArray(res.skills)) {
                setSkills(res.skills);
                if (res.rejected_skills) {
                    setRejectedSkills(res.rejected_skills);
                } else {
                    setRejectedSkills(prev => prev.filter(s => s.name !== rejectedSkill.name));
                }
            } else {
                await loadSkills();
            }
            setMessages([`Successfully force-installed skill '${rejectedSkill.name}'.`]);
            setSkillTableTab('installed');
        } catch (err: any) {
            const bodyMsg = err?.body?.error || err?.body?.message;
            setError(bodyMsg || err?.message || 'Failed force installing skill.');
        } finally {
            setForcingName(null);
        }
    };

    const handleDelete = async (skillDir: string) => {
        if (!window.confirm(`Are you sure you want to delete skill '${skillDir}'?\nThis will permanently delete .claude/skills/${skillDir} from disk.`)) return;

        try {
            setDeletingName(skillDir);
            setError(null);
            const res = await service.deleteSkill(skillDir);
            if (res && Array.isArray(res.skills)) {
                setSkills(res.skills);
            } else {
                await loadSkills();
            }
            setMessages([`Successfully deleted skill '${skillDir}' from codebase.`]);
            setSkipped([]);
        } catch (err: any) {
            setError(err?.message || err?.error || 'Failed deleting skill.');
        } finally {
            setDeletingName(null);
        }
    };

    const handleDeleteAllInstalled = async () => {
        if (skills.length === 0) return;
        if (!window.confirm(`Are you sure you want to delete all ${skills.length} installed skill(s)?\nThis will permanently delete all installed skills from .claude/skills/ on disk.`)) return;

        try {
            setDeletingAll(true);
            setError(null);
            const res = await service.deleteAllSkills();
            if (res && Array.isArray(res.skills)) {
                setSkills(res.skills);
            } else {
                await loadSkills();
            }
            setMessages(['Successfully deleted all installed skills from codebase.']);
            setSkipped([]);
        } catch (err: any) {
            setError(err?.message || err?.error || 'Failed deleting all skills.');
        } finally {
            setDeletingAll(false);
        }
    };

    const handleClearAllUninstalled = async () => {
        if (rejectedSkills.length === 0) return;
        if (!window.confirm(`Are you sure you want to clear all ${rejectedSkills.length} uninstalled / rejected skill(s) from the table?`)) return;

        try {
            setClearingAll(true);
            setError(null);
            if (service.clearRejectedSkills) {
                await service.clearRejectedSkills();
            }
            setRejectedSkills([]);
            setMessages(['Cleared all uninstalled / rejected skills from the list.']);
        } catch (err: any) {
            setError(err?.message || err?.error || 'Failed clearing uninstalled skills.');
        } finally {
            setClearingAll(false);
        }
    };

    const handleTogglePreset = async (skillDir: string, enabled: boolean) => {
        try {
            setTogglingName(skillDir);
            setError(null);
            const res = await service.toggleSkillPreset(skillDir, enabled);
            if (res && Array.isArray(res.skills)) {
                setSkills(res.skills);
            } else {
                await loadSkills();
            }
        } catch (err: any) {
            setError(err?.message || err?.error || 'Failed updating skill preset.');
        } finally {
            setTogglingName(null);
        }
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setDragOver(false);
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            const file = e.dataTransfer.files[0];
            setSelectedFile(file);
            void handleFileUpload(file);
        }
    };

    const renderCategoryBadge = (skill: InstalledSkill) => {
        const cat = skill.category || 'General Engineering';

        switch (cat) {
            case 'UI / Frontend':
                return (
                    <span className="inline-flex items-center gap-1 rounded-full bg-indigo-500/10 px-2 py-0.5 text-[11px] font-medium text-indigo-500 dark:bg-indigo-500/20">
                        <Layout className="size-3" />
                        UI / Frontend
                    </span>
                );
            case 'Backend / API':
                return (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-500 dark:bg-emerald-500/20">
                        <Server className="size-3" />
                        Backend / API
                    </span>
                );
            case 'Audit & Security':
                return (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400 dark:bg-amber-500/20">
                        <ShieldAlert className="size-3" />
                        Audit
                    </span>
                );
            case 'Architecture & Design':
                return (
                    <span className="inline-flex items-center gap-1 rounded-full bg-purple-500/10 px-2 py-0.5 text-[11px] font-medium text-purple-500 dark:bg-purple-500/20">
                        <Layers className="size-3" />
                        Architecture
                    </span>
                );
            case 'Testing & QA':
                return (
                    <span className="inline-flex items-center gap-1 rounded-full bg-cyan-500/10 px-2 py-0.5 text-[11px] font-medium text-cyan-500 dark:bg-cyan-500/20">
                        <CheckCircle2 className="size-3" />
                        Testing & QA
                    </span>
                );
            case 'Documentation & Rules':
                return (
                    <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/10 px-2 py-0.5 text-[11px] font-medium text-rose-500 dark:bg-rose-500/20">
                        <FileText className="size-3" />
                        Docs & Rules
                    </span>
                );
            case 'DevOps & Workflow':
                return (
                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-500/10 px-2 py-0.5 text-[11px] font-medium text-slate-400 dark:bg-slate-500/20">
                        <GitBranch className="size-3" />
                        DevOps
                    </span>
                );
            case 'General Engineering':
            default:
                return (
                    <span className="inline-flex items-center gap-1 rounded-full bg-sky-500/10 px-2 py-0.5 text-[11px] font-medium text-sky-500 dark:bg-sky-500/20">
                        <Sparkles className="size-3" />
                        General Skill
                    </span>
                );
        }
    };

    const renderSourceLink = (source?: string) => {
        const rawSource = source || 'https://github.com/mattpocock/skills';

        if (rawSource.startsWith('http://') || rawSource.startsWith('https://')) {
            const displayLabel = rawSource.replace(/^https?:\/\/(www\.)?github\.com\//, '');
            return (
                <a
                    href={rawSource}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-blue-500 hover:underline max-w-[240px] truncate"
                    title={rawSource}
                >
                    <ExternalLink className="size-3 shrink-0" />
                    <span className="truncate">{displayLabel || rawSource}</span>
                </a>
            );
        }

        if (rawSource.startsWith('File Upload:')) {
            return (
                <span className="inline-flex items-center gap-1 text-xs text-amber-500 dark:text-amber-400 max-w-[240px] truncate" title={rawSource}>
                    <UploadCloud className="size-3 shrink-0" />
                    <span className="truncate">{rawSource.replace('File Upload: ', '')}</span>
                </span>
            );
        }

        return (
            <a
                href="https://github.com/mattpocock/skills"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-blue-500 hover:underline max-w-[240px] truncate"
                title={rawSource}
            >
                <ExternalLink className="size-3 shrink-0" />
                <span className="truncate">{rawSource}</span>
            </a>
        );
    };

    return (
        <Card className="mt-4">
            <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Download className="size-4 text-muted-foreground" />
                        <div>
                            <CardTitle className="text-base">Skill Importer Tool</CardTitle>
                            <p className="text-xs text-muted-foreground mt-0.5">
                                Import Claude Code skills from Git repos (GitHub), file uploads (.zip, .md), or local folders into <code className="text-foreground">.claude/skills/</code>.
                            </p>
                        </div>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Method Navigation Tabs */}
                <div className="flex items-center justify-between border-b border-border pb-2">
                    <div className="flex items-center gap-2">
                        <Button
                            type="button"
                            variant={activeTab === 'url' ? 'secondary' : 'ghost'}
                            size="xs"
                            onClick={() => setActiveTab('url')}
                            className="gap-1.5 text-xs font-medium"
                        >
                            <FileCode className="size-3.5" />
                            Git URL / Path Input
                        </Button>
                        <Button
                            type="button"
                            variant={activeTab === 'upload' ? 'secondary' : 'ghost'}
                            size="xs"
                            onClick={() => setActiveTab('upload')}
                            className="gap-1.5 text-xs font-medium"
                        >
                            <UploadCloud className="size-3.5" />
                            Upload File (.zip / .md)
                        </Button>
                    </div>
                </div>

                {/* Import via Git URL or Local Path */}
                {activeTab === 'url' && (
                    <form onSubmit={handleImportUrl} className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                        <div className="relative flex-1">
                            <Input
                                type="text"
                                value={sourceUrl}
                                onChange={(e) => setSourceUrl(e.target.value)}
                                placeholder="e.g. https://github.com/mattpocock/skills or /path/to/skills"
                                className="text-xs"
                                disabled={importing}
                            />
                        </div>
                        <Button type="submit" size="sm" disabled={importing || !sourceUrl.trim()} className="gap-1.5 whitespace-nowrap">
                            {importing ? (
                                <>
                                    <Loader2 className="size-3.5 animate-spin" />
                                    Importing...
                                </>
                            ) : (
                                <>
                                    <Download className="size-3.5" />
                                    Import Skills
                                </>
                            )}
                        </Button>
                    </form>
                )}

                {/* Import via File Upload Dropzone */}
                {activeTab === 'upload' && (
                    <div
                        onDragOver={(e) => {
                            e.preventDefault();
                            setDragOver(true);
                        }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={handleDrop}
                        className={`border-2 border-dashed rounded-lg p-4 text-center transition-colors ${
                            dragOver ? 'border-primary bg-primary/5' : 'border-border hover:border-muted-foreground/50'
                        }`}
                    >
                        <input
                            type="file"
                            ref={fileInputRef}
                            accept=".zip,.tar.gz,.tgz,.md"
                            onChange={(e) => {
                                if (e.target.files && e.target.files.length > 0) {
                                    setSelectedFile(e.target.files[0]);
                                    void handleFileUpload(e.target.files[0]);
                                }
                            }}
                            className="hidden"
                        />

                        <div className="flex flex-col items-center justify-center gap-1.5">
                            <UploadCloud className="size-6 text-muted-foreground" />
                            <div className="text-xs text-foreground font-medium">
                                Drag & drop skill archive (<code className="text-xs">.zip</code>, <code className="text-xs">.md</code>) here
                            </div>
                            <p className="text-[11px] text-muted-foreground">or click below to choose a file from your computer (Max 15 MB)</p>
                            
                            <Button
                                type="button"
                                variant="outline"
                                size="xs"
                                onClick={() => fileInputRef.current?.click()}
                                disabled={uploading}
                                className="mt-1 gap-1.5"
                            >
                                {uploading ? (
                                    <>
                                        <Loader2 className="size-3.5 animate-spin" />
                                        Uploading...
                                    </>
                                ) : (
                                    <>
                                        <Folder className="size-3.5" />
                                        Browse File
                                    </>
                                )}
                            </Button>
                        </div>
                    </div>
                )}

                {/* Notifications & Feedback */}
                {error && (
                    <div className="flex items-center justify-between gap-2 rounded-md bg-destructive/10 p-3 text-xs text-destructive border border-destructive/20">
                        <div className="flex items-start gap-2">
                            <AlertCircle className="size-4 shrink-0 mt-0.5" />
                            <span>{error}</span>
                        </div>
                        <button
                            type="button"
                            onClick={() => setError(null)}
                            className="text-destructive/80 hover:text-destructive shrink-0 p-0.5 rounded transition-colors"
                            title="Dismiss error"
                        >
                            <X className="size-3.5" />
                        </button>
                    </div>
                )}

                {(messages.length > 0 || skipped.length > 0) && (() => {
                    if (messages.length === 1 && !messages[0].startsWith('Successfully imported skill')) {
                        const isSuccess = messages[0].startsWith('Successfully') || messages[0].startsWith('Cleared');
                        return (
                            <div className={`flex items-center justify-between gap-2 rounded-md p-3 text-xs border transition-all ${
                                isSuccess
                                    ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
                                    : 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20'
                            }`}>
                                <div className="flex items-center gap-2">
                                    {isSuccess ? <CheckCircle2 className="size-4 shrink-0" /> : <Info className="size-4 shrink-0" />}
                                    <span>{messages[0]}</span>
                                </div>
                                <button
                                    type="button"
                                    onClick={() => { setMessages([]); setSkipped([]); }}
                                    className="text-muted-foreground hover:text-foreground shrink-0 p-0.5 rounded transition-colors"
                                    title="Dismiss notification"
                                >
                                    <X className="size-3.5" />
                                </button>
                            </div>
                        );
                    }

                    const successMessages = messages.filter(m => m.startsWith('Successfully'));
                    const installedCount = successMessages.length;
                    const existingSkipped = skipped.filter(s => (s.reason || '').toLowerCase().includes('already exists'));
                    const rejectedSkipped = skipped.filter(s => !(s.reason || '').toLowerCase().includes('already exists'));

                    let notificationText = '';
                    let isSuccess = false;

                    if (installedCount > 0) {
                        isSuccess = true;
                        notificationText = `Successfully installed ${installedCount} skill(s).`;
                        const extras: string[] = [];
                        if (existingSkipped.length > 0) {
                            extras.push(`${existingSkipped.length} existing skill(s) skipped`);
                        }
                        if (rejectedSkipped.length > 0) {
                            extras.push(`${rejectedSkipped.length} uninstalled skill(s) skipped below threshold`);
                        }
                        if (extras.length > 0) {
                            notificationText += ` (${extras.join(', ')})`;
                        }
                    } else if (existingSkipped.length > 0 && rejectedSkipped.length === 0) {
                        isSuccess = false;
                        notificationText = `No new skills imported — all ${existingSkipped.length} skill(s) already exist in workspace.`;
                    } else if (rejectedSkipped.length > 0 && existingSkipped.length === 0) {
                        isSuccess = false;
                        notificationText = `No skills met the quality threshold for automatic installation (${rejectedSkipped.length} uninstalled skill(s) added to Uninstalled tab).`;
                    } else if (existingSkipped.length > 0 || rejectedSkipped.length > 0) {
                        isSuccess = false;
                        notificationText = `No new skills installed (${existingSkipped.length} already existing, ${rejectedSkipped.length} uninstalled below threshold).`;
                    } else {
                        isSuccess = messages[0]?.startsWith('Successfully') ?? false;
                        notificationText = messages[0] || 'Operation completed.';
                    }

                    return (
                        <div className={`flex items-center justify-between gap-2 rounded-md p-3 text-xs border transition-all ${
                            isSuccess
                                ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
                                : 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20'
                        }`}>
                            <div className="flex items-center gap-2">
                                {isSuccess ? <CheckCircle2 className="size-4 shrink-0" /> : <Info className="size-4 shrink-0" />}
                                <span>{notificationText}</span>
                            </div>
                            <button
                                type="button"
                                onClick={() => { setMessages([]); setSkipped([]); }}
                                className="text-muted-foreground hover:text-foreground shrink-0 p-0.5 rounded transition-colors"
                                title="Dismiss notification"
                            >
                                <X className="size-3.5" />
                            </button>
                        </div>
                    );
                })()}

                {/* Skills Table Section (Tabbed: Installed vs Uninstalled / Rejected) */}
                <div className="pt-2 border-t border-border">
                    <div className="flex items-center justify-between pb-2 mb-3">
                        <div className="flex items-center gap-2">
                            <Button
                                type="button"
                                variant={skillTableTab === 'installed' ? 'secondary' : 'ghost'}
                                size="xs"
                                onClick={() => setSkillTableTab('installed')}
                                className="gap-1.5 text-xs font-medium"
                            >
                                <CheckCircle2 className="size-3.5 text-emerald-500" />
                                Installed Skills ({skills.length})
                            </Button>
                            <Button
                                type="button"
                                variant={skillTableTab === 'uninstalled' ? 'secondary' : 'ghost'}
                                size="xs"
                                onClick={() => setSkillTableTab('uninstalled')}
                                className="gap-1.5 text-xs font-medium"
                            >
                                <AlertTriangle className="size-3.5 text-amber-500" />
                                Uninstalled Skills ({rejectedSkills.length})
                            </Button>
                        </div>

                        {/* Bulk Action Controls on extreme right of tab row */}
                        <div className="flex items-center gap-2">
                            {skillTableTab === 'installed' && skills.length > 0 && (
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="xs"
                                    onClick={() => void handleDeleteAllInstalled()}
                                    disabled={deletingAll}
                                    className="gap-1.5 text-xs text-destructive border-destructive/30 hover:bg-destructive/10 hover:text-destructive font-medium"
                                    title="Delete all installed custom skills from codebase"
                                >
                                    {deletingAll ? (
                                        <>
                                            <Loader2 className="size-3.5 animate-spin" />
                                            Deleting All...
                                        </>
                                    ) : (
                                        <>
                                            <Trash2 className="size-3.5" />
                                            Delete All
                                        </>
                                    )}
                                </Button>
                            )}

                            {skillTableTab === 'uninstalled' && rejectedSkills.length > 0 && (
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="xs"
                                    onClick={() => void handleClearAllUninstalled()}
                                    disabled={clearingAll}
                                    className="gap-1.5 text-xs text-amber-700 dark:text-amber-300 border-amber-500/30 hover:bg-amber-500/20 font-medium"
                                    title="Clear all uninstalled skills from the table list"
                                >
                                    {clearingAll ? (
                                        <>
                                            <Loader2 className="size-3.5 animate-spin" />
                                            Clearing All...
                                        </>
                                    ) : (
                                        <>
                                            <XCircle className="size-3.5" />
                                            Clear All
                                        </>
                                    )}
                                </Button>
                            )}
                        </div>
                    </div>

                    {/* TAB 1: Installed Skills Table */}
                    {skillTableTab === 'installed' && (
                        <>
                            {loading ? (
                                <div className="py-8 text-center text-xs text-muted-foreground flex items-center justify-center gap-2">
                                    <Loader2 className="size-4 animate-spin" />
                                    Loading installed skills...
                                </div>
                            ) : skills.length === 0 ? (
                                <div className="py-8 text-center text-xs text-muted-foreground border border-dashed border-border rounded-md">
                                    No custom skills installed. Import via URL, folder, or file upload above.
                                </div>
                            ) : (
                                <div className="border border-border rounded-md overflow-hidden max-h-[420px] overflow-y-auto w-full">
                                    <table className="w-full table-fixed text-left border-collapse text-xs">
                                        <thead className="sticky top-0 z-20 border-b border-border shadow-sm">
                                            <tr className="bg-muted/90 text-foreground font-semibold">
                                                <th className="py-2.5 px-3 bg-muted/90 w-[20%]">Import Source</th>
                                                <th className="py-2.5 px-3 bg-muted/90 w-[44%]">Skill &amp; Description</th>
                                                <th className="py-2.5 px-3 bg-muted/90 text-center w-[18%]">Category</th>
                                                <th className="py-2.5 px-3 bg-muted/90 text-center w-[18%]">Action</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-border">
                                            {skills.map((skill) => (
                                                <tr key={skill.directory} className="hover:bg-muted/40 transition-colors">
                                                    {/* Import Source */}
                                                    <td className="py-2.5 px-3 align-top whitespace-nowrap overflow-hidden text-ellipsis">
                                                        {renderSourceLink(skill.imported_from)}
                                                    </td>

                                                    {/* Skill Name & Description */}
                                                    <td className="py-2.5 px-3 align-top">
                                                        <div className="font-medium text-foreground flex items-center gap-1.5 flex-wrap">
                                                            <span>{skill.title}</span>
                                                            <code className="text-[11px] text-muted-foreground bg-muted/60 px-1 py-0.2 rounded font-mono">
                                                                /{skill.name}
                                                            </code>
                                                        </div>
                                                        {skill.description && (
                                                            <p className="text-[11px] text-muted-foreground line-clamp-2 mt-0.5 leading-relaxed">
                                                                {skill.description}
                                                            </p>
                                                        )}
                                                        {/* Missing System CLI Warning */}
                                                        {skill.missing_system_cli && skill.missing_system_cli.length > 0 && (
                                                            <div className="mt-1 text-[11px] text-amber-600 dark:text-amber-400 flex items-center gap-1">
                                                                <AlertTriangle className="size-3 shrink-0" />
                                                                <span>CLI not found: <code>{skill.missing_system_cli.join(', ')}</code></span>
                                                            </div>
                                                        )}
                                                    </td>

                                                    {/* Category-wise Badge */}
                                                    <td className="py-2.5 px-3 align-top text-center whitespace-nowrap">
                                                        <div className="flex justify-center">
                                                            {renderCategoryBadge(skill)}
                                                        </div>
                                                    </td>

                                                    {/* Action / Checkbox & Delete Button */}
                                                    <td className="py-2.5 px-3 align-top text-center whitespace-nowrap">
                                                        <div className="flex items-center justify-center gap-2 mt-0.5">
                                                            <label
                                                                className="inline-flex items-center gap-1.5 cursor-pointer text-xs text-muted-foreground hover:text-foreground select-none"
                                                                title={skill.preset_enabled ? "Skill is active as Task & Spec preset. Uncheck to remove." : "Check to add skill as Task & Spec preset."}
                                                            >
                                                                <input
                                                                    type="checkbox"
                                                                    checked={Boolean(skill.preset_enabled)}
                                                                    disabled={togglingName === skill.directory}
                                                                    onChange={(e) => void handleTogglePreset(skill.directory, e.target.checked)}
                                                                    className="size-3.5 rounded border-border text-primary focus:ring-primary/20 cursor-pointer accent-primary"
                                                                />
                                                                <span className="text-[11px] font-medium">Preset</span>
                                                            </label>
                                                            <Button
                                                                variant="ghost"
                                                                size="xs"
                                                                onClick={() => handleDelete(skill.directory)}
                                                                disabled={deletingName === skill.directory}
                                                                className="text-muted-foreground hover:text-destructive hover:bg-destructive/10 shrink-0"
                                                                title={`Delete .claude/skills/${skill.directory} from codebase`}
                                                            >
                                                                {deletingName === skill.directory ? (
                                                                    <Loader2 className="size-3.5 animate-spin" />
                                                                ) : (
                                                                    <Trash2 className="size-3.5 text-slate-400 hover:text-destructive" />
                                                                )}
                                                            </Button>
                                                        </div>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </>
                    )}

                    {/* TAB 2: Uninstalled / Rejected Skills Table */}
                    {skillTableTab === 'uninstalled' && (
                        <>
                            {rejectedSkills.length === 0 ? (
                                <div className="py-8 text-center text-xs text-muted-foreground border border-dashed border-amber-500/20 bg-amber-500/5 rounded-md">
                                    No uninstalled or rejected skills found. All imported skills qualified and were installed!
                                </div>
                            ) : (
                                <div className="border border-amber-500/30 rounded-md overflow-hidden max-h-[420px] overflow-y-auto w-full">
                                    <table className="w-full table-fixed text-left border-collapse text-xs">
                                        <thead className="sticky top-0 z-20 border-b border-amber-500/30 shadow-sm">
                                            <tr className="bg-amber-100 dark:bg-amber-950 text-amber-900 dark:text-amber-200 font-semibold">
                                                <th className="py-2.5 px-3 bg-amber-100 dark:bg-amber-950 w-[20%]">Import Source</th>
                                                <th className="py-2.5 px-3 bg-amber-100 dark:bg-amber-950 w-[44%]">Skill &amp; Description</th>
                                                <th className="py-2.5 px-3 bg-amber-100 dark:bg-amber-950 text-center w-[12%]">Score</th>
                                                <th className="py-2.5 px-3 bg-amber-100 dark:bg-amber-950 text-center w-[12%]">Category</th>
                                                <th className="py-2.5 px-3 bg-amber-100 dark:bg-amber-950 text-center w-[12%]">Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-amber-500/20 bg-amber-500/5">
                                            {rejectedSkills
                                                .slice()
                                                .sort((a, b) => (b.usefulness_score ?? 0) - (a.usefulness_score ?? 0))
                                                .map((skill) => (
                                                <tr key={skill.name} className="hover:bg-amber-500/10 transition-colors">
                                                    {/* Import Source */}
                                                    <td className="py-2.5 px-3 align-top whitespace-nowrap overflow-hidden text-ellipsis">
                                                        {renderSourceLink(skill.source)}
                                                    </td>

                                                    {/* Skill Name & Description */}
                                                    <td className="py-2.5 px-3 align-top">
                                                        <div className="font-medium text-foreground flex items-center gap-1.5 flex-wrap">
                                                            <span>{skill.title || skill.name}</span>
                                                            <code className="text-[11px] text-muted-foreground bg-muted/60 px-1 py-0.2 rounded font-mono">
                                                                /{skill.name}
                                                            </code>
                                                        </div>
                                                        <p className="text-[11px] text-muted-foreground line-clamp-2 mt-0.5 leading-relaxed">
                                                            {skill.description || skill.reason || 'Not classified as useful for Harness Kit.'}
                                                        </p>
                                                    </td>

                                                    {/* Usefulness Score Column */}
                                                    <td className="py-2.5 px-3 align-top text-center whitespace-nowrap">
                                                        <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/20">
                                                            {skill.usefulness_score ?? 0}/100
                                                        </span>
                                                    </td>

                                                    {/* Category-wise Badge */}
                                                    <td className="py-2.5 px-3 align-top text-center whitespace-nowrap">
                                                        <div className="flex justify-center">
                                                            {renderCategoryBadge({ category: skill.category } as InstalledSkill)}
                                                        </div>
                                                    </td>

                                                    {/* Action / Force Install Button */}
                                                    <td className="py-2.5 px-3 align-top text-center whitespace-nowrap">
                                                        <Button
                                                            variant="outline"
                                                            size="xs"
                                                            onClick={() => void handleForceInstall(skill)}
                                                            disabled={forcingName === skill.name}
                                                            className="border-amber-500/30 text-amber-700 dark:text-amber-300 hover:bg-amber-500/20 gap-1 font-medium"
                                                        >
                                                            {forcingName === skill.name ? (
                                                                <Loader2 className="size-3.5 animate-spin" />
                                                            ) : (
                                                                <Download className="size-3.5" />
                                                            )}
                                                            Force Install
                                                        </Button>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
