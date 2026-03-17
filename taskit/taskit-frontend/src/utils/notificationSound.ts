type NotificationType =
    | 'task_assigned'
    | 'comment_added'
    | 'status_changed'
    | 'planning_complete'
    | 'question_asked'
    | 'spec_finished';

const SOUND_MAP: Record<NotificationType, string> = {
    question_asked: '/sounds/abstract-sound3.wav',
    spec_finished: '/sounds/abstract-sound3.wav',
    planning_complete: '/sounds/abstract-sound3.wav',
    task_assigned: '/sounds/abstract-sound3.wav',
    comment_added: '/sounds/abstract-sound3.wav',
    status_changed: '/sounds/abstract-sound3.wav',
};

function playFallbackBeep(): void {
    try {
        const ctx = new AudioContext();
        const oscillator = ctx.createOscillator();
        const gain = ctx.createGain();

        oscillator.frequency.value = 800;
        gain.gain.value = 0.1;

        oscillator.connect(gain);
        gain.connect(ctx.destination);

        oscillator.start();
        oscillator.stop(ctx.currentTime + 0.15);
    } catch {
        // No Web Audio support — silently ignore
    }
}

export function playNotificationSound(type: NotificationType): void {
    const path = SOUND_MAP[type];
    try {
        const audio = new Audio(path);
        audio.play().catch(() => playFallbackBeep());
    } catch {
        playFallbackBeep();
    }
}
