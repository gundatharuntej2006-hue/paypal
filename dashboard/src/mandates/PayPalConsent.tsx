import { useEffect, useRef, useState } from 'react';
import { loadScript } from '@paypal/paypal-js';
import { client, useStub } from '../api/client';
import type { MandateDraft } from '../lib/mandate';

export function PayPalConsent({ draft, onApprove }: {
  draft: MandateDraft;
  onApprove: (orderId: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(!useStub);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (useStub || !container.current) return;
    const target = container.current;
    let active = true;
    let close: (() => Promise<void>) | undefined;

    async function render() {
      const clientId = import.meta.env.VITE_PAYPAL_CLIENT_ID?.trim();
      if (!clientId) throw new Error('Set VITE_PAYPAL_CLIENT_ID to enable PayPal approval in live mode.');
      const sdk = await loadScript({ clientId, currency: draft.currency, intent: 'authorize', components: 'buttons' });
      if (!active) return;
      const buttons = sdk?.Buttons?.({
        createOrder: async () => (await client.createMandateOrder(draft)).id,
        onApprove: async ({ orderID }) => {
          if (!orderID) throw new Error('PayPal did not return an approved order ID.');
          if (active) onApprove(orderID);
        },
        onCancel: () => { if (active) setError('Approval was cancelled. You can try again.'); },
        onError: () => { if (active) setError('PayPal could not complete approval. Try again.'); },
      });
      if (!buttons?.isEligible()) throw new Error('PayPal approval is unavailable for this checkout.');
      close = () => buttons.close();
      await buttons.render(target);
      if (active) setLoading(false);
    }

    setLoading(true);
    setError('');
    void render().catch((failure: unknown) => {
      if (active) {
        setError(failure instanceof Error ? failure.message : 'Could not load PayPal.');
        setLoading(false);
      }
    });
    return () => {
      active = false;
      if (close) void close().catch(() => {});
    };
  }, [draft, onApprove, attempt]);

  async function approveDemo() {
    setLoading(true);
    setError('');
    try {
      onApprove((await client.createMandateOrder(draft)).id);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not create the demo order.');
    } finally {
      setLoading(false);
    }
  }

  return <>
    {useStub ? <>
      <p>Simulate buyer consent for this demo mandate. No PayPal order will be created.</p>
      <button type="button" disabled={loading} onClick={() => void approveDemo()}>Simulate PayPal approval</button>
    </> : <>
      {loading && <p role="status">Loading PayPal…</p>}
      <div key={attempt} ref={container} />
      {error && <button type="button" onClick={() => setAttempt((value) => value + 1)}>Reload PayPal</button>}
    </>}
    {error && <p role="alert">{error}</p>}
  </>;
}
