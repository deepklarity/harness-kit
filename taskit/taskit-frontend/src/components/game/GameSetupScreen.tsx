import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { PlayerSetupCard } from './SetupComponents';

export const GameSetupScreen: React.FC = () => {
    const navigate = useNavigate();
    const [rounds, setRounds] = useState(3);
    const [timeLimit, setTimeLimit] = useState(60);

    return (
        <div className="flex flex-col gap-4 p-4 max-w-md mx-auto">
            <header className="flex justify-between items-center">
                <Button variant="ghost" onClick={() => navigate(-1)}>Back</Button>
                <h1 className="text-xl font-bold">Game Setup</h1>
                <Button variant="ghost">?</Button>
            </header>
            
            <PlayerSetupCard player={1} />
            <PlayerSetupCard player={2} />

            <Card>
                <CardHeader><CardTitle>Game Options</CardTitle></CardHeader>
                <CardContent className="flex flex-col gap-4">
                    <div>
                        <label>Rounds</label>
                        <div className="flex gap-2">
                            <Button onClick={() => setRounds(Math.max(1, rounds - 1))}>-</Button>
                            <span>{rounds}</span>
                            <Button onClick={() => setRounds(Math.min(7, rounds + 1))}>+</Button>
                        </div>
                    </div>
                    <div>
                        <label>Guess Time Limit</label>
                        <div className="flex gap-2">
                            {[30, 45, 60, 75, 90].map(time => (
                                <Button key={time} variant={timeLimit === time ? 'default' : 'outline'} onClick={() => setTimeLimit(time)}>{time}s</Button>
                            ))}
                        </div>
                    </div>
                </CardContent>
            </Card>

            <Button className="w-full" onClick={() => navigate('/game')}>START GAME</Button>
        </div>
    );
};
