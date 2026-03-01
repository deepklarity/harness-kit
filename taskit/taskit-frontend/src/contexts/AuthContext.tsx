/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

interface AuthUser {
    id: string;
    email: string | null;
    displayName: string | null;
    isAdmin?: boolean;
    mustChangePassword?: boolean;
}

interface AuthContextValue {
    user: AuthUser | null;
    loading: boolean;
    authEnabled: boolean;
    login: (email: string, password: string) => Promise<void>;
    logout: () => Promise<void>;
    changePassword: (oldPassword: string, newPassword: string) => Promise<void>;
    getIdToken: () => Promise<string | null>;
}

interface LoginResponse {
    token?: string;
    expires_in?: number;
    user?: {
        id: number;
        name: string;
        email: string;
        is_admin: boolean;
        must_change_password?: boolean;
    };
}

const AuthContext = createContext<AuthContextValue | null>(null);

const AUTH_ENABLED = (import.meta.env.VITE_AUTH_ENABLED ?? import.meta.env.VITE_FIREBASE_AUTH_ENABLED) === 'true';
const API_BASE_URL = (import.meta.env.VITE_HARNESS_TIME_API_URL || 'http://localhost:8000').replace(/\/$/, '');

function mapUser(payload: LoginResponse['user']): AuthUser {
    return {
        id: String(payload?.id ?? ''),
        email: payload?.email ?? null,
        displayName: payload?.name ?? null,
        isAdmin: payload?.is_admin,
        mustChangePassword: payload?.must_change_password ?? false,
    };
}

async function parseApiError(res: Response): Promise<string> {
    try {
        const data = await res.json();
        if (typeof data?.detail === 'string' && data.detail.trim()) return data.detail;
    } catch {
        // ignore parse errors, fallback to status text
    }
    return res.statusText || 'Authentication failed';
}

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<AuthUser | null>(null);
    const [loading, setLoading] = useState(AUTH_ENABLED);
    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [tokenExpiresAtMs, setTokenExpiresAtMs] = useState<number>(0);

    const clearAuthState = useCallback(() => {
        setUser(null);
        setAccessToken(null);
        setTokenExpiresAtMs(0);
    }, []);

    const saveAccessToken = useCallback((token: string, expiresInSeconds: number) => {
        setAccessToken(token);
        setTokenExpiresAtMs(Date.now() + expiresInSeconds * 1000);
    }, []);

    const refreshAccessToken = useCallback(async (): Promise<string | null> => {
        if (!AUTH_ENABLED) return null;
        const res = await fetch(`${API_BASE_URL}/auth/refresh/`, {
            method: 'POST',
            credentials: 'include',
        });
        if (!res.ok) return null;
        const data = await res.json() as { token?: string; expires_in?: number };
        if (!data.token || !data.expires_in) return null;
        saveAccessToken(data.token, data.expires_in);
        return data.token;
    }, [saveAccessToken]);

    const getIdToken = useCallback(async (): Promise<string | null> => {
        if (!AUTH_ENABLED) return null;
        const stillValid = accessToken && (Date.now() < tokenExpiresAtMs - 30_000);
        if (stillValid) return accessToken;
        const refreshed = await refreshAccessToken();
        if (!refreshed) clearAuthState();
        return refreshed;
    }, [accessToken, tokenExpiresAtMs, refreshAccessToken, clearAuthState]);

    const login = useCallback(async (email: string, password: string) => {
        const res = await fetch(`${API_BASE_URL}/auth/login/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ email, password }),
        });
        if (!res.ok) throw new Error(await parseApiError(res));

        const data = await res.json() as LoginResponse;
        if (!data.token || !data.expires_in) {
            throw new Error('Auth server did not return an access token');
        }

        saveAccessToken(data.token, data.expires_in);
        if (data.user) {
            setUser(mapUser(data.user));
        } else {
            const meRes = await fetch(`${API_BASE_URL}/auth/me/`, {
                headers: { Authorization: `Bearer ${data.token}` },
                credentials: 'include',
            });
            if (meRes.ok) {
                const me = await meRes.json() as LoginResponse['user'];
                setUser(mapUser(me));
            }
        }
    }, [saveAccessToken]);

    const logout = useCallback(async () => {
        try {
            const token = await getIdToken();
            await fetch(`${API_BASE_URL}/auth/logout/`, {
                method: 'POST',
                headers: token ? { Authorization: `Bearer ${token}` } : undefined,
                credentials: 'include',
            });
        } finally {
            clearAuthState();
        }
    }, [clearAuthState, getIdToken]);

    const changePassword = useCallback(async (oldPassword: string, newPassword: string) => {
        const token = await getIdToken();
        if (!token) throw new Error('Not authenticated');
        const res = await fetch(`${API_BASE_URL}/auth/change-password/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
            credentials: 'include',
            body: JSON.stringify({
                old_password: oldPassword,
                new_password: newPassword,
            }),
        });
        if (!res.ok) throw new Error(await parseApiError(res));
        setUser(prev => prev ? { ...prev, mustChangePassword: false } : prev);
    }, [getIdToken]);

    useEffect(() => {
        if (!AUTH_ENABLED) {
            setLoading(false);
            return;
        }

        let cancelled = false;

        const bootstrap = async () => {
            try {
                const token = await refreshAccessToken();
                if (!token) {
                    if (!cancelled) clearAuthState();
                    return;
                }
                const meRes = await fetch(`${API_BASE_URL}/auth/me/`, {
                    headers: { Authorization: `Bearer ${token}` },
                    credentials: 'include',
                });
                if (!meRes.ok) {
                    if (!cancelled) clearAuthState();
                    return;
                }
                const me = await meRes.json() as LoginResponse['user'];
                if (!cancelled) setUser(mapUser(me));
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        void bootstrap();
        return () => {
            cancelled = true;
        };
    }, [refreshAccessToken, clearAuthState]);

    return (
        <AuthContext.Provider value={{ user, loading, authEnabled: AUTH_ENABLED, login, logout, changePassword, getIdToken }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth(): AuthContextValue {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error('useAuth must be used within AuthProvider');
    return ctx;
}
