import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { migrateStorageKeys } from './lib/storageMigration';

// Before the first render, so sign-in, theme and sidebar state saved under
// the app's old name are already in place when the app reads them.
migrateStorageKeys();

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
