import type { Member, Task } from '../types';
import { formatDuration, getStatusColor } from '../utils/transformer';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { UserRound, Pencil } from 'lucide-react';

interface MemberCardsProps {
    members: Member[];
    tasks: Task[];
    onEditMember?: (member: Member) => void;
}

export function MemberCards({ members, tasks, onEditMember }: MemberCardsProps) {
    if (members.length === 0) {
        return (
            <div className="text-center py-16 text-muted-foreground">
                <UserRound className="size-10 mx-auto mb-3 opacity-50" />
                <div className="text-base font-medium mb-1">No members</div>
                <p className="text-sm">Create a user to get started.</p>
            </div>
        );
    }

    // Show members with tasks first, then the rest
    const taskCountById = new Map<string, number>();
    for (const t of tasks) {
        for (const id of t.assigneeIds) {
            taskCountById.set(id, (taskCountById.get(id) ?? 0) + 1);
        }
    }
    const sorted = [...members].sort((a, b) =>
        (taskCountById.get(b.id) ?? 0) - (taskCountById.get(a.id) ?? 0)
    );

    return (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4 mb-8">
            {sorted.map(member => {
                const memberTasks = tasks.filter(t => t.assigneeIds.includes(member.id));
                const done = memberTasks.filter(t =>
                    t.currentStatus.toLowerCase().includes('done')
                ).length;
                const inProgress = memberTasks.filter(t =>
                    t.currentStatus.toLowerCase().includes('doing') ||
                    t.currentStatus.toLowerCase().includes('progress') ||
                    t.currentStatus.toLowerCase().includes('testing')
                ).length;
                const avgWorkTime = memberTasks.length > 0
                    ? memberTasks.reduce((sum, t) => sum + t.workTimeMs, 0) / memberTasks.length
                    : 0;

                return (
                    <Card key={member.id} className="border-border">
                        <CardContent className="p-5">
                            <div className="flex items-center gap-3 mb-4">
                                <div
                                    className="size-10 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0"
                                    style={{ background: member.color }}
                                >
                                    {member.initials}
                                </div>
                                <div className="flex-1 min-w-0">
                                    <div className="text-sm font-semibold">{member.fullName}</div>
                                    <div className="text-xs text-muted-foreground">@{member.username}</div>
                                </div>
                                {onEditMember && (
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="size-7 shrink-0"
                                        onClick={() => onEditMember(member)}
                                    >
                                        <Pencil className="size-3.5" />
                                    </Button>
                                )}
                            </div>

                            <div className="grid grid-cols-3 gap-3 mb-4">
                                {[
                                    { value: memberTasks.length, label: 'Tasks' },
                                    { value: done, label: 'Done' },
                                    { value: inProgress, label: 'Active' },
                                ].map(stat => (
                                    <div key={stat.label} className="text-center">
                                        <div className="text-lg font-bold">{stat.value}</div>
                                        <div className="text-xs text-muted-foreground">{stat.label}</div>
                                    </div>
                                ))}
                            </div>

                            <div className="text-xs text-muted-foreground mb-3">
                                Avg. work time: <span className="text-foreground font-medium">{formatDuration(avgWorkTime)}</span>
                            </div>

                            {memberTasks.length > 0 && (
                                <div className="space-y-1">
                                    {memberTasks.map(t => (
                                        <div key={t.id} className="flex items-center justify-between text-xs py-1">
                                            <span className="text-muted-foreground">#{t.idShort} {t.name}</span>
                                            <Badge
                                                variant="outline"
                                                className="text-[10px] px-1.5 py-0"
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
                            )}
                        </CardContent>
                    </Card>
                );
            })}
        </div>
    );
}
