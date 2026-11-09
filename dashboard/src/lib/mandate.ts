import type { Mandate } from '../contracts/types';

export type MandateDefinition = Omit<Mandate, 'mandate_id' | 'principal_id' | 'nonce' | 'paypal_order_id' | 'signature'>;
export type MandateDraft = Omit<Mandate, 'paypal_order_id' | 'signature'>;

const amounts = ['max_total', 'max_per_txn', 'step_up_over'] as const;
const labels = { max_total: 'Total cap', max_per_txn: 'Per-transaction cap', step_up_over: 'Step-up threshold' };

export function validateDefinition(definition: MandateDefinition): string[] {
  const errors: string[] = [];
  if (!definition.task.trim()) errors.push('Enter a task.');
  if (!definition.agent_id.trim()) errors.push('Enter an agent ID.');
  if (!/^[A-Z]{3}$/.test(definition.currency)) errors.push('Enter a three-letter currency code.');
  if (!definition.allowed_merchants.some((item) => item.trim())) errors.push('Select at least one merchant.');
  if (!definition.allowed_categories.some((item) => item.trim())) errors.push('Enter at least one category.');

  const cents = amounts.map((field) => {
    const value = definition[field];
    if (/^\d+\.\d{2}$/.test(value)) {
      const parsed = BigInt(value.replace('.', ''));
      if (parsed > 0n) return parsed;
    }
    errors.push(labels[field] + ' must be a positive amount with two decimal places.');
    return null;
  });
  const [total, perTransaction, stepUp] = cents;
  if (total !== null && perTransaction !== null && perTransaction > total) {
    errors.push('Per-transaction cap must be at or below the total cap.');
  }
  if (perTransaction !== null && stepUp !== null && stepUp > perTransaction) {
    errors.push('Step-up threshold must be at or below the per-transaction cap.');
  }

  const start = Date.parse(definition.valid_from);
  const expiry = Date.parse(definition.expires_at);
  if (!Number.isFinite(start) || !Number.isFinite(expiry) || expiry <= start) {
    errors.push('Expiry must be after the validity start.');
  }
  return errors;
}

/**
 * Assumed wire format pending gateway confirmation: remove the top-level
 * signature, sort the keys lexicographically, preserve array order, use
 * JSON.stringify escaping, and encode the compact JSON as UTF-8.
 * Mandate values are strings and string arrays.
 */
export function canonicalize(mandate: Omit<Mandate, 'signature'> | Mandate): Uint8Array<ArrayBuffer> {
  const fields = Object.entries(mandate)
    .filter(([key]) => key !== 'signature')
    .sort(([first], [second]) => first < second ? -1 : first > second ? 1 : 0);
  return new TextEncoder().encode(JSON.stringify(Object.fromEntries(fields)));
}

export const toBase64 = (bytes: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(bytes)));
const fromBase64 = (value: string) => Uint8Array.from(atob(value), (character) => character.charCodeAt(0));

export async function signMandate(mandate: Omit<Mandate, 'signature'>, key: CryptoKey): Promise<Mandate> {
  if (!mandate.paypal_order_id) throw new Error('Approve a PayPal order before signing.');
  const signature = await crypto.subtle.sign('Ed25519', key, canonicalize(mandate));
  return { ...mandate, signature: toBase64(signature) };
}

export async function verifyMandate(mandate: Mandate, publicKey: string): Promise<boolean> {
  const key = await crypto.subtle.importKey('raw', fromBase64(publicKey), 'Ed25519', false, ['verify']);
  return crypto.subtle.verify('Ed25519', key, fromBase64(mandate.signature), canonicalize(mandate));
}

const storageKey = 'agentguard.demo-principal.v1';
let principal: Promise<{ id: string; publicKey: string; privateKey: CryptoKey }> | undefined;

async function loadPrincipal() {
  const saved = localStorage.getItem(storageKey);
  if (saved) {
    const { id, publicKey, privateKey } = JSON.parse(saved);
    const key = await crypto.subtle.importKey('pkcs8', fromBase64(privateKey), 'Ed25519', false, ['sign']);
    return { id: String(id), publicKey: String(publicKey), privateKey: key };
  }
  const keys = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const id = 'principal_' + crypto.randomUUID();
  const publicKey = toBase64(await crypto.subtle.exportKey('raw', keys.publicKey));
  const privateKey = toBase64(await crypto.subtle.exportKey('pkcs8', keys.privateKey));
  localStorage.setItem(storageKey, JSON.stringify({ id, publicKey, privateKey }));
  return { id, publicKey, privateKey: keys.privateKey };
}

export function getDemoPrincipal() {
  if (!crypto.subtle) return Promise.reject(new Error('Signing requires HTTPS or localhost.'));
  if (!principal) {
    const load = Promise.resolve(navigator.locks ? navigator.locks.request(storageKey, loadPrincipal) : loadPrincipal());
    principal = load.catch((error: unknown) => { principal = undefined; throw error; });
  }
  return principal;
}
