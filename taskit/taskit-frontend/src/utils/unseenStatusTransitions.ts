import type { Task, TaskMutation } from '../types';

const SEEN_STORAGE_KEY = 'taskit:seenExecutionTransitions';
const PENDING_STORAGE_KEY = 'taskit:pendingExecutionTransitions';

type SeenTransitionMap = Record<string, number>;

function getMap(key: string): SeenTransitionMap {
    try {
        return JSON.parse(localStorage.getItem(key) || '{}');
    } catch {
        return {};
    }
}

function setMap(key: string, map: SeenTransitionMap): void {
    localStorage.setItem(key, JSON.stringify(map));
}

function normalizeStatus(status?: string): string {
    return (status || '').toUpperCase();
}

function isProgressStatus(status?: string): boolean {
    const normalized = normalizeStatus(status);
    return normalized === 'IN_PROGRESS' || normalized === 'EXECUTING';
}

export function didLeaveProgressStatus(fromStatus?: string, toStatus?: string): boolean {
    const from = normalizeStatus(fromStatus);
    const to = normalizeStatus(toStatus);
    return isProgressStatus(from) && !!to && !isProgressStatus(to);
}

function isProgressExitTransition(mutation: TaskMutation): boolean {
    if (mutation.type !== 'status_change') return false;
    return didLeaveProgressStatus(mutation.fromStatus, mutation.toStatus);
}

export function getSeenExecutionTransitionTimestamp(taskId: string): number {
    return getMap(SEEN_STORAGE_KEY)[taskId] ?? 0;
}

function getPendingExecutionTransitionTimestamp(taskId: string): number {
    return getMap(PENDING_STORAGE_KEY)[taskId] ?? 0;
}

export function markExecutionTransitionSeen(taskId: string, transitionTs: number): void {
    const seenMap = getMap(SEEN_STORAGE_KEY);
    seenMap[taskId] = transitionTs;
    setMap(SEEN_STORAGE_KEY, seenMap);

    const pendingMap = getMap(PENDING_STORAGE_KEY);
    if ((pendingMap[taskId] ?? 0) <= transitionTs) {
        delete pendingMap[taskId];
        setMap(PENDING_STORAGE_KEY, pendingMap);
    }
}

export function markExecutionTransitionUnseen(taskId: string, transitionTs: number): void {
    const seenTs = getSeenExecutionTransitionTimestamp(taskId);
    if (transitionTs <= seenTs) return;

    const pendingMap = getMap(PENDING_STORAGE_KEY);
    const existing = pendingMap[taskId] ?? 0;
    if (transitionTs > existing) {
        pendingMap[taskId] = transitionTs;
        setMap(PENDING_STORAGE_KEY, pendingMap);
    }
}

function getLatestProgressExitFromHistory(task: Task): TaskMutation | null {
    let latest: TaskMutation | null = null;
    for (const mutation of task.mutations || []) {
        if (!isProgressExitTransition(mutation)) continue;
        if (!latest || mutation.timestamp > latest.timestamp) latest = mutation;
    }
    return latest;
}

export function getLatestExecutionTransitionTimestamp(task: Task): number {
    const fromHistory = getLatestProgressExitFromHistory(task)?.timestamp ?? 0;
    const fromPending = getPendingExecutionTransitionTimestamp(task.id);
    return Math.max(fromHistory, fromPending);
}

export function hasUnseenExecutionCompletion(task: Task): boolean {
    const latestTs = getLatestExecutionTransitionTimestamp(task);
    if (!latestTs) return false;
    const seenTs = getSeenExecutionTransitionTimestamp(task.id);
    return latestTs > seenTs;
}
