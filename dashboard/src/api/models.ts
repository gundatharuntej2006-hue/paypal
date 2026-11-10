import type { DecisionResponse } from '../contracts/types';

export interface Payment extends DecisionResponse {
  mandate_id: string;
  merchant_id: string;
  amount: string;
  currency: string;
  ts: string;
}

export interface ApprovalPayment extends Omit<Payment, 'risk_score' | 'checks' | 'audit_event_id'> {
  risk_score?: Payment['risk_score'];
  checks?: Payment['checks'];
  audit_event_id?: string;
  items?: { name: string; qty: number; unit_price: string }[];
  injection_rationale?: string;
  intent_rationale?: string;
  approval_url?: string;
  approval_order_id?: string;
  approval_intent?: 'authorize' | 'capture';
}

export type DemoScenario = 'happy_path' | 'poisoned_page' | 'budget_splitting';
