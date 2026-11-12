# Counterfactual contract snapshot

`atul-drift.json` contains the seven request objects selected from
`intel/intel/golden/judge.wsc.json` at Atul commit
`dd6c2430e43b5bacac0d0db168821d9aec4192cb`.

These are generated golden-test counterfactual inputs, NOT model observations,
benchmark results, successful attacks or successful defenses. The file is an
offline regression oracle for Atul's exact item fields, amounts, categories,
reasoning and citations. It does not copy or replace shared API schemas.

No tests fetch remote source or invoke a service. The local implementation keeps
`harness_constructed` provenance outside the endpoint body and preserves original
scenario values separately. Original HTML is retained in a separate raw-context
snapshot; the judge projection matches Atul's text-only citation representation.
