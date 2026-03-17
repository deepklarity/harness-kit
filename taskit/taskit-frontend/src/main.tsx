import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

// Unregister any stale service workers so new Notification() works from page context.
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations().then(regs => {
        regs.forEach(reg => reg.unregister());
    });
}
import { AuthProvider } from './contexts/AuthContext'
import { ServiceProvider } from './contexts/ServiceContext'
import { NotificationProvider } from './contexts/NotificationContext'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
    <AuthProvider>
        <ServiceProvider>
            <BrowserRouter>
                <NotificationProvider>
                    <App />
                </NotificationProvider>
            </BrowserRouter>
        </ServiceProvider>
    </AuthProvider>
);
