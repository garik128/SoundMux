import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import './styles/themes.css';
import './styles/app.css';
import './styles/reference.css';
import './styles/settings.css';
import './styles/hint.css';
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
