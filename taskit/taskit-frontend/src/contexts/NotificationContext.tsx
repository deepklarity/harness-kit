/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Notification, NotificationPreference } from '../types';
import { useService } from './ServiceContext';
import { usePolling } from '../hooks/usePolling';
import { playNotificationSound } from '../utils/notificationSound';
import { toast } from '../hooks/use-toast';

interface NotificationContextValue {
    notifications: Notification[];
    unreadCount: number;
    preferences: NotificationPreference | null;
    desktopSupported: boolean;
    desktopEnabled: boolean;
    desktopPermission: NotificationPermission | 'unsupported';
    loading: boolean;
    fetchNotifications: () => Promise<void>;
    markAsRead: (id: number) => Promise<void>;
    markAllAsRead: () => Promise<void>;
    deleteNotification: (id: number) => Promise<void>;
    updatePreferences: (prefs: Partial<NotificationPreference>) => Promise<void>;
    enableDesktopNotifications: () => Promise<void>;
    disableDesktopNotifications: () => Promise<void>;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

const desktopSupported =
    typeof window !== 'undefined' &&
    'Notification' in window;

export function NotificationProvider({ children }: { children: ReactNode }) {
    const service = useService();
    const navigate = useNavigate();

    const [notifications, setNotifications] = useState<Notification[]>([]);
    const [unreadCount, setUnreadCount] = useState(0);
    const [preferences, setPreferences] = useState<NotificationPreference | null>(null);
    const [desktopPermission, setDesktopPermission] = useState<NotificationPermission | 'unsupported'>(
        desktopSupported ? Notification.permission : 'unsupported',
    );
    const [loading, setLoading] = useState(true);

    const knownNotificationIdsRef = useRef<Set<number>>(new Set());
    const latestCreatedAtRef = useRef<string | null>(null);

    const trackKnownNotifications = useCallback((items: Notification[]) => {
        if (items.length === 0) return;

        for (const item of items) {
            knownNotificationIdsRef.current.add(item.id);
            if (!latestCreatedAtRef.current || item.created_at > latestCreatedAtRef.current) {
                latestCreatedAtRef.current = item.created_at;
            }
        }
    }, []);

    const mergeNotifications = useCallback((incoming: Notification[]) => {
        setNotifications(prev => {
            const byId = new Map(prev.map(item => [item.id, item]));
            for (const item of incoming) {
                byId.set(item.id, item);
            }
            return Array.from(byId.values()).sort((a, b) => (
                new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
            ));
        });
    }, []);

    const syncDesktopPermission = useCallback(() => {
        setDesktopPermission(desktopSupported ? Notification.permission : 'unsupported');
    }, []);

    const openNotificationTarget = useCallback((notification: Notification) => {
        if (notification.task) {
            navigate(`/board?taskId=${notification.task}`);
            return;
        }
        if (notification.spec) {
            navigate(`/specs?specId=${notification.spec}`);
        }
    }, [navigate]);

    useEffect(() => {
        let cancelled = false;

        const bootstrap = async () => {
            try {
                const [prefs, unread, initialNotifications] = await Promise.all([
                    service.fetchNotificationPreferences(),
                    service.fetchUnreadCount(),
                    service.fetchNotifications(),
                ]);

                if (cancelled) return;

                setPreferences(prefs);
                setUnreadCount(unread.count);
                setNotifications(initialNotifications.results);
                trackKnownNotifications(initialNotifications.results);
            } catch {
                // Non-fatal: preferences may be unavailable (e.g., unauthenticated).
            }

            if (!cancelled) setLoading(false);
        };

        void bootstrap();
        return () => {
            cancelled = true;
        };
    }, [service, trackKnownNotifications]);

    useEffect(() => {
        if (!desktopSupported) return undefined;

        const handleVisibilityChange = () => {
            if (!document.hidden) {
                syncDesktopPermission();
            }
        };

        document.addEventListener('visibilitychange', handleVisibilityChange);
        return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
    }, [syncDesktopPermission]);

    const handleIncomingNotifications = useCallback((incoming: Notification[]) => {
        if (incoming.length === 0) return;

        incoming
            .slice()
            .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
            .forEach(notification => {
                toast({
                    title: notification.title,
                    description: notification.body || 'Open the notification center for details.',
                });

                if (
                    preferences?.desktop_enabled &&
                    desktopSupported &&
                    Notification.permission === 'granted'
                ) {
                    const popup = new Notification(notification.title, {
                        body: notification.body || 'Open TaskIt to view this notification.',
                        icon: '/favicon.ico',
                    });
                    popup.onclick = () => {
                        window.focus();
                        openNotificationTarget(notification);
                        popup.close();
                    };
                }
            });

        if (preferences?.sound_enabled) {
            playNotificationSound(incoming[0].notification_type);
        }
    }, [openNotificationTarget, preferences]);

    const pollNotifications = useCallback(async () => {
        const [unread, latest] = await Promise.all([
            service.fetchUnreadCount(),
            service.fetchNotifications(
                latestCreatedAtRef.current
                    ? { since: latestCreatedAtRef.current }
                    : undefined,
            ),
        ]);

        setUnreadCount(unread.count);

        const fresh = latest.results.filter(item => !knownNotificationIdsRef.current.has(item.id));
        if (fresh.length > 0) {
            mergeNotifications(fresh);
            trackKnownNotifications(fresh);
            handleIncomingNotifications(fresh);
            return;
        }

        trackKnownNotifications(latest.results);
    }, [handleIncomingNotifications, mergeNotifications, service, trackKnownNotifications]);

    usePolling(pollNotifications, {
        enabled: !loading,
        intervalMs: 10_000,
        immediate: true,
    });

    const fetchNotifications = useCallback(async () => {
        const { results } = await service.fetchNotifications();
        setNotifications(results);
        trackKnownNotifications(results);
    }, [service, trackKnownNotifications]);

    const markAsRead = useCallback(async (id: number) => {
        const updated = await service.markNotificationRead(id);
        setNotifications(prev => {
            const target = prev.find(n => n.id === id);
            if (target && !target.is_read) {
                setUnreadCount(count => Math.max(0, count - 1));
            }
            return prev.map(n => (n.id === id ? updated : n));
        });
    }, [service]);

    const markAllAsRead = useCallback(async () => {
        await service.markAllNotificationsRead();
        setNotifications(prev => prev.map(n => ({ ...n, is_read: true })));
        setUnreadCount(0);
    }, [service]);

    const deleteNotification = useCallback(async (id: number) => {
        await service.deleteNotification(id);
        setNotifications(prev => {
            const target = prev.find(n => n.id === id);
            const wasUnread = target && !target.is_read;
            const next = prev.filter(n => n.id !== id);
            if (wasUnread) setUnreadCount(c => Math.max(0, c - 1));
            return next;
        });
    }, [service]);

    const updatePreferences = useCallback(
        async (prefs: Partial<NotificationPreference>) => {
            const updated = await service.updateNotificationPreferences(prefs);
            setPreferences(updated);
        },
        [service],
    );

    const enableDesktopNotifications = useCallback(async () => {
        if (!desktopSupported) {
            throw new Error('Desktop notifications are not supported in this browser');
        }

        const permission = Notification.permission === 'granted'
            ? 'granted'
            : await Notification.requestPermission();

        setDesktopPermission(permission);

        if (permission !== 'granted') {
            await updatePreferences({ desktop_enabled: false });
            throw new Error(
                permission === 'denied'
                    ? 'Notification permission was denied in the browser'
                    : 'Notification permission was dismissed',
            );
        }

        await updatePreferences({ desktop_enabled: true });
    }, [updatePreferences]);

    const disableDesktopNotifications = useCallback(async () => {
        await updatePreferences({ desktop_enabled: false });
        syncDesktopPermission();
    }, [syncDesktopPermission, updatePreferences]);

    const desktopEnabled = Boolean(
        preferences?.desktop_enabled &&
        desktopSupported &&
        desktopPermission === 'granted',
    );

    return (
        <NotificationContext.Provider
            value={{
                notifications,
                unreadCount,
                preferences,
                desktopSupported,
                desktopEnabled,
                desktopPermission,
                loading,
                fetchNotifications,
                markAsRead,
                markAllAsRead,
                deleteNotification,
                updatePreferences,
                enableDesktopNotifications,
                disableDesktopNotifications,
            }}
        >
            {children}
        </NotificationContext.Provider>
    );
}

export function useNotifications(): NotificationContextValue {
    const ctx = useContext(NotificationContext);
    if (!ctx) throw new Error('useNotifications must be used within NotificationProvider');
    return ctx;
}
