# Disposable local smoke credential preparation

This helper prepares inputs only. It starts no service, sends no requests, and
does not enable either benchmark executor. Install the optional `local-smoke`
extra into an explicitly selected environment (`python -m pip install -e
".[local-smoke]"`). Normal Workstream C installations do not need cryptography.

Explicit Windows invocation (do not run until credential creation is authorized):

```powershell
& $wscPython -m agentguard_workstream_c.integration.prepare_credentials --confirm-create-local-smoke-credentials
```

The default directory is `%USERPROFILE%\AgentGuardSecrets`; `--output-dir` can
select a different local fixed-drive directory whose parent already exists.
UNC/network drives, device paths, alternate data streams, junctions and other
reparse points are rejected. Unsupported path aliases/ACL structures fail closed.
An existing output directory must already meet the exact protected ACL policy;
the helper does not repair existing permissions or overwrite destination files.

`windows_files.py` uses ctypes Win32 APIs, not PowerShell pathname ACL queries.
Every ancestor is opened from the volume root downward with real directory read
access and without FILE_SHARE_DELETE; those handles remain held through output
close. Handle-derived final paths and volume/file IDs are checked. GetSecurityInfo
reads owner/DACL from the same handles. Untrusted effective DELETE, DELETE_CHILD,
WRITE_DAC, WRITE_OWNER and write permissions reject the path. INHERIT_ONLY ACEs
do not apply to an ancestor itself; OWNER RIGHTS resolves to its verified owner.
Windows volume-root ADD_SUBDIRECTORY-only grants are accepted because they cannot
replace the pinned existing chain. Unknown/object/callback ACEs are rejected.
Windows TrustedInstaller is trusted only for ancestors, alongside the caller,
SYSTEM and Administrators; other ancestor owners are rejected.

New directories and files receive explicit protected ACLs at creation. Output
owner must be the caller and the exact three trustees must have full control.
Files use CREATE_NEW and no read/write/delete sharing. ACLs, final paths, single
link count and identity are checked on their actual opened handles, including
before writing and after fsync. The same handles back Python output streams.
No private key/tokens are generated before both files pass verification.

Failure is closed but is not a two-file transaction: either/both protected files
can be incomplete, even if the first file is valid JSON. Sanitized errors identify
only filenames successfully created by this invocation, tell the operator not to
use the pair and recommend a fresh secured directory. No automatic deletion is
performed: deleting a pathname after releasing a handle could remove a replacement
or pre-existing file. Review partial output manually; never reuse or overwrite it.

`local-smoke-mandates.json` contains format_version, task, purchase metadata and
two cases (clean and poisoned_evidence). Each case contains its signed mandate,
UUID idempotency_key and attack_id (null or poison_06). This is a local helper
file format, not a new gateway API schema. Both mandates authorize only merchant_a
and electronics_accessories, with 40.00 USD total/per-transaction/step-up limits,
a 30-second clock-skew allowance and 15-minute validity. Unique mandate IDs and
nonces isolate the cases. The proposed purchase is one 29.99 USD USB-C hub.

`local-smoke-secrets.json` contains principal_id, public_key, agent_id and four
independently random 32-byte URL-safe tokens: AGENT_AUTH_TOKEN, HUMAN_AUTH_TOKEN,
INTERNAL_LEDGER_TOKEN and INTEL_API_TOKEN. Configure INTEL_API_TOKEN identically
in gateway and intelligence. Use principal_id/public_key as TEST_PRINCIPAL_ID and
TEST_PRINCIPAL_PUBLIC_KEY. Do not register through POST /v1/mandates. No private
key or seed is persisted or printed; signatures are verified in memory before
writing. Python cannot promise physical memory zeroization. Administrators,
SYSTEM and code running as the current user remain trusted.

Signing follows gateway d4471a2d2b00d3fa840c406b68c8915faee0b8dc,
pkg/crypto/crypto.go: public_key is included; signature/unknown fields excluded;
nonempty allowlists sorted; optional empty policy/order fields omitted; UTC
RFC3339 timestamps have fractional seconds removed. JSON keys are sorted, output
is compact UTF-8, and Go HTML/U+2028/U+2029 escaping is reproduced. This is the
gateway's actual algorithm, not a claim of generic RFC 8785 support.

The public-only fixture fixtures/local-smoke/go-mandate-public-vector.json copies
the canonical JSON, mandate and signature from that commit's Go-exported signing
vector. Its private key is deliberately omitted. Never enroll its historical
test identity. Tests compare exact canonical bytes and verify the Go signature;
additional tests exercise timestamp, optional-field and escaping behavior.

Tests use a fixed dummy private key and deterministic dummy tokens only. Native
Windows tests create empty or noncredential fixture files, inspect an existing
Windows junction without writing through it, and exercise ancestor rights,
exclusive handles, identity/ACL queries, and replacement attempts. Fault tests
cover each file's serialization, creation, write, flush, fsync and close failure.
Internal injected test dependencies are trusted code, not a sandbox.

Crypto imports remain lazy. Without the extra, ordinary test collection and
noncrypto tests work; only tests requiring Ed25519/cryptography skip. Missing
cryptography produces a sanitized installation hint before filesystem changes.
For a complete suite in an explicitly selected Workstream C environment:

```powershell
& $wscPython -m pip install -e ".[dev,agent,local-smoke]" -c constraints-agent-windows.txt
& $wscPython -m pip check
& $wscPython -m pytest -q
```

Use the documented Windows TEMP workaround if the editable build requires it.
The full suite uses temporary loopback HTTP fixtures; credential-helper tests do
not make HTTP requests. No provider SDK extra is needed for these offline tests.

Before future requests, separately verify ENV=production, GATEWAY_MODE=dryrun,
loopback binding, INTEL_MODE=stub and fresh bootstrap identity. Preparation does
not prove runtime configuration. The separate two-case local network smoke runner is documented in
`local-smoke-runner.md`. Credential preparation sends no requests; the runner
performs only its two fixed local submissions. The full-stack benchmark executor
remains offline-only and does not provide arbitrary local scenario execution.
