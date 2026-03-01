const STORAGE_KEY = 'taskit:seenComments';

type SeenMap = Record<string, number>;

function getMap(): SeenMap {
    try {
        return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    } catch {
        return {};
    }
}

export function getSeenCommentCount(taskId: string): number {
    return getMap()[taskId] ?? 0;
}

export function markCommentsSeen(taskId: string, count: number): void {
    const map = getMap();
    map[taskId] = count;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
}
