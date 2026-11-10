import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useStub } from '../api/client';
import type { ApprovalPayment } from '../api/models';
import { CheckoutButton } from './CheckoutButton';
import type { ReviewAction, useApprovalInbox } from './inbox';
import './approvals.css';

type Inbox = ReturnType<typeof useApprovalInbox>;

function paypalLink(value?: string) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' && (url.hostname === 'paypal.com' || url.hostname.endsWith('.paypal.com')) &&
        !url.username && !url.password) return url.href;
  } catch {
    return undefined;
  }
}

const timeline = (id: string) => '/payments/' + encodeURIComponent(id);
const total = (payment: ApprovalPayment) => payment.amount ? [payment.currency, payment.amount].filter(Boolean).join(' ') : 'Amount unavailable';

function ReviewDialog({ payment, action, onClose, onSubmit, onCheckout }: {
  payment: ApprovalPayment;
  action: ReviewAction;
  onClose: () => void;
  onSubmit: (detail?: string) => void;
  onCheckout: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState('');
  const approve = action === 'approve';
  const link = approve && !useStub ? paypalLink(payment.approval_url) : undefined;
  const orderId = approve && !useStub ? payment.approval_order_id ?? payment.paypal?.order_id : undefined;
  const badLink = approve && !useStub && payment.approval_url && !link && !orderId;

  useEffect(() => {
    const modal = dialog.current;
    modal?.showModal();
    return () => modal?.close();
  }, []);

  return <dialog ref={dialog} className="panel card review-dialog" aria-labelledby="review-title"
    onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <p className="eyebrow">Human review</p>
    <h2 id="review-title">{approve ? 'Confirm approval' : 'Confirm rejection'}</h2>
    <p>{payment.merchant_id} · {total(payment)} · {payment.payment_id}</p>
    <form onSubmit={(event) => {
      event.preventDefault();
      if (!link && !orderId && !badLink) onSubmit(approve ? undefined : reason.trim() || undefined);
    }}>
      {!approve && <label>Rejection reason (optional)
        <textarea rows={3} maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
      </label>}
      {link && <p>Complete buyer approval in PayPal. The inbox will update when the gateway records the result.</p>}
      {orderId && !link && <CheckoutButton orderId={orderId} currency={payment.currency}
        intent={payment.approval_intent ?? 'authorize'} onApprove={onSubmit} />}
      {badLink && <p role="alert">The gateway supplied an invalid PayPal approval URL.</p>}
      <div className="actions">
        <button type="button" className="secondary" autoFocus onClick={onClose}>Cancel</button>
        {link ? <a className="review-link-button" href={link} target="_blank" rel="noopener noreferrer" onClick={onCheckout}>Open PayPal</a> :
          !orderId && !badLink && <button type="submit">{approve ? 'Confirm approval' : 'Confirm rejection'}</button>}
      </div>
    </form>
  </dialog>;
}

export function Approvals({ inbox }: { inbox: Inbox }) {
  const [review, setReview] = useState<{ payment: ApprovalPayment; action: ReviewAction }>();
  const selected = inbox.pending.find((payment) => payment.payment_id === review?.payment.payment_id);

  return <section>
    <header className="page-heading">
      <p className="eyebrow">Human review</p><h1>Approvals</h1>
      <p>Review the signals behind each step-up payment before allowing it to proceed.</p>
    </header>
    {inbox.actionError && <p className="panel notice" role="alert">{inbox.actionError}</p>}
    {inbox.loadError && <div className="panel notice" role="alert"><p>{inbox.loadError}</p><button type="button" onClick={inbox.retry}>Try again</button></div>}
    {inbox.loading && !inbox.pending.length && <p className="panel notice" role="status">Loading approvals…</p>}
    {!inbox.loading && !inbox.loadError && !inbox.pending.length && !inbox.awaiting.length &&
      <p className="panel notice">There are no payments awaiting approval.</p>}
    {inbox.awaiting.length > 0 && <section className="panel card approval-progress" aria-label="Submitted reviews" aria-live="polite">
      <h2>Awaiting confirmation</h2>
      <ul>{inbox.awaiting.map(({ payment, action, phase }) => <li key={payment.payment_id}>
        <Link to={timeline(payment.payment_id)}>{payment.payment_id}</Link> · {
          phase === 'checkout' ? 'Awaiting PayPal buyer approval' :
          phase === 'sending' ? 'Submitting ' + (action === 'approve' ? 'approval' : 'rejection') :
          (action === 'approve' ? 'Approval' : 'Rejection') + ' submitted. Waiting for the audit event.'
        }
        {phase === 'checkout' && <button className="secondary" type="button" onClick={() => inbox.cancelCheckout(payment.payment_id)}>Return to review</button>}
      </li>)}</ul>
    </section>}
    <div className="cards approval-cards">{inbox.pending.map((payment) => <article className="panel card" key={payment.payment_id}>
      <div className="event-heading"><span className="pill step_up">Step-up</span><Link to={timeline(payment.payment_id)}>{payment.payment_id}</Link></div>
      <h2>{payment.merchant_id}</h2>
      <p className="approval-total">{total(payment)}</p>
      <dl className="approval-signals">
        <dt>Risk score</dt><dd>{typeof payment.risk_score === 'number' ? payment.risk_score.toFixed(2) : 'Unavailable'}</dd>
        <dt>Signals</dt><dd>{payment.checks ? Object.entries(payment.checks).map(([name, result]) => name + ' · ' + result).join(', ') : 'Unavailable'}</dd>
        {payment.injection_rationale && <><dt>Injection scan</dt><dd>{payment.injection_rationale}</dd></>}
        {payment.intent_rationale && <><dt>Intent judge</dt><dd>{payment.intent_rationale}</dd></>}
      </dl>
      <h3>Items</h3>
      {payment.items?.length ? <ul>{payment.items.map((item, index) => <li key={index}>{item.qty} × {item.name} · {item.unit_price} {payment.currency}</li>)}</ul> : <p>Item details unavailable.</p>}
      <h3>Why this needs review</h3>
      {payment.reasons.length ? <ul>{payment.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : <p>No reason was supplied.</p>}
      <div className="actions">
        <button type="button" onClick={() => setReview({ payment, action: 'approve' })}>Approve</button>
        <button className="secondary" type="button" onClick={() => setReview({ payment, action: 'reject' })}>Reject</button>
      </div>
    </article>)}</div>
    {inbox.confirmations.length > 0 && <section className="panel card approval-progress" aria-label="Confirmed human decisions" aria-live="polite">
      <h2>Recent decisions</h2>
      <ul>{inbox.confirmations.map((event) => <li key={event.id}>
        <span className={'pill ' + (event.type === 'human.approved' ? 'allow' : 'block')}>{event.type === 'human.approved' ? 'Approved' : 'Rejected'}</span>
        {' '}<Link to={timeline(event.payment_id)}>{event.payment_id}</Link>
        {typeof event.payload?.reason === 'string' && <span> · {event.payload.reason}</span>}
      </li>)}</ul>
    </section>}
    {review && selected && <ReviewDialog payment={selected} action={review.action} onClose={() => setReview(undefined)}
      onSubmit={(detail) => { setReview(undefined); void inbox.submit(selected, review.action, detail); }}
      onCheckout={() => { setReview(undefined); inbox.openCheckout(selected); }} />}
  </section>;
}
