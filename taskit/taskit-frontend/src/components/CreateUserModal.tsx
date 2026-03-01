import { useState } from 'react';
import { useToast } from '@/hooks/use-toast';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { MEMBER_COLORS } from '@/services/harness/HarnessTimeService';
import { cn } from '@/lib/utils';
import { Check } from 'lucide-react';

interface CreateUserModalProps {
    onClose: () => void;
    onCreate: (name: string, email: string, color: string) => Promise<void>;
}

export function CreateUserModal({ onClose, onCreate }: CreateUserModalProps) {
    const { toast } = useToast();
    const [name, setName] = useState('');
    const [email, setEmail] = useState('');
    const [color, setColor] = useState(MEMBER_COLORS[0]);
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!name.trim() || !email.trim()) return;
        setLoading(true);
        try {
            await onCreate(name, email, color);
            onClose();
        } catch {
            toast({
                title: "Error",
                description: "Failed to create user. Email may already exist.",
                variant: "destructive"
            });
        } finally {
            setLoading(false);
        }
    };

    return (
        <Dialog open onOpenChange={onClose}>
            <DialogContent className="sm:max-w-[400px]">
                <DialogHeader>
                    <DialogTitle>Create New User</DialogTitle>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                    <div className="flex flex-col gap-2">
                        <Label>Name</Label>
                        <Input
                            autoFocus
                            required
                            value={name}
                            onChange={e => setName(e.target.value)}
                            placeholder="e.g. Alice Smith"
                        />
                    </div>
                    <div className="flex flex-col gap-2">
                        <Label>Email</Label>
                        <Input
                            required
                            type="email"
                            value={email}
                            onChange={e => setEmail(e.target.value)}
                            placeholder="e.g. alice@example.com"
                        />
                    </div>
                    <div className="flex flex-col gap-2">
                        <Label>Assigned Color</Label>
                        <div className="flex flex-wrap gap-2">
                            {MEMBER_COLORS.map((c) => (
                                <button
                                    key={c}
                                    type="button"
                                    onClick={() => setColor(c)}
                                    className={cn(
                                        "w-8 h-8 rounded-full border-2 transition-all flex items-center justify-center",
                                        color === c ? "border-foreground scale-110" : "border-transparent hover:opacity-80"
                                    )}
                                    style={{ backgroundColor: c }}
                                >
                                    {color === c && <Check className="w-4 h-4 text-white drop-shadow-md" />}
                                </button>
                            ))}
                        </div>
                    </div>
                    <div className="flex gap-3 justify-end mt-2">
                        <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
                        <Button type="submit" disabled={loading || !name.trim() || !email.trim()}>
                            {loading ? 'Creating...' : 'Create User'}
                        </Button>
                    </div>
                </form>
            </DialogContent>
        </Dialog>
    );
}
