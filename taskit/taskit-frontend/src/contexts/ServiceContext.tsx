import { createContext, useContext } from 'react';
import { HarnessTimeService } from '../services/harness/HarnessTimeService';

const service = new HarnessTimeService();

export const ServiceContext = createContext<HarnessTimeService>(service);

export function ServiceProvider({ children }: { children: React.ReactNode }) {
    return (
        <ServiceContext.Provider value={service}>
            {children}
        </ServiceContext.Provider>
    );
}

export function useService(): HarnessTimeService {
    return useContext(ServiceContext);
}
