import { useEffect, useMemo, useRef, useState } from 'react';
import { client } from '../api/client';
import type { ApprovalPayment } from '../api/models';
import type { EventEnvelope } from '../contracts/types';
import { projectLedger } from '../ledger/rows';

export type ReviewAction = 'approve' | 'reject';
type Submission = { payment: ApprovalPayment; action: ReviewAction; phase: 'sending' | 'waiting' | 'checkout' };

function projectInbox(snapshot: ApprovalPayment[], events: EventEnvelope[]) {
  const rows = projectLedger(events).payments;
  const pending = new Map(snapshot.filter((payment) => payment.decision === 'step_up')
    .map((payment) => [payment.payment_id, payment]));
  const decided = new Map<string, EventEnvelope>();
  const resolved = new Map<string, EventEnvelope>();
  const intents = new Map<string, EventEnvelope>();
  for (const event of events) {
    const id = event.payment_id;
    if (!id) continue;
    if (event.type === 'decision.made' && !decided.has(id)) decided.set(id, event);
    if ((event.type === 'human.approved' || event.type === 'human.rejected') && !resolved.has(id)) resolved.set(id, event);
    if (event.type === 'intent.received' && !intents.has(id)) intents.set(id, event);
  }

  for (const [id, decision] of decided) {
    const existing = pending.get(id);
    if (existing && Date.parse(existing.ts) > Date.parse(decision.ts)) continue;
    if (decision.payload?.decision !== 'step_up') {
      pending.delete(id);
      continue;
    }
    const row = rows.get(id);
    const extra = decision.payload as Partial<ApprovalPayment>;
    const items = intents.get(id)?.payload?.items;
    pending.set(id, {
      ...existing,
      ...extra,
      payment_id: id,
      mandate_id: decision.mandate_id,
      decision: 'step_up',
      ts: decision.ts,
      merchant_id: row?.merchant ?? existing?.merchant_id ?? 'Merchant unavailable',
      amount: row?.amount ?? existing?.amount ?? '',
      currency: row?.currency ?? existing?.currency ?? '',
      risk_score: row?.risk ?? existing?.risk_score,
      reasons: row?.reasons ?? existing?.reasons ?? [],
      items: extra.items ?? (Array.isArray(items) ? items as ApprovalPayment['items'] : existing?.items),
      injection_rationale: extra.injection_rationale ?? row?.checks.injection?.rationale ?? existing?.injection_rationale,
      intent_rationale: extra.intent_rationale ?? row?.checks.judge?.rationale ?? existing?.intent_rationale,
    });
  }
  for (const [id, event] of resolved) {
    const payment = pending.get(id);
    if (payment && Date.parse(event.ts) >= Date.parse(payment.ts)) pending.delete(id);
  }
  return { pending, resolved };
}

export function useApprovalInbox(events: EventEnvelope[], connected: boolean) {
  const [snapshot, setSnapshot] = useState<ApprovalPayment[]>();
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [retryCount, setRetryCount] = useState(0);
  const [submissions, setSubmissions] = useState<Record<string, Submission>>({});
  const posting = useRef(new Set<string>());
  const lastChange = events.find((event) => event.type === 'decision.made' ||
    event.type === 'human.approved' || event.type === 'human.rejected')?.id;

  useEffect(() => {
    let current = true;
    setLoadError('');
    void client.listApprovals().then(
      (data) => { if (current) setSnapshot(data); },
      (error: unknown) => { if (current) setLoadError(error instanceof Error ? error.message : 'Unable to load approvals.'); },
    );
    return () => { current = false; };
  }, [connected, lastChange, retryCount]);

  const { pending, resolved } = useMemo(() => projectInbox(snapshot ?? [], events), [snapshot, events]);

  useEffect(() => {
    setSubmissions((current) => {
      const confirmed = Object.entries(current).filter(([id, { payment }]) => {
        const event = resolved.get(id);
        return event && Date.parse(event.ts) >= Date.parse(payment.ts);
      });
      if (!confirmed.length) return current;
      const remaining = { ...current };
      for (const [id] of confirmed) delete remaining[id];
      return remaining;
    });
  }, [resolved]);

  function update(payment: ApprovalPayment, action: ReviewAction, phase: Submission['phase']) {
    setSubmissions((current) => ({ ...current, [payment.payment_id]: { payment, action, phase } }));
  }

  function cancelCheckout(id: string) {
    setSubmissions((current) => {
      if (current[id]?.phase !== 'checkout') return current;
      const remaining = { ...current };
      delete remaining[id];
      return remaining;
    });
  }

  async function submit(payment: ApprovalPayment, action: ReviewAction, detail?: string) {
    const id = payment.payment_id;
    if (posting.current.has(id) || submissions[id]) return;
    posting.current.add(id);
    setActionError('');
    update(payment, action, 'sending');
    try {
      await client[action](id, detail);
      setSubmissions((current) => current[id] ? { ...current, [id]: { ...current[id], phase: 'waiting' } } : current);
    } catch (error) {
      setSubmissions((current) => {
        const remaining = { ...current };
        delete remaining[id];
        return remaining;
      });
      setActionError(error instanceof Error ? error.message : 'Unable to submit the human decision.');
    } finally {
      posting.current.delete(id);
    }
  }

  return {
    pending: [...pending.values()].filter((payment) => !submissions[payment.payment_id]),
    awaiting: Object.values(submissions),
    confirmations: [...resolved.values()].slice(0, 5),
    loading: !snapshot && !loadError,
    loadError,
    actionError,
    retry: () => setRetryCount((value) => value + 1),
    submit,
    openCheckout: (payment: ApprovalPayment) => update(payment, 'approve', 'checkout'),
    cancelCheckout,
  };
}
