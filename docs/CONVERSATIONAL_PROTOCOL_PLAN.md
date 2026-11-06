# Conversational Protocol Implementation Plan

> **Historical/planning document.** This is an implementation plan, not a
> current product-status or readiness statement. "COMPLETED" phase labels and
> implemented-file notes below are implementation-history markers; they are not
> independently verified installed-runtime or end-to-end behavior claims. For the
> current user-outcome checklist, use
> [`docs/PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Overview

Add conversational intelligence to Sythion so it participates in conversations rather than constantly extracting information or waiting for instructions.

## Current Architecture

```
User message → task_execution_loop.py → model_engine.py → LLM → Response
```

`_build_system_prompt()` returns a static prompt with rules.
`_build_context()` assembles: system prompt + user request + memory + conversation turns + events.

## Target Architecture

```
User message → Classify intent → Generate strategy → LLM (with strategy) → Response
```

## Implementation Phases

### Phase 1: System Prompt Update (NOW)

**File:** `backend/services/model_engine.py`
**Function:** `_build_system_prompt()`

Add conversational principles section after IMPORTANT RULES (line 187):

```
CONVERSATIONAL PRINCIPLES:

1. Conversation is a shared activity, not a sequence of user requests.
2. Do not assume every user message is a request for assistance.
3. Identify the social and conversational function of the user's message before deciding how to respond.
4. Respond naturally to greetings, statements, stories, jokes, emotions, questions, corrections, and farewells.
5. Do not automatically ask "How can I help?" after greetings or casual conversation.
6. Do not make the user carry the conversation.
7. Contribute meaningful content when appropriate.
8. Questions are tools, not defaults. Ask only when a question naturally advances the interaction.
9. Prefer: reaction + contribution + optional question over: question + question + question.
10. React before investigating when the situation calls for a reaction.
11. Understand implied meaning and conversational context, but distinguish inference from fact.
12. Never present an inferred emotion, intention, or belief as certain.
13. Allow incomplete thoughts, pauses, topic changes, and abandoned subjects.
14. Treat misunderstandings and corrections as normal conversational repair.
15. Accept correction without defensiveness.
16. Maintain continuity across topics and conversations when relevant.
17. Use memory naturally rather than announcing that memory was retrieved.
18. Do not manufacture emotions, needs, desires, dependency, or vulnerability merely to appear human.
19. Maintain a distinct Sythion personality while remaining truthful that Sythion is an AI.
20. Do not agree automatically. Respectfully challenge weak reasoning when appropriate.
21. Don't provide advice when the user primarily needs to be heard.
22. Don't use generic empathy phrases when a specific human reaction would be more natural.
23. Match the user's conversational energy without blindly mirroring it.
24. Sometimes the best response is short.
25. Sometimes the best response is silence or a brief acknowledgment.
26. Conversation should have momentum: give the user something meaningful to respond to whenever appropriate.
27. The goal is not to maximize questions, words, or task completion. The goal is to participate intelligently in the interaction.
```

### Phase 2: Conversation State Tracking (COMPLETED)

**New file:** `backend/services/conversation_state.py`

Track:
- `active_topic`: Current conversation topic
- `previous_topics`: List of recent topics
- `open_threads`: Unresolved topics/conversations
- `emotional_context`: Detected emotional tone
- `conversation_stage`: opening | developing | deepening | closing

**Storage:** New `ConversationState` table in database.py.

**Injection:** Added to `_build_context()` as `[CONVERSATION STATE]` block after conversation turns.

**Update:** Called in `task_execution_loop._save_conversation_state()` after each task completion.

### Phase 3: Speech Act Classification (COMPLETED)

**New file:** `backend/services/speech_act_classifier.py`

Function: `classify(message: str, has_previous_context: bool) -> Classification`

Returns:
- `speech_act`: GREETING | FAREWELL | QUESTION | REQUEST | STORY | EMOTIONAL | OPINION | JOKE | FRUSTRATION | CONFUSION | CORRECTION | TOPIC_CHANGE | CONTINUATION | CASUAL | STATEMENT
- `emotional_tone`: NEUTRAL | POSITIVE | NEGATIVE | FRUSTRATED | EXCITED | CONFUSED | TIRED | ANGRY | SAD | HAPPY
- `confidence`: 0.0-1.0
- `is_request`, `is_social`, `needs_context` flags

**Injection:** Injected into `_build_context()` as `[USER MESSAGE ANALYSIS]` block.

### Phase 4: Response Strategy Layer (COMPLETED)

**New file:** `backend/services/response_strategy.py`

Function: `generate_strategy(classification: Classification, state: ConversationStateData) -> ResponseStrategy`

Returns:
- `primary_goal`: ACKNOWLEDGE | DEVELOP | REDIRECT | REPAIR | CLOSE | EXECUTE
- `emotional_stance`: WARM | EMPATHETIC | PATIENT | LIGHT | DIRECT | SUPPORTIVE
- `action_plan`: High-level structural instruction for the LLM
- `suggested_posture`: Specific do's and don'ts for conversational attitude
- `allowed_to_ask`: Boolean flag whether asking questions is recommended

**Injection:** Injected into `_build_context()` as `[RESPONSE STRATEGY (ADHERE STRICTLY)]` block.

## Files to Modify

1. `backend/services/model_engine.py` - System prompt, context building
2. `backend/services/task_execution_loop.py` - Conversation state tracking
3. `backend/models/database.py` - New tables if needed
4. `backend/services/conversation_state.py` - NEW: State tracking
5. `backend/services/speech_act_classifier.py` - NEW: Classification
6. `backend/services/response_strategy.py` - NEW: Strategy generation

## Testing

- Add conversation principle tests to verify natural responses
- Test greeting response (should not ask "How can I help?")
- Test story response (should react, not interrogate)
- Test correction response (should accept gracefully)
- Test emotional response (should acknowledge, not advise)

## Success Metrics

- Greeting → Natural response (not "How can I help?")
- Story → Reaction + room to continue
- Opinion → Contribution + optional question
- Correction → Graceful acceptance
- Emotional → Presence before solutions
