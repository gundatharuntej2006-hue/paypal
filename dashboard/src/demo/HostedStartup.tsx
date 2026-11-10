import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { checkGatewayHealth, useStub } from '../api/client';
import './demo.css';

const retryDelays = [1000, 2000, 4000, 8000, 8000];

export function HostedStartup({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<'waking' | 'ready' | 'failed'>(useStub ? 'ready' : 'waking');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (useStub) return;
    const controller = new AbortController();
    let timer: number | undefined;

    async function probe(index: number) {
      try {
        await checkGatewayHealth(controller.signal);
        if (!controller.signal.aborted) setPhase('ready');
      } catch {
        if (controller.signal.aborted) return;
        const delay = retryDelays[index];
        if (delay === undefined) setPhase('failed');
        else timer = window.setTimeout(() => { void probe(index + 1); }, delay);
      }
    }

    setPhase('waking');
    void probe(0);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [attempt]);

  if (phase === 'ready') return children;
  return <main className="startup-screen">
    <section className="panel card">
      <p className="eyebrow">AgentGuard</p>
      {phase === 'waking' ? <div role="status">
        <h1>Waking up the server</h1>
        <p>The hosted gateway may take a moment to start. This page will retry automatically.</p>
      </div> : <div role="alert">
        <h1>The gateway is still unavailable</h1>
        <p>Give the hosted service a little more time, then try again.</p>
        <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry connection</button>
      </div>}
    </section>
  </main>;
}
