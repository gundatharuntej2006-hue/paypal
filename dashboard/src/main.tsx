import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <main>
      <h1>AgentGuard</h1>
      <p>Payment firewall dashboard</p>
    </main>
  </StrictMode>,
);
