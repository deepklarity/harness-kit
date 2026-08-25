import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

interface AvatarSelectionProps {
    selected: string;
    onSelect: (avatar: string) => void;
}

const avatars = ['human', 'dog', 'panda', 'lion', 'robot'];

export const AvatarSelection: React.FC<AvatarSelectionProps> = ({ selected, onSelect }) => (
    <div className="flex gap-2">
        {avatars.map(avatar => (
            <button
                key={avatar}
                className={`size-10 rounded-full border-2 ${selected === avatar ? 'border-blue-500' : 'border-transparent'}`}
                onClick={() => onSelect(avatar)}
            >
                {avatar[0].toUpperCase()}
            </button>
        ))}
    </div>
);

export const PlayerSetupCard: React.FC<{ player: number }> = ({ player }) => {
    const [name, setName] = React.useState(`Artist ${player}`);
    const [avatar, setAvatar] = React.useState('human');

    return (
        <Card>
            <CardContent className="p-4 flex flex-col gap-2">
                <Input value={name} onChange={(e) => setName(e.target.value)} />
                <AvatarSelection selected={avatar} onSelect={setAvatar} />
            </CardContent>
        </Card>
    );
};
