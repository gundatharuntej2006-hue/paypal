import type mandate from '../../../contracts/mandate.json';
import type paymentIntent from '../../../contracts/payment-intent-request.json';
import type decisionResponse from '../../../contracts/decision-response.json';
import type scanContent from '../../../contracts/scan-content.json';
import type judgeIntent from '../../../contracts/judge-intent.json';
import type riskScore from '../../../contracts/risk-score.json';
import type eventEnvelope from '../../../contracts/event-envelope.json';

export type Mandate = typeof mandate;
export type PaymentIntentRequest = typeof paymentIntent;
export type Decision = 'allow' | 'step_up' | 'block';
export type DecisionResponse = Omit<typeof decisionResponse, 'decision' | 'paypal'> & {
  decision: Decision;
  paypal?: Partial<typeof decisionResponse.paypal>;
};

export type ScanContentRequest = typeof scanContent.request;
export type ScanContentResponse = Omit<typeof scanContent.response, 'snippets'> & {
  snippets: string[];
};
export type JudgeIntentRequest = typeof judgeIntent.request;
export type JudgeIntentResponse = typeof judgeIntent.response;
export type RiskScoreRequest = Omit<typeof riskScore.request, 'history'> & {
  history: unknown[];
};
export type RiskScoreResponse = typeof riskScore.response;

export type EventType =
  | 'intent.received'
  | 'mandate.verified'
  | 'mandate.failed'
  | 'scan.completed'
  | 'judge.completed'
  | 'risk.scored'
  | 'decision.made'
  | 'human.approved'
  | 'human.rejected'
  | 'paypal.order_created'
  | 'paypal.authorized'
  | 'paypal.captured'
  | 'paypal.voided'
  | 'paypal.refunded'
  | 'webhook.received'
  | 'reconciliation.mismatch';

export type EventEnvelope = Omit<typeof eventEnvelope, 'type' | 'payload'> & {
  type: EventType;
  payload: Record<string, unknown>;
};
