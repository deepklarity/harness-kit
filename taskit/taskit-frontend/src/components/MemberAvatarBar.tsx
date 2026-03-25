import type { Member } from '../types';
import { AvatarGroup, AvatarGroupCount } from '@/components/ui/avatar';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { X, Bot } from 'lucide-react';
import { UserAvatar } from './UserAvatar';

interface MemberAvatarBarProps {
    members: Member[];
    filteredMemberId: string | null;
    onSelect: (id: string | null) => void;
}

const MAX_VISIBLE = 5;

export function MemberAvatarBar({ members, filteredMemberId, onSelect }: MemberAvatarBarProps) {
    if (members.length === 0) return null;

    const visible = members.slice(0, MAX_VISIBLE);
    const overflow = members.slice(MAX_VISIBLE);

    const handleClick = (id: string) => {
        onSelect(filteredMemberId === id ? null : id);
    };

    return (
        <div className="flex items-center gap-2">
            <AvatarGroup>
                {visible.map(member => (
                    <MemberAvatar
                        key={member.id}
                        member={member}
                        isSelected={filteredMemberId === member.id}
                        onClick={() => handleClick(member.id)}
                    />
                ))}
                {overflow.length > 0 && (
                    <Popover>
                        <PopoverTrigger asChild>
                            <button type="button" className="cursor-pointer">
                                <AvatarGroupCount className="text-xs cursor-pointer hover:ring-primary/50 hover:ring-2 transition-all">
                                    +{overflow.length}
                                </AvatarGroupCount>
                            </button>
                        </PopoverTrigger>
                        <PopoverContent className="w-56 p-2" align="start">
                            <div className="space-y-1">
                                {overflow.map(member => {
                                    const isAgent = member.role === 'AGENT' || member.email.endsWith('@odin.agent');
                                    return (
                                        <button
                                            key={member.id}
                                            type="button"
                                            className={`flex items-center gap-2 w-full px-2 py-1.5 rounded-md text-sm transition-colors hover:bg-muted ${
                                                filteredMemberId === member.id ? 'bg-primary/10 text-primary' : ''
                                            }`}
                                            onClick={() => handleClick(member.id)}
                                        >
                                            <div className="relative shrink-0">
                                                <div
                                                    className="size-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white"
                                                    style={{ background: member.color }}
                                                >
                                                    {member.initials}
                                                </div>
                                                {isAgent && (
                                                    <div className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full bg-indigo-600 border border-background flex items-center justify-center">
                                                        <Bot className="size-1 text-white" />
                                                    </div>
                                                )}
                                            </div>
                                            <span className="truncate flex-1 text-left">{member.fullName}</span>
                                            {isAgent && (
                                                <span className="text-[10px] bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-400 px-1 rounded-sm">Agent</span>
                                            )}
                                        </button>
                                    );
                                })}
                            </div>
                        </PopoverContent>
                    </Popover>
                )}
            </AvatarGroup>

            {filteredMemberId && (
                <button
                    type="button"
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                    onClick={() => onSelect(null)}
                >
                    <X className="size-3" />
                    clear
                </button>
            )}
        </div>
    );
}

function MemberAvatar({ member, isSelected, onClick }: {
    member: Member;
    isSelected: boolean;
    onClick: () => void;
}) {
    const isAgent = member.role === 'AGENT' || member.email.endsWith('@odin.agent');

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <div>
                    <UserAvatar
                        member={member}
                        size="sm"
                        isSelected={isSelected}
                        onClick={onClick}
                    />
                </div>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">
                {member.fullName} {isAgent ? '(Agent)' : ''}
            </TooltipContent>
        </Tooltip>
    );
}
