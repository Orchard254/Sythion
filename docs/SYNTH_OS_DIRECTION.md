# Synth as an always-on system (design direction, 2026-11-19)

This records the owner's direction for evolving Synth from a desktop app into an
always-on system. It is a design note, not an acceptance claim.

## Owner decisions (2026-11-19)

1. **Host:** run on this Windows PC for now. If it proves reliable through real
   use, move it later to a dedicated always-on PC with internet access. No cloud
   core yet.
2. **Phone:** the phone is an endpoint, not the system, and it is a *full
   conversational client* — the way one reaches Gemini, ChatGPT, or Claude. It
   is not merely a briefing/approval console: you talk to Synth there, and that
   conversation is the same conversation you have on the desktop (one Core, one
   memory, one identity). The phone is a window into Synth, not a second Synth.
   Synth may also access and operate the phone itself only under granted
   permissions, because that is where the owner's chats live. The owner may be
   abroad and still talk to Synth, receive updates, and give it goals. Long-term
   example: Synth runs a company and prepares the CEO's reports.
3. **Trust:** progressive, one information at a time. Start with the minimum the
   secretary needs; as behaviour proves predictable, grant more. Eventually it
   may operate without asking. Trust is earned, never assumed.

## Target architecture

- **Synth Core** — always-on local process holding identity, durable state,
  memory, authority, and the trust ledger. The LLM is a replaceable reasoning
  processor the Core calls. No client, token, or model output is the brain.
- **Devices** — the Windows PC, the phone, and any future machines are endpoints
  with an identity, a trust level, granted permissions, capabilities, and a
  last-seen record. They are sensors and actuators, not the system.
- **Client API** — one client-agnostic surface used by the desktop, the
  companion overlay, and the phone. A client cannot widen its own permissions.
  `POST /assistant/turns` is already the conversational contract; the phone can
  consume the same endpoint. The briefing rides on that response, and a future
  morning brief is simply a proactive message in the same conversation
  timeline. The difference from the hosted assistants is that this chat is
  backed by a body: durable state, tools, devices, and evidence — not just text.
- **Core services** — briefing, monitoring, scheduling, recovery, permission /
  trust ledger, device registry, and the reasoning boundary.
- **Briefing service** — assembles what happened and what is blocked from
  authoritative local state. It is read-only and never grants authority.

## What exists today

The *doing* is largely present: 63 tools for files, browser, GUI, apps,
packages, memory, objectives and device status; goals with evidence and
blockers; durable wakes; an owner-gated bounded wake; read-only recovery review
packets; and a versioned authority-free reasoning contract.

The *system* layer is missing: there is no device registry, no phone client, no
remote transport, no morning brief, and no progressive trust ledger. The
briefing layer is being built first because it is the user-facing foundation.

## Stages (each independently verifiable)

- **Stage 0 — briefing service (implemented 2026-11-20):** deterministic
  blocked-work briefing from local state; "heads up" card in the Assistant
  turn; no new remote surface, no new authority. Built:
  `backend/services/user_briefing.py` (read-only, bounded, sanitized,
  non-authorizing), `GET /api/v1/briefing`, and `briefing` on the Assistant
  turn response. Frontend `BriefingCard` renders the heads-up; every item is
  `authority: none`, `approved: false`, and the card has no approve control.
  Evidence: `test_user_briefing.py` (6 passed), `test_assistant_turn_api.py`
  (turn briefing test), `BriefingCard.test.tsx` (3 passed), 86 frontend tests
  pass, `tsc --noEmit` exit 0. Full backend suite pending.
- **Stage 1 — on-demand + morning brief:** a read-only briefing endpoint and a
  scheduled local brief from app-local state (goals, tasks, blockers, agent
  status, device health).
- **Stage 2 — device registry:** local machine identity, last-seen, health, and
  capability records; visible to the owner.
- **Stage 3 — trusted remote endpoint:** phone connects as a full conversational
  client with a per-device trust level and explicit grants. Talking to Synth
  works like the hosted assistants; consequential actions still go through the
  trusted boundary.
- **Stage 4 — phone as endpoint:** per-source grants (messages, notifications,
  news), goal submission from the phone, and operation of the phone where the
  owner has granted it — one source at a time. Conversational approval must be
  bound to a specific, fresh proposal; a free-floating "yes" in chat is never
  itself authority, and model output never becomes approval.
- **Stage 5 — dedicated host:** migrate the always-on Core to a separate
  internet-connected machine once local reliability is demonstrated.

## Non-negotiable invariants

- The LLM never holds authority, identity, or the deciding vote.
- Model output, UI flags, and tool claims never become approval.
- A conversational "yes" is the owner speaking, not the model — but it is only
  authority when bound to a specific, fresh, displayed proposal; an unbound
  "yes" is not approval.
- A briefing is read-only and contains no authorizing language.
- A client cannot expand its own permissions; grants are owner actions, audited.
- The default when uncertain is to ask, never to guess or auto-resume.
- Trust grows only through recorded, predictable behaviour — never silently.
