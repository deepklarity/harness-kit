import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { Member, Task } from '@/types';
import { useService } from '@/contexts/ServiceContext';
import { formatDuration, getStatusColor } from '@/utils/transformer';
import { FilterBar, MultiSelectFilter, PaginationControls, SearchBar, SortControl, DateRangeFilter } from '@/components/filters';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Pencil, ChevronDown, ChevronUp, Bot, User } from 'lucide-react';

const VISIBLE_TASKS = 5;

interface MembersPageProps {
    selectedBoard?: string;
    refreshKey?: number;
    onEditMember?: (member: Member) => void;
}

function splitParam(value: string | null): string[] {
    if (!value) return [];
    return value.split(',').map(v => v.trim()).filter(Boolean);
}

export function MembersPage({ selectedBoard, refreshKey = 0, onEditMember }: MembersPageProps) {
    const service = useService();
    const [searchParams, setSearchParams] = useSearchParams();
    const [members, setMembers] = useState<Member[]>([]);
    const [count, setCount] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [tasks, setTasks] = useState<Task[]>([]);

    const query = useMemo(() => {
        const page = Number(searchParams.get('page') || '1');
        const pageSize = Number(searchParams.get('page_size') || '25');
        return {
            q: searchParams.get('q') || '',
            role: splitParam(searchParams.get('role')) as Array<'HUMAN' | 'AGENT' | 'ADMIN'>,
            joined_from: searchParams.get('joined_from') || undefined,
            joined_to: searchParams.get('joined_to') || undefined,
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

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const resp = await service.fetchMembersPage({
                ...query,
                board: selectedBoard,
            });
            setMembers(resp.results);
            setCount(resp.count);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load members');
        } finally {
            setLoading(false);
        }
    }, [service, query, selectedBoard]);

    useEffect(() => { load(); }, [load, refreshKey]);

    const loadTasks = useCallback(async () => {
        try {
            const pageSize = 500;
            let page = 1;
            let allTasks: Task[] = [];
            let total = 0;
            while (page <= 10) {
                const resp = await service.fetchTimelinePage({
                    board: selectedBoard,
                    page,
                    page_size: pageSize,
                    sort: '-created_at',
                });
                allTasks = allTasks.concat(resp.results);
                total = resp.count;
                if (!resp.next || allTasks.length >= total) break;
                page += 1;
            }
            setTasks(allTasks);
        } catch {
            setTasks([]);
        }
    }, [service, selectedBoard]);

    useEffect(() => { loadTasks(); }, [loadTasks, refreshKey]);

    return (
        <div>
            <FilterBar resultCount={loading ? undefined : count} resultLabel={count === 1 ? 'member' : 'members'} onClearAll={() => setSearchParams(prev => {
                const next = new URLSearchParams();
                const board = prev.get('board');
                if (board) next.set('board', board);
                return next;
            }, { replace: true })}>
                <SearchBar
                    value={query.q}
                    onSearchChange={(value) => setParam('q', value || undefined)}
                    placeholder="Search members by name or email..."
                    ariaLabel="Search members"
                />
                <MultiSelectFilter
                    label="Role"
                    options={[
                        { label: 'Human', value: 'HUMAN' },
                        { label: 'Agent', value: 'AGENT' },
                        { label: 'Admin', value: 'ADMIN' },
                    ]}
                    selected={query.role}
                    onChange={(next) => setParam('role', next.length ? next.join(',') : undefined)}
                />
                <DateRangeFilter
                    label="Join date"
                    from={query.joined_from}
                    to={query.joined_to}
                    onChange={(fromVal, toVal) => {
                        setSearchParams(prev => {
                            const next = new URLSearchParams(prev);
                            if (fromVal) next.set('joined_from', fromVal); else next.delete('joined_from');
                            if (toVal) next.set('joined_to', toVal); else next.delete('joined_to');
                            next.set('page', '1');
                            return next;
                        }, { replace: true });
                    }}
                />
                <SortControl
                    value={query.sort}
                    onChange={(value) => setParam('sort', value)}
                    options={[
                        { label: 'Name', value: 'name' },
                        { label: 'Join date', value: 'created_at' },
                        { label: 'Task count', value: 'task_count' },
                    ]}
                />
            </FilterBar>

            {error && <div className="text-sm text-destructive mb-3">{error}</div>}
            {loading ? (
                <div className="text-sm text-muted-foreground py-8">Loading members...</div>
            ) : (
                <>
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4">
                        {members.map(member => (
                            <MemberCard
                                key={member.id}
                                member={member}
                                tasks={tasks}
                                onEdit={onEditMember}
                            />
                        ))}
                        {members.length === 0 && (
                            <div className="text-sm text-muted-foreground">No members found.</div>
                        )}
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
        </div>
    );
}

function MemberCard({ member, tasks, onEdit }: { member: Member; tasks: Task[]; onEdit?: (m: Member) => void }) {
    const [expanded, setExpanded] = useState(false);
    const isAgent = member.role === 'AGENT';

    const memberTasks = useMemo(() => tasks.filter(t => t.assigneeIds.includes(member.id)), [tasks, member.id]);
    const done = useMemo(() => memberTasks.filter(t => t.currentStatus.toLowerCase().includes('done')).length, [memberTasks]);
    const active = useMemo(() => memberTasks.filter(t =>
        t.currentStatus.toLowerCase().includes('doing') ||
        t.currentStatus.toLowerCase().includes('progress') ||
        t.currentStatus.toLowerCase().includes('testing')
    ).length, [memberTasks]);
    const avgWorkTime = useMemo(() =>
        memberTasks.length > 0 ? memberTasks.reduce((sum, t) => sum + t.workTimeMs, 0) / memberTasks.length : 0,
    [memberTasks]);

    const visibleTasks = expanded ? memberTasks : memberTasks.slice(0, VISIBLE_TASKS);
    const hiddenCount = memberTasks.length - VISIBLE_TASKS;

    return (
        <Card className="border-border overflow-hidden">
            <CardContent className="p-0">
                <div className="p-4 pb-3">
                    <div className="flex items-start gap-3">
                        <div
                            className="size-10 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0"
                            style={{ background: member.color }}
                        >
                            {member.initials}
                        </div>
                        <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                                <span className="text-sm font-semibold truncate">{member.fullName}</span>
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-0.5 shrink-0">
                                    {isAgent ? <Bot className="size-2.5" /> : <User className="size-2.5" />}
                                    {member.role}
                                </Badge>
                            </div>
                            <div className="text-xs text-muted-foreground truncate">{member.email}</div>
                        </div>
                        {onEdit && (
                            <Button variant="ghost" size="icon" className="size-7 shrink-0" onClick={() => onEdit(member)}>
                                <Pencil className="size-3.5" />
                            </Button>
                        )}
                    </div>
                </div>

                <div className="grid grid-cols-4 border-y border-border bg-muted/30">
                    {[
                        { value: memberTasks.length, label: 'Total' },
                        { value: done, label: 'Done' },
                        { value: active, label: 'Active' },
                        { value: formatDuration(avgWorkTime), label: 'Avg Time' },
                    ].map((stat, i) => (
                        <div key={stat.label} className={`text-center py-2.5 ${i > 0 ? 'border-l border-border' : ''}`}>
                            <div className="text-sm font-bold">{stat.value}</div>
                            <div className="text-[10px] text-muted-foreground">{stat.label}</div>
                        </div>
                    ))}
                </div>

                {memberTasks.length > 0 && (
                    <div className="px-4 pt-2.5 pb-3">
                        <div className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider mb-1.5">
                            Recent Tasks
                        </div>
                        <div className="space-y-0.5">
                            {visibleTasks.map(t => (
                                <div key={t.id} className="flex items-center justify-between gap-2 text-xs py-1 px-1.5 rounded hover:bg-muted/40 transition-colors">
                                    <span className="text-muted-foreground truncate min-w-0">
                                        <span className="font-mono text-[10px] opacity-60">#{t.idShort}</span>{' '}
                                        {t.name}
                                    </span>
                                    <Badge
                                        variant="outline"
                                        className="text-[10px] px-1.5 py-0 shrink-0"
                                        style={{
                                            background: `color-mix(in srgb, ${getStatusColor(t.currentStatus)}, transparent 85%)`,
                                            color: getStatusColor(t.currentStatus),
                                            borderColor: 'transparent',
                                        }}
                                    >
                                        {t.currentStatus}
                                    </Badge>
                                </div>
                            ))}
                        </div>
                        {hiddenCount > 0 && (
                            <button
                                onClick={() => setExpanded(prev => !prev)}
                                className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors mt-1.5 mx-auto"
                            >
                                {expanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
                                {expanded ? 'Show less' : `${hiddenCount} more task${hiddenCount !== 1 ? 's' : ''}`}
                            </button>
                        )}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
