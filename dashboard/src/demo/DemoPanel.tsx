import { useEffect, useState } from 'react';
import { client, useStub } from '../api/client';
import type { DemoScenario } from '../api/models';
import type { Mandate } from '../contracts/types';
import './demo.css';

export const demoEnabled = useStub || import.meta.env.VITE_DEMO_MODE === 'true';

const scenarios: { id: DemoScenario; label: string }[] = [
  { id: 'happy_path', label: 'Happy path' },
  { id: 'poisoned_page', label: 'Poisoned page' },
  { id: 'budget_splitting', label: 'Budget splitting' },
];

export function DemoBanner() {
  if (!demoEnabled) return null;
  return <aside className="demo-banner">
    <strong>Demo mode</strong>
    <span>{useStub ? 'Simulated payments and PayPal approvals in this browser.' : 'Runs use the configured sandbox gateway.'}</span>
  </aside>;
}

export function DemoPanel() {
  const [mandates, setMandates] = useState<Mandate[]>([]);
  const [mandateId, setMandateId] = useState('');
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let current = true;
    if (demoEnabled) void client.listMandates().then((items) => {
      if (current) setMandates(items.filter((item) => Date.parse(item.valid_from) <= Date.now() && Date.parse(item.expires_at) > Date.now()));
    }).catch(() => {
      if (current) setStatus('Saved mandates could not be loaded. The seeded demo mandate is still available.');
    });
    return () => { current = false; };
  }, []);

  if (!demoEnabled) return null;

  async function run(scenario: DemoScenario, label: string) {
    if (running) return;
    setRunning(true);
    setError('');
    setStatus('Starting ' + label.toLowerCase() + '…');
    try {
      await client.runDemoScenario(scenario, mandateId || undefined);
      setStatus(useStub ? label + ' finished. See its decisions in the ledger below.' : label + ' started. Watch the ledger for its events.');
    } catch (failure) {
      setStatus('');
      setError(failure instanceof Error ? failure.message : 'Unable to start the demo.');
    } finally {
      setRunning(false);
    }
  }

  return <section className="panel card demo-panel" aria-label="Run demo scenario">
    <div><h2>Run demo scenario</h2><p>Replay a sandbox scenario and watch the audit events arrive below.</p></div>
    <label>Mandate <select value={mandateId} onChange={(event) => setMandateId(event.target.value)} disabled={running}>
      <option value="">Seeded demo mandate</option>
      {mandates.map((mandate) => <option key={mandate.mandate_id} value={mandate.mandate_id}>{mandate.task}</option>)}
    </select></label>
    <div className="actions">
      {scenarios.map(({ id, label }) => <button key={id} type="button" disabled={running} onClick={() => void run(id, label)}>{label}</button>)}
    </div>
    {status && <p role="status">{status}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
