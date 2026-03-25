import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { formatDistanceToNow, format } from 'date-fns';
import { Bell, Trash2, Check, CheckCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { useNotifications } from '@/contexts/NotificationContext';
import { TYPE_ICONS, TYPE_META, DEFAULT_ICON } from '@/components/notificationHelpers';
import type { Notification, NotificationType } from '@/types';

const PAGE_SIZE = 20;

type Filter = 'all' | 'unread';

export function NotificationsPage() {
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const {
        notifications,
        loading,
        fetchNotifications,
        markAsRead,
        markAllAsRead,
        deleteNotification,
        unreadCount,
    } = useNotifications();

    const filter: Filter = searchParams.get('filter') === 'unread' ? 'unread' : 'all';
    const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

    const setFilter = (f: Filter) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (f === 'all') next.delete('filter');
            else next.set('filter', f);
            return next;
        }, { replace: true });
        setVisibleCount(PAGE_SIZE);
    };

    useEffect(() => {
        void fetchNotifications();
    }, [fetchNotifications]);

    const filtered = filter === 'unread'
        ? notifications.filter(n => !n.is_read)
        : notifications;

    const displayed = filtered.slice(0, visibleCount);
    const remaining = filtered.length - displayed.length;

    const handleClick = (notification: Notification) => {
        void markAsRead(notification.id);
        const targetBoard = notification.board ? String(notification.board) : searchParams.get('board');
        if (notification.task) {
            navigate(targetBoard ? `/board?taskId=${notification.task}&board=${targetBoard}` : `/board?taskId=${notification.task}`);
        } else if (notification.spec) {
            navigate(targetBoard ? `/specs?specId=${notification.spec}&board=${targetBoard}` : `/specs?specId=${notification.spec}`);
        }
    };

    return (
        <TooltipProvider delayDuration={300}>
            <div className="max-w-3xl mx-auto">
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-2">
                        <h2 className="text-lg font-semibold tracking-tight">Notifications</h2>
                        {unreadCount > 0 && (
                            <Badge variant="secondary" className="text-xs">
                                {unreadCount} unread
                            </Badge>
                        )}
                    </div>
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={unreadCount === 0}
                        onClick={() => void markAllAsRead()}
                    >
                        <CheckCheck className="size-3.5 mr-1.5" />
                        Mark all as read
                    </Button>
                </div>

                {/* Filter tabs */}
                <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)} className="mb-4">
                    <TabsList>
                        <TabsTrigger value="all">
                            All
                        </TabsTrigger>
                        <TabsTrigger value="unread">
                            Unread
                            {unreadCount > 0 && (
                                <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-[10px]">
                                    {unreadCount}
                                </Badge>
                            )}
                        </TabsTrigger>
                    </TabsList>
                </Tabs>

                {/* List */}
                {loading && notifications.length === 0 ? (
                    <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
                        Loading...
                    </div>
                ) : displayed.length === 0 ? (
                    <div className="flex flex-col items-center justify-center gap-1 py-16 text-muted-foreground">
                        <Bell className="size-10 opacity-20 mb-2" />
                        <span className="text-sm font-medium">
                            {filter === 'unread' ? 'All caught up' : 'No notifications yet'}
                        </span>
                        <span className="text-xs text-muted-foreground/70">
                            {filter === 'unread'
                                ? 'You have no unread notifications right now.'
                                : 'Notifications about tasks, comments, and specs will show up here.'}
                        </span>
                    </div>
                ) : (
                    <>
                        <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                            {displayed.map(notification => {
                                const Icon = TYPE_ICONS[notification.notification_type] ?? DEFAULT_ICON;
                                const isUnread = !notification.is_read;
                                const meta = TYPE_META[notification.notification_type as NotificationType];

                                return (
                                    <div
                                        key={notification.id}
                                        className={cn(
                                            "group relative flex items-start gap-3 px-4 py-3 transition-colors",
                                            isUnread && "bg-accent/50",
                                        )}
                                    >
                                        {isUnread && (
                                            <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-primary" />
                                        )}
                                        <button
                                            type="button"
                                            className="flex-1 flex items-start gap-3 text-left cursor-pointer hover:opacity-80"
                                            onClick={() => handleClick(notification)}
                                        >
                                            <Icon className="size-4 mt-0.5 shrink-0 text-muted-foreground" />
                                            <div className="flex-1 min-w-0 space-y-0.5">
                                                <div className="flex items-baseline justify-between gap-2">
                                                    <span className={cn("text-sm", isUnread && "font-medium")}>
                                                        {notification.title}
                                                    </span>
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                                                            <span className="text-[10px] text-muted-foreground shrink-0">
                                                                {formatDistanceToNow(new Date(notification.created_at), {
                                                                    addSuffix: true,
                                                                })}
                                                            </span>
                                                        </TooltipTrigger>
                                                        <TooltipContent side="left" className="text-xs">
                                                            {format(new Date(notification.created_at), "MMM d, yyyy h:mm a")}
                                                        </TooltipContent>
                                                    </Tooltip>
                                                </div>
                                                <div className="flex items-center gap-1.5">
                                                    {(notification.task || notification.spec) && (
                                                        <span className="text-[10px] font-mono text-muted-foreground/70">
                                                            {notification.task ? `T-${notification.task}` : `S-${notification.spec}`}
                                                        </span>
                                                    )}
                                                    {meta && (
                                                        <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full font-medium", meta.color)}>
                                                            {meta.label}
                                                        </span>
                                                    )}
                                                </div>
                                                {notification.body && (
                                                    <p className="text-xs text-muted-foreground line-clamp-2">
                                                        {notification.body}
                                                    </p>
                                                )}
                                            </div>
                                        </button>
                                        <div className="shrink-0 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                                            {isUnread && (
                                                <button
                                                    type="button"
                                                    className="p-1 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
                                                    onClick={() => void markAsRead(notification.id)}
                                                    title="Mark as read"
                                                >
                                                    <Check className="size-3.5" />
                                                </button>
                                            )}
                                            <button
                                                type="button"
                                                className="p-1 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                                                onClick={() => void deleteNotification(notification.id)}
                                                title="Delete notification"
                                            >
                                                <Trash2 className="size-3.5" />
                                            </button>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>

                        {remaining > 0 && (
                            <div className="flex justify-center mt-4">
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => setVisibleCount(prev => prev + PAGE_SIZE)}
                                >
                                    Show more ({remaining} remaining)
                                </Button>
                            </div>
                        )}
                    </>
                )}
            </div>
        </TooltipProvider>
    );
}
