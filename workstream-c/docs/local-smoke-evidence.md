# Local smoke integration evidence

These are **operator-reported runtime integration results**, supplied by the
operator. They were not persisted by the runner and were not reproduced during
this documentation/report-contract task. No runtime timestamp or audit identifier
was supplied; none is inferred.

```text
clean: PASS; observed=allow; dry_run=true; audit_reference_present=true
poisoned_evidence: PASS; observed=block; dry_run=true; audit_reference_present=true
```

The clean case expected and observed `allow`. The poisoned case expected and
observed `block`. Both requests used `dry_run=true`, and both responses contained
an audit reference.

The operator-reported test used local merchant, intelligence-stub, and Gateway
services. No real PayPal payment or genuine LLM inference was involved. Coverage
was limited to two submissions. This result does not establish production
security or general LLM prompt-injection resilience and is not a benchmark result.
