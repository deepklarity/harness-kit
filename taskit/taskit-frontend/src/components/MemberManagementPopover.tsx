import { useState } from 'react';
import type { Member } from '../types';
import { CreateUserModal } from './CreateUserModal';
import { EditUserModal } from './EditUserModal';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Separator } from '@/components/ui/separator';
import { useToast } from '@/hooks/use-toast';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Users, Trash2, Pencil, UserPlus, Bot, ChevronDown } from 'lucide-react';

interface MemberManagementPopoverProps {
    members: Member[];
    onCreateUser: (name: string, email: string, color: string) => Promise<void>;
    onUpdateUser: (id: string, name: string, email: string, color: string) => Promise<void>;
    onDeleteUser: (id: string) => Promise<void>;
}

export function MemberManagementPopover({
    members,
    onCreateUser,
    onUpdateUser,
    onDeleteUser,
}: MemberManagementPopoverProps) {
    const { toast } = useToast();
    const [open, setOpen] = useState(false);
    const [showCreateUser, setShowCreateUser] = useState(false);
    const [editingUser, setEditingUser] = useState<Member | null>(null);
    const [memberToDelete, setMemberToDelete] = useState<Member | null>(null);
    const [deleting, setDeleting] = useState(false);

    const isAgentUser = (member: Member) => member.email.endsWith('@odin.agent');

    const handleDeleteMember = async () => {
        if (!memberToDelete) return;
        setDeleting(true);
        try {
            await onDeleteUser(memberToDelete.id);
            toast({
                title: 'Member removed',
                description: `"${memberToDelete.fullName}" has been removed.`,
            });
        } catch (e) {
            toast({
                title: 'Failed to remove member',
                description: e instanceof Error ? e.message : 'Unknown error',
                variant: 'destructive',
            });
        } finally {
            setDeleting(false);
            setMemberToDelete(null);
        }
    };

    return (
        <>
            <Popover open={open} onOpenChange={setOpen}>
                <PopoverTrigger asChild>
                    <Button
                        variant="outline"
                        size="sm"
                        className="gap-1.5 h-8 px-2.5"
                    >
                        <Users className="size-3.5" />
                        <span className="text-xs tabular-nums">{members.length}</span>
                        <ChevronDown className={`size-3 text-muted-foreground transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
                    </Button>
                </PopoverTrigger>
                <PopoverContent
                    side="bottom"
                    align="end"
                    sideOffset={8}
                    className="w-72 p-0 overflow-hidden"
                >
                    <div className="flex items-center justify-between px-3 py-2.5">
                        <span className="text-xs font-semibold text-foreground tracking-wide uppercase">Members</span>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 gap-1 text-[11px] px-2 text-muted-foreground hover:text-foreground"
                            onClick={() => { setShowCreateUser(true); setOpen(false); }}
                        >
                            <UserPlus className="size-3" />
                            Add
                        </Button>
                    </div>
                    <Separator />
                    <div className="max-h-[320px] overflow-y-auto overscroll-contain">
                        {members.length === 0 ? (
                            <div className="px-3 py-6 text-center">
                                <Users className="size-6 mx-auto mb-2 text-muted-foreground/40" />
                                <p className="text-xs text-muted-foreground">No members yet</p>
                            </div>
                        ) : (
                            <div className="py-1">
                                {members.map(member => (
                                    <div
                                        key={member.id}
                                        className="group flex items-center gap-2.5 px-3 py-2 hover:bg-muted/50 transition-colors"
                                    >
                                        <div
                                            className="size-6 rounded-full flex items-center justify-center text-[9px] font-semibold text-white shrink-0"
                                            style={{ backgroundColor: member.color }}
                                        >
                                            {member.initials}
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-1">
                                                <span className="text-xs font-medium truncate">{member.fullName}</span>
                                                {isAgentUser(member) && (
                                                    <Badge variant="outline" className="text-[8px] leading-none px-1 py-px gap-px text-blue-600 border-blue-200 dark:text-blue-400 dark:border-blue-700 shrink-0">
                                                        <Bot className="size-2" />
                                                        Agent
                                                    </Badge>
                                                )}
                                            </div>
                                            <p className="text-[10px] text-muted-foreground truncate">
                                                {member.email} &middot; {member.taskCount} task{member.taskCount !== 1 ? 's' : ''}
                                            </p>
                                        </div>
                                        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                                            <button
                                                type="button"
                                                className="size-6 inline-flex items-center justify-center rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                                                onClick={() => { setEditingUser(member); setOpen(false); }}
                                            >
                                                <Pencil className="size-2.5" />
                                            </button>
                                            <button
                                                type="button"
                                                className="size-6 inline-flex items-center justify-center rounded-md hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors"
                                                onClick={() => { setMemberToDelete(member); setOpen(false); }}
                                            >
                                                <Trash2 className="size-2.5" />
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </PopoverContent>
            </Popover>

            <AlertDialog open={!!memberToDelete} onOpenChange={(open) => !open && setMemberToDelete(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Remove member</AlertDialogTitle>
                        <AlertDialogDescription>
                            This will remove <strong>{memberToDelete?.fullName}</strong> ({memberToDelete?.email}).
                            {memberToDelete && isAgentUser(memberToDelete) && (
                                <> This is an Odin agent user and will be recreated automatically if Odin runs again.</>
                            )}
                            {' '}Tasks currently assigned to this member will become unassigned.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={handleDeleteMember}
                            disabled={deleting}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {deleting ? 'Removing...' : 'Remove member'}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            {showCreateUser && (
                <CreateUserModal
                    onClose={() => setShowCreateUser(false)}
                    onCreate={onCreateUser}
                />
            )}

            {editingUser && (
                <EditUserModal
                    user={editingUser}
                    onClose={() => setEditingUser(null)}
                    onUpdate={onUpdateUser}
                />
            )}
        </>
    );
}
