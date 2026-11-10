import { useEffect, useRef, useState } from 'react';
import { loadScript } from '@paypal/paypal-js';

export function CheckoutButton({ orderId, currency, intent, onApprove }: {
  orderId: string; currency: string; intent: 'authorize' | 'capture'; onApprove: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const callback = useRef(onApprove);
  const [status, setStatus] = useState('Loading PayPal…');
  const [error, setError] = useState('');
  useEffect(() => { callback.current = onApprove; }, [onApprove]);

  useEffect(() => {
    const target = container.current;
    if (!target) return;
    let active = true;
    let close: (() => Promise<void>) | undefined;

    async function render() {
      const clientId = import.meta.env.VITE_PAYPAL_CLIENT_ID?.trim();
      if (!clientId) throw new Error('PayPal checkout requires VITE_PAYPAL_CLIENT_ID.');
      const sdk = await loadScript({ clientId, currency, intent, components: 'buttons' });
      if (!active || !target) return;
      const buttons = sdk?.Buttons?.({
        createOrder: async () => orderId,
        onApprove: async ({ orderID }) => {
          if (orderID !== orderId) throw new Error('PayPal returned an unexpected order ID.');
          if (active) callback.current(orderID);
        },
        onCancel: () => { if (active) setStatus('PayPal checkout was cancelled. You can try again.'); },
        onError: () => { if (active) setError('PayPal could not complete checkout.'); },
      });
      if (!buttons?.isEligible()) throw new Error('PayPal checkout is unavailable for this payment.');
      close = () => buttons.close();
      await buttons.render(target);
      if (active) setStatus('');
    }

    void render().catch((failure: unknown) => {
      if (active) {
        setStatus('');
        setError(failure instanceof Error ? failure.message : 'Unable to load PayPal.');
      }
    });
    return () => {
      active = false;
      if (close) void close().catch(() => {});
    };
  }, [orderId, currency, intent]);

  return <>
    {status && <p role="status">{status}</p>}
    {error && <p role="alert">{error}</p>}
    <div ref={container} />
  </>;
}
