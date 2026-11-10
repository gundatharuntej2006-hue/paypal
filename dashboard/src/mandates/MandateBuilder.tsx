import { useCallback, useState } from 'react';
import type { FormEvent } from 'react';
import { client } from '../api/client';
import type { Mandate } from '../contracts/types';
import { getDemoPrincipal, signMandate, validateDefinition, verifyMandate } from '../lib/mandate';
import type { MandateDraft } from '../lib/mandate';
import { PayPalConsent } from './PayPalConsent';

function localInputDate(timestamp: number) {
  const date = new Date(timestamp);
  return new Date(timestamp - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function initialFields() {
  const now = Date.now();
  return {
    task: '', agent_id: 'agent_shopper', currency: 'USD', max_total: '60.00', max_per_txn: '40.00',
    step_up_over: '25.00', categories: 'electronics_accessories',
    valid_from: localInputDate(now), expires_at: localInputDate(now + 86_400_000),
  };
}

function isoDate(value: string) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : '';
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [message, setMessage] = useState('');
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setMessage('Copied.');
    } catch {
      setMessage('Clipboard unavailable. Select the displayed text to copy it.');
    }
  }
  return <div className="actions">
    <button type="button" className="secondary" onClick={() => void copy()}>{label}</button>
    <span role="status">{message}</span>
  </div>;
}

export function MandateBuilder({ onSaved }: { onSaved: (mandate: Mandate) => void }) {
  const [fields, setFields] = useState(initialFields);
  const [merchants, setMerchants] = useState(['merchant_a', 'merchant_b']);
  const [allowed, setAllowed] = useState(merchants);
  const [newMerchant, setNewMerchant] = useState('');
  const [principal, setPrincipal] = useState<Awaited<ReturnType<typeof getDemoPrincipal>>>();
  const [draft, setDraft] = useState<MandateDraft>();
  const [orderId, setOrderId] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState('');
  const step = orderId ? 3 : draft ? 2 : 1;

  function field(key: keyof typeof fields, value: string) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  function addMerchant() {
    const merchant = newMerchant.trim();
    if (!merchant) return;
    if (!merchants.includes(merchant)) setMerchants([...merchants, merchant]);
    if (!allowed.includes(merchant)) setAllowed([...allowed, merchant]);
    setNewMerchant('');
  }

  function preset(hours: number) {
    const start = Date.parse(fields.valid_from);
    if (Number.isFinite(start)) field('expires_at', localInputDate(start + hours * 3_600_000));
  }

  async function define(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaved('');
    const { categories, ...input } = fields;
    const definition = {
      ...input,
      task: input.task.trim(),
      agent_id: input.agent_id.trim(),
      currency: input.currency.trim().toUpperCase(),
      allowed_merchants: allowed,
      allowed_categories: [...new Set(categories.split(',').map((value) => value.trim()).filter(Boolean))],
      valid_from: isoDate(input.valid_from),
      expires_at: isoDate(input.expires_at),
    };
    const validation = validateDefinition(definition);
    setErrors(validation);
    if (validation.length) return;
    setBusy(true);
    try {
      const key = await getDemoPrincipal();
      setPrincipal(key);
      setDraft({ ...definition, principal_id: key.id, public_key: key.publicKey, mandate_id: 'mdt_' + crypto.randomUUID(), nonce: crypto.randomUUID() });
    } catch (error) {
      setErrors([error instanceof Error ? error.message : 'Could not generate the demo signing key.']);
    } finally {
      setBusy(false);
    }
  }

  const approve = useCallback((id: string) => setOrderId(id), []);

  async function save() {
    if (!draft || !principal || !orderId) return;
    setBusy(true);
    setErrors([]);
    try {
      if (Date.parse(draft.expires_at) <= Date.now()) throw new Error('This mandate expired during approval. Edit the definition and approve a new order.');
      const mandate = await signMandate({ ...draft, paypal_order_id: orderId }, principal.privateKey);
      if (!await verifyMandate(mandate, principal.publicKey)) throw new Error('The local signature check failed.');
      const stored = await client.createMandate(mandate);
      setSaved(stored.mandate_id);
      setDraft(undefined);
      setOrderId('');
      setFields(initialFields());
      onSaved(stored);
    } catch (error) {
      setErrors([error instanceof Error ? error.message : 'Could not save the signed mandate.']);
    } finally {
      setBusy(false);
    }
  }

  function edit() {
    setDraft(undefined);
    setOrderId('');
    setErrors([]);
  }

  return <section className="panel card mandate-builder">
    <h2>Build a mandate</h2>
    <ol className="steps" aria-label="Mandate setup">
      {['Define', 'Approve with PayPal', 'Sign and Save'].map((label, index) =>
        <li key={label} aria-current={step === index + 1 ? 'step' : undefined}>{index + 1}. {label}</li>)}
    </ol>
    {saved && <p role="status">Saved {saved}. The signed JSON is in the mandates list below.</p>}
    {errors.length > 0 && <ul className="form-errors" role="alert">{errors.map((error) => <li key={error}>{error}</li>)}</ul>}
    {!draft ? <form className="mandate-form" onSubmit={(event) => void define(event)} noValidate>
      <fieldset disabled={busy}>
        <legend>Define spending authority</legend>
        <label className="wide">Task<textarea rows={3} value={fields.task} onChange={(event) => field('task', event.target.value)} /></label>
        <label>Agent ID<input value={fields.agent_id} onChange={(event) => field('agent_id', event.target.value)} /></label>
        <label>Currency<input maxLength={3} value={fields.currency} onChange={(event) => field('currency', event.target.value)} /></label>
        {([['max_total', 'Total cap'], ['max_per_txn', 'Per-transaction cap'], ['step_up_over', 'Step-up threshold']] as const)
          .map(([key, label]) => <label key={key}>{label}<input inputMode="decimal" value={fields[key]} onChange={(event) => field(key, event.target.value)} /></label>)}
        <label>Allowed categories <span className="field-hint">(comma-separated)</span>
          <input value={fields.categories} onChange={(event) => field('categories', event.target.value)} />
        </label>
        <fieldset className="wide merchant-picker">
          <legend>Allowed merchants</legend>
          <div className="actions">{merchants.map((merchant) => <label key={merchant}>
            <input type="checkbox" checked={allowed.includes(merchant)} onChange={(event) => {
              setAllowed(event.target.checked ? [...allowed, merchant] : allowed.filter((item) => item !== merchant));
            }} /> {merchant}
          </label>)}</div>
          <div className="actions">
            <label>Add merchant<input value={newMerchant} onChange={(event) => setNewMerchant(event.target.value)} onKeyDown={(event) => {
              if (event.key === 'Enter') { event.preventDefault(); addMerchant(); }
            }} /></label>
            <button className="secondary" type="button" onClick={addMerchant}>Add</button>
          </div>
        </fieldset>
        <label>Valid from (local time)<input type="datetime-local" value={fields.valid_from} onChange={(event) => field('valid_from', event.target.value)} /></label>
        <label>Expires at (local time)<input type="datetime-local" value={fields.expires_at} onChange={(event) => field('expires_at', event.target.value)} /></label>
        <div className="actions wide">
          <button className="secondary" type="button" onClick={() => preset(1)}>1h</button>
          <button className="secondary" type="button" onClick={() => preset(24)}>24h</button>
        </div>
      </fieldset>
      <button type="submit" disabled={busy}>{busy ? 'Preparing signing key…' : 'Continue to PayPal'}</button>
    </form> : <>
      <p>{draft.task} · {draft.currency} {draft.max_total} total spending cap</p>
      {!orderId ? <PayPalConsent draft={draft} onApprove={approve} /> : <>
        <p>Approved order <strong>{orderId}</strong></p>
        <button type="button" disabled={busy} onClick={() => void save()}>{busy ? 'Saving…' : 'Sign and save mandate'}</button>
      </>}
      <button type="button" className="secondary edit-definition" disabled={busy} onClick={edit}>Edit definition</button>
    </>}
    {principal && <section className="panel card principal-key">
      <h3>Principal key</h3>
      <p>{principal.id}</p>
      <p>Ed25519 public key (base64)</p>
      <code>{principal.publicKey}</code>
      <CopyButton text={principal.publicKey} label="Copy public key" />
    </section>}
  </section>;
}
