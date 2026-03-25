import * as React from 'react';
import type { Member } from '../types';
import { Avatar, AvatarFallback, AvatarBadge } from '@/components/ui/avatar';
import { Bot } from 'lucide-react';
import { cn } from '@/lib/utils';

interface UserAvatarProps {
    member: Member;
    size?: "sm" | "default" | "lg";
    className?: string;
    isSelected?: boolean;
    onClick?: () => void;
}

export function UserAvatar({ member, size = "default", className, isSelected, onClick }: UserAvatarProps) {
    const isAgent = member.role === 'AGENT' || member.email.endsWith('@odin.agent');

    const handleClick = (e: React.MouseEvent) => {
        if (onClick) {
            e.preventDefault();
            e.stopPropagation();
            onClick();
        }
    };

    const avatarContent = (
        <Avatar
            size={size}
            className={cn(
                "transition-all cursor-pointer",
                isSelected && "ring-2 ring-primary ring-offset-1 ring-offset-background",
                !isSelected && !onClick && "cursor-default",
                !isSelected && onClick && "opacity-80 hover:opacity-100",
                className
            )}
            onClick={onClick ? handleClick : undefined}
        >
            <AvatarFallback
                className="text-[10px] font-bold text-white font-mono"
                style={{ background: member.color }}
            >
                {member.initials}
            </AvatarFallback>
            {isAgent && (
                <AvatarBadge className="bg-indigo-600 text-white flex items-center justify-center border border-background">
                    <Bot className="size-2" />
                </AvatarBadge>
            )}
        </Avatar>
    );

    return avatarContent;
}
