# Beam diagnostic adjudication

## 2026-10-23 - researcher - 000 - ACCEPT
- brief: briefs/000-research-7.md
- report: reports/000-research-7.md (authorized beam diagnostic section only; unrelated earlier report preserved)
- evidence: One authorized live chat returned HTTP 200 and OK; actual built parser extracted blank from that response, while source helper extracted OK. SHA-256 comparison identifies stale target/release/backend/api/routes.py despite matching source and staged routes.
- coverage: Provider availability, tiny response, actual built parser, launcher resource resolution and selected model comparison covered. No UI success claimed.
- consistency: This corrects the earlier unsupported implication that a successful executable build refreshed its adjacent Python resources. It also limits the prior claim that beam works to the newly verified tiny request, not native tool support.
- not checked: Native UI, local onboarding HTTP endpoint, packaged interpreter imports, installer and rebuild not exercised. No production repair made by this diagnostic.
- self-directed: no
- verdict: ACCEPT
- reason: Evidence distinguishes working provider/key from stale built resource parsing. Next implementation must address resource freshness and validate actual release payload, not merely rebuild Rust or test checkout source.

The report path collided with a prior research report; that older report was preserved. Future briefs must reserve unused identifiers before writing outputs.
