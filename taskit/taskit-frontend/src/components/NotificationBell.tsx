import { useState } from "react";
import { Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import { useNotifications } from "@/contexts/NotificationContext";
import { NotificationDropdown } from "./NotificationDropdown";

export function NotificationBell() {
    const [open, setOpen] = useState(false);
    const { unreadCount, fetchNotifications } = useNotifications();

    const handleOpenChange = (next: boolean) => {
        setOpen(next);
        if (next) {
            void fetchNotifications();
        }
    };

    return (
        <Popover open={open} onOpenChange={handleOpenChange}>
            <PopoverTrigger asChild>
                <Button variant="ghost" size="sm" className="size-8 p-0 relative">
                    <Bell className="size-4" />
                    {unreadCount > 0 && (
                        <span className="absolute -top-1 -right-1 size-4 rounded-full bg-destructive text-destructive-foreground text-[10px] font-medium flex items-center justify-center">
                            {unreadCount > 9 ? "9+" : unreadCount}
                        </span>
                    )}
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-96 p-0">
                <NotificationDropdown onClose={() => setOpen(false)} />
            </PopoverContent>
        </Popover>
    );
}
