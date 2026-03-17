import { useState, useEffect } from "react";
import { Bell, BellOff, Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useNotifications } from "@/contexts/NotificationContext";

const NOTIFICATION_TYPE_LABELS: Record<string, string> = {
    task_assigned: "Task assigned",
    comment_added: "New comment",
    status_changed: "Status changed",
    planning_complete: "Planning complete",
    question_asked: "Agent question",
    spec_finished: "Spec finished",
};

const ALL_TYPES = Object.keys(NOTIFICATION_TYPE_LABELS);

export function NotificationSettings() {
    const {
        preferences,
        updatePreferences,
        desktopSupported,
        desktopEnabled,
        desktopPermission,
        enableDesktopNotifications,
        disableDesktopNotifications,
    } = useNotifications();

    const [desktopLoading, setDesktopLoading] = useState(false);
    const [desktopError, setDesktopError] = useState<string | null>(null);

    // Local controlled state, synced from preferences when they load.
    const [soundEnabled, setSoundEnabled] = useState(true);
    const [disabledTypes, setDisabledTypes] = useState<string[]>([]);
    const [quietStart, setQuietStart] = useState("");
    const [quietEnd, setQuietEnd] = useState("");

    useEffect(() => {
        if (!preferences) return;
        setSoundEnabled(preferences.sound_enabled);
        setDisabledTypes(preferences.disabled_types ?? []);
        setQuietStart(preferences.quiet_hours_start ?? "");
        setQuietEnd(preferences.quiet_hours_end ?? "");
    }, [preferences]);

    // ── Handlers ──────────────────────────────────────────────────────────

    const handleToggleSound = () => {
        const next = !soundEnabled;
        setSoundEnabled(next);
        void updatePreferences({ sound_enabled: next });
    };

    const handleToggleType = (type: string) => {
        const next = disabledTypes.includes(type)
            ? disabledTypes.filter((t) => t !== type)
            : [...disabledTypes, type];
        setDisabledTypes(next);
        void updatePreferences({ disabled_types: next });
    };

    const handleQuietStartChange = (value: string) => {
        setQuietStart(value);
        void updatePreferences({ quiet_hours_start: value || null });
    };

    const handleQuietEndChange = (value: string) => {
        setQuietEnd(value);
        void updatePreferences({ quiet_hours_end: value || null });
    };

    const handleToggleDesktop = async () => {
        setDesktopError(null);
        setDesktopLoading(true);
        try {
            if (desktopEnabled) {
                await disableDesktopNotifications();
            } else {
                await enableDesktopNotifications();
            }
        } catch (err) {
            setDesktopError(err instanceof Error ? err.message : "Failed to update desktop notifications");
        } finally {
            setDesktopLoading(false);
        }
    };

    // ── Render ─────────────────────────────────────────────────────────────

    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-sm font-medium text-muted-foreground mb-3">Notifications</h3>
                <div className="space-y-4">

                    {/* Desktop Notifications */}
                    <div className="border border-border rounded-lg p-4 space-y-3">
                        <div className="flex items-center justify-between">
                            <div>
                                <div className="text-sm font-medium">Desktop Notifications</div>
                                <div className="text-xs text-muted-foreground mt-0.5">
                                    Show browser notifications while TaskIt is open
                                </div>
                            </div>
                            {desktopSupported ? (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="gap-1.5 shrink-0"
                                    disabled={desktopLoading}
                                    onClick={() => void handleToggleDesktop()}
                                >
                                    {desktopEnabled ? (
                                        <>
                                            <BellOff className="size-3.5" />
                                            Disable
                                        </>
                                    ) : (
                                        <>
                                            <Bell className="size-3.5" />
                                            Enable
                                        </>
                                    )}
                                </Button>
                            ) : (
                                <span className="text-xs text-muted-foreground">
                                    Not supported in this browser
                                </span>
                            )}
                        </div>
                        {desktopSupported && desktopPermission !== 'granted' && (
                            <p className="text-xs text-muted-foreground">
                                Browser permission: {desktopPermission}
                            </p>
                        )}
                        {!desktopEnabled && preferences?.desktop_enabled && desktopPermission !== 'granted' && (
                            <p className="text-xs text-muted-foreground">
                                Desktop notifications stay off until the browser grants permission.
                            </p>
                        )}
                        {desktopError && (
                            <p className="text-xs text-destructive">{desktopError}</p>
                        )}
                    </div>

                    {/* Sound */}
                    <div className="border border-border rounded-lg p-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <div className="text-sm font-medium">Sound</div>
                                <div className="text-xs text-muted-foreground mt-0.5">
                                    Play a sound when new notifications arrive
                                </div>
                            </div>
                            <Button
                                variant="outline"
                                size="sm"
                                className="gap-1.5 shrink-0"
                                onClick={handleToggleSound}
                            >
                                {soundEnabled ? (
                                    <>
                                        <VolumeX className="size-3.5" />
                                        Mute
                                    </>
                                ) : (
                                    <>
                                        <Volume2 className="size-3.5" />
                                        Unmute
                                    </>
                                )}
                            </Button>
                        </div>
                    </div>

                    {/* Notification Types */}
                    <div className="border border-border rounded-lg p-4 space-y-3">
                        <div>
                            <div className="text-sm font-medium">Notification Types</div>
                            <div className="text-xs text-muted-foreground mt-0.5">
                                Choose which events trigger notifications
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                            {ALL_TYPES.map((type) => {
                                const isEnabled = !disabledTypes.includes(type);
                                return (
                                    <label
                                        key={type}
                                        className="flex items-center gap-2 cursor-pointer select-none"
                                    >
                                        <input
                                            type="checkbox"
                                            checked={isEnabled}
                                            onChange={() => handleToggleType(type)}
                                            className="size-3.5 rounded accent-primary cursor-pointer"
                                        />
                                        <span className="text-xs">
                                            {NOTIFICATION_TYPE_LABELS[type]}
                                        </span>
                                    </label>
                                );
                            })}
                        </div>
                    </div>

                    {/* Quiet Hours */}
                    <div className="border border-border rounded-lg p-4 space-y-3">
                        <div>
                            <div className="text-sm font-medium">Quiet Hours</div>
                            <div className="text-xs text-muted-foreground mt-0.5">
                                Suppress notifications during these hours
                            </div>
                        </div>
                        <div className="flex items-center gap-3">
                            <div className="flex-1 space-y-1">
                                <label className="text-xs text-muted-foreground" htmlFor="quiet-start">
                                    Start
                                </label>
                                <input
                                    id="quiet-start"
                                    type="time"
                                    value={quietStart}
                                    onChange={(e) => handleQuietStartChange(e.target.value)}
                                    className="w-full h-8 rounded-md border border-input bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                                />
                            </div>
                            <div className="flex-1 space-y-1">
                                <label className="text-xs text-muted-foreground" htmlFor="quiet-end">
                                    End
                                </label>
                                <input
                                    id="quiet-end"
                                    type="time"
                                    value={quietEnd}
                                    onChange={(e) => handleQuietEndChange(e.target.value)}
                                    className="w-full h-8 rounded-md border border-input bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                                />
                            </div>
                        </div>
                        {(quietStart || quietEnd) && (
                            <p className="text-[10px] text-muted-foreground">
                                Notifications are suppressed from{" "}
                                <span className="font-mono">{quietStart || "—"}</span> to{" "}
                                <span className="font-mono">{quietEnd || "—"}</span>. Leave both blank to disable quiet hours.
                            </p>
                        )}
                    </div>

                </div>
            </div>
        </div>
    );
}
