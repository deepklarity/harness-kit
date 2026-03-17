import { useNavigate } from "react-router-dom";
import { formatDistanceToNow, format } from "date-fns";
import { Settings, Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useNotifications } from "@/contexts/NotificationContext";
import { TYPE_ICONS, TYPE_META, DEFAULT_ICON } from "@/components/notificationHelpers";
import type { Notification, NotificationType } from "@/types";

interface Props {
    onClose: () => void;
}

export function NotificationDropdown({ onClose }: Props) {
    const navigate = useNavigate();
    const {
        notifications,
        loading,
        markAsRead,
        markAllAsRead,
        unreadCount,
    } = useNotifications();

    const handleClick = (notification: Notification) => {
        void markAsRead(notification.id);
        if (notification.task) {
            navigate(`/board?taskId=${notification.task}`);
        } else if (notification.spec) {
            navigate(`/specs?specId=${notification.spec}`);
        }
        onClose();
    };

    const handleMarkAllRead = () => {
        void markAllAsRead();
    };

    const handleSettingsClick = () => {
        navigate("/settings#notifications");
        onClose();
    };

    const displayed = notifications.slice(0, 5);

    return (
        <TooltipProvider delayDuration={300}>
            <div className="flex flex-col max-h-[480px]">
                {/* Header */}
                <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-border">
                    <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">Notifications</span>
                        {unreadCount > 0 && (
                            <Badge variant="default" className="h-5 px-1.5 text-[10px] font-semibold">
                                {unreadCount}
                            </Badge>
                        )}
                    </div>
                    <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs text-muted-foreground"
                        disabled={unreadCount === 0}
                        onClick={handleMarkAllRead}
                    >
                        Mark all read
                    </Button>
                </div>

                {/* Body */}
                <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain">
                    {loading && notifications.length === 0 ? (
                        <div className="flex items-center justify-center py-10 text-sm text-muted-foreground">
                            Loading…
                        </div>
                    ) : notifications.length === 0 ? (
                        <div className="flex flex-col items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
                            <Bell className="size-8 opacity-20" />
                            <span>No notifications yet</span>
                        </div>
                    ) : (
                        <div className="divide-y divide-border">
                            {displayed.map((notification) => {
                                const Icon = TYPE_ICONS[notification.notification_type] ?? DEFAULT_ICON;
                                const isUnread = !notification.is_read;
                                const meta = TYPE_META[notification.notification_type as NotificationType];

                                return (
                                    <button
                                        key={notification.id}
                                        type="button"
                                        className={cn(
                                            "group w-full text-left flex items-start gap-3 px-4 py-3 transition-colors cursor-pointer border-l-2",
                                            isUnread
                                                ? "border-primary bg-accent/50 hover:bg-accent/70"
                                                : "border-transparent hover:bg-accent/30",
                                        )}
                                        onClick={() => handleClick(notification)}
                                    >
                                        <Icon className="size-4 mt-0.5 shrink-0 text-muted-foreground" />
                                        <div className="flex-1 min-w-0 space-y-0.5">
                                            <div className="flex items-baseline justify-between gap-2">
                                                <span className="text-sm font-medium truncate">
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
                                                <p className="text-xs text-muted-foreground line-clamp-1">
                                                    {notification.body}
                                                </p>
                                            )}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="shrink-0 border-t border-border px-4 py-2.5 flex items-center justify-between">
                    <button
                        type="button"
                        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                        onClick={handleSettingsClick}
                    >
                        <Settings className="size-3" />
                        Notification Settings
                    </button>
                    {notifications.length > 0 && (
                        <div className="flex items-center gap-2">
                            <Separator orientation="vertical" className="h-4" />
                            <button
                                type="button"
                                className="text-xs text-primary hover:text-primary/80 font-medium transition-colors"
                                onClick={() => {
                                    navigate('/notifications');
                                    onClose();
                                }}
                            >
                                View all
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </TooltipProvider>
    );
}
