"""Intent understanding (Phase 8).

Deterministic rule-based interpretation first; optional LLM refinement only
for genuinely ambiguous inputs (proposals only — the LLM never decides).
Every result carries confidence + ambiguity so downstream policy can choose
clarification over guessing.
"""

from __future__ import annotations

import re
from typing import List, Optional

from backend.cognitive.state import (
    AmbiguityLevel,
    IntentCategory,
    IntentResult,
    Interpretation,
    InteractionType,
)

_GOAL_MARKERS = re.compile(
    r"\b(over the next|this year|long.term|eventually|my goal|i want to become|"
    r"help me build|help me get|roadmap|plan for|create a report|generate a report|"
    r"organize\b.*?\binto|monitor\b.*?\band\s+notify|research\b.*?\b(?:and\s+)?produce|"
    r"develop\b.*?\b(?:plan|roadmap|system))\b", re.I)
_COMMAND_MARKERS = re.compile(
    r"^\s*(open|close|launch|start|run|kill|create|delete|write|move|copy|"
    r"install|uninstall|search|set|turn (on|off)|shutdown|restart|check|show|find|list|"
    r"read|inspect|view|"
    r"send|clean(?:\s+up)?|print|update|sync)\b", re.I)
_ACTION_QUESTION_MARKERS = re.compile(
    r"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:check|show|get|find|list|open|run|read|inspect|view|look at|tell me (?:what time|the time|my battery)|look up|send|clean|print|update|sync)\b|"
    r"\b(?:what\s+time\s+is\s+it|what\s+is\s+the\s+time|what\s+is\s+my\s+battery|how\s+is\s+my\s+battery)\b",
    re.I)
_INFORMATIONAL_MARKERS = re.compile(
    r"^\s*(?:explain|describe|define|elaborate on|tell me about\s+(?!yourself\b)|tell me why|explain why|what year|who created|who invented|why is|why are|why does|why did|how does|how do|how did|what is the capital|what is the difference)\b|"
    r"\b(?:tell me why|explain why)\b",
    re.I)
_QUESTION_MARKERS = re.compile(
    r"^\s*(what|who|when|where|which|why|how|is|are|does|do|can|could|should)\b|[?]\s*$",
    re.I)
_SOCIAL_MARKERS = re.compile(
    r"^\s*(?:hi|hey|hello|yo|thanks|thank you|good (?:morning|afternoon|evening|day)|"
    r"bye|goodbye|night|good night|sup)(?:,?\s+(?:sythion|synth|friend|there|all))?\b.*$|"
    r"\b(?:how have you been|how are you|how's it going|what's up|how do you do)\b|"
    r"\b(?:talk to me|let's talk|let's chat|chat with me|just chat|just chatting|let's just chat)\b|"
    r"\b(?:tell me something interesting|tell me a joke|tell me a story|tell me about yourself|tell me more)\b|"
    r"\b(?:i'm bored|i am bored)\b|"
    r"\b(?:what do you think|what's your opinion|what is your opinion|i just want your opinion|just want your opinion|give me your opinion|what are your thoughts)\b|"
    r"\b(?:why do you think that is|why do you think so)\b|"
    r"\b(?:i've been thinking|i have been thinking|thinking about)\b|"
    r"\b(?:that's interesting|that is interesting|interesting)\b[!. ]*$|"
    r"\b(?:what's on your mind|what is on your mind|nice to see you|maybe we should talk about)\b|"
    r"\b(?:stop there|don't do anything).*(?:just want|opinion|chat|talk)\b",
    re.I)
_CORRECTION_MARKERS = re.compile(
    r"\b(no,? (that's |that is )?(not |wrong)|not what i (wanted|meant)|"
    r"i said|i meant|you misunderstood|that's incorrect|wrong file|wrong one)\b",
    re.I)
_TRANSITION_MARKERS = re.compile(
    r"\b(?:actually|instead|on second thought|wait,?\s+just|wait,?\s+no|never\s+mind|no,?\s+(?:don't|do not)\s+do\s+anything|by\s+the\s+way|but\s+first)\b",
    re.I)
_CLARIFICATION_MARKERS = re.compile(
    r"^\s*(the (first|second|third|last)( one)?|option \d|number \d|#\d|"
    r"yes|no|sure|ok(ay)?)\b[.!]*\s*$", re.I)
_DEICTIC_ACTION_TARGETS = re.compile(
    r"\b(?:this|that)\s+(?:file|document|folder|script|report|email|package|record)\b|"
    r"\b(?:the\s+right\s+folder|the\s+document|the\s+file|the\s+folder|the\s+email|the\s+report|the\s+script|the\s+package)\b|"
    r"\b(?:to\s+[A-Z][a-z]+|there)\b|"
    r"^\s*(?:delete|remove|run|open|send|move|copy)\s+(?:it|that|them)\b",
    re.I
)
# Orthogonal action signal: a concrete filesystem path makes any request executable
# regardless of the leading verb (inspect, read, summarize, tell me what is in, ...).
# A user who names a specific path is always requesting tool execution, never chatting.
# This is a property of the *target*, not the verb — so it must not expand the verb vocabulary.
_CONCRETE_PATH_SIGNAL = re.compile(
    r"[A-Za-z]:\\[^\s]+"       # Windows absolute path: C:\foo\bar
    r"|(?<!\S)/[\w./\-]+"      # Unix absolute path: /home/user (leading slash, non-embedded)
    r"|~/\S+",                  # Tilde-home path: ~/documents
    re.I,
)



class IntentInterpreter:
    """Deterministic first-pass intent interpretation."""

    def interpret(self, user_input: str, context: Optional[dict] = None) -> IntentResult:
        text = (user_input or "").strip()
        lowered = text.lower()
        context = context or {}

        if not text:
            return IntentResult(
                category=IntentCategory.NO_ACTION if hasattr(IntentCategory, "NO_ACTION")
                else IntentCategory.SOCIAL,
                interpretations=[Interpretation(
                    description="empty input", category=IntentCategory.SOCIAL)],
                ambiguity=AmbiguityLevel.CLEAR)

        # 1. Transitions / Explicit redirections within the utterance
        trans_match = _TRANSITION_MARKERS.search(text)
        if trans_match:
            post_transition = text[trans_match.start():].strip()
            if re.search(r"\b(?:just want|opinion|chat|talk)\b", post_transition, re.I):
                return self._result(
                    IntentCategory.SOCIAL, 0.9,
                    Interpretation("user redirected to conversation", IntentCategory.SOCIAL),
                    text)
            clean_post = _TRANSITION_MARKERS.sub("", post_transition).strip().lstrip(",:;- ")
            if _COMMAND_MARKERS.match(clean_post) or _ACTION_QUESTION_MARKERS.search(clean_post):
                res = self._check_command(clean_post, context)
                res.desired_outcome = text
                return res
            if _INFORMATIONAL_MARKERS.search(clean_post) or ("why" in clean_post.lower() and "?" in clean_post):
                return self._result(
                    IntentCategory.INFORMATIONAL, 0.85,
                    Interpretation("user redirected to informational inquiry", IntentCategory.INFORMATIONAL),
                    text)

        # 1.5 Multi-sentence commands (e.g. "Nice weather today! Find all pdfs in downloads.")
        sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
        for s in sentences:
            if _COMMAND_MARKERS.match(s):
                return self._check_command(s, context)

        # 2. Corrections dominate: the user is telling us we were wrong.
        if _CORRECTION_MARKERS.search(lowered):
            return self._result(
                IntentCategory.CORRECTION, 0.85,
                Interpretation("user is correcting a previous outcome/interpretation",
                               IntentCategory.CORRECTION), text)

        # 3. Clarifications are short answers to our pending question.
        if _CLARIFICATION_MARKERS.match(lowered):
            return self._result(
                IntentCategory.CLARIFICATION, 0.75,
                Interpretation("user replying to a pending clarification",
                               IntentCategory.CLARIFICATION), text)

        # 4. Contextual continuation: active conversation without action verbs
        if context.get("in_conversation") and not (_COMMAND_MARKERS.match(text) or _ACTION_QUESTION_MARKERS.search(text) or _GOAL_MARKERS.search(text)):
            if any(w in lowered for w in ("it", "that", "why", "think", "slow", "feel", "bored", "computer", "laptop")):
                return self._result(
                    IntentCategory.SOCIAL, 0.85,
                    Interpretation("conversational continuation on active topic", IntentCategory.SOCIAL),
                    text)

        # 5. Compound / Mixed intent: action questions embedded
        if _ACTION_QUESTION_MARKERS.search(text):
            return self._check_command(text, context)

        # 6. Conversational / social dialogue (expanded Phase 13A)
        if _SOCIAL_MARKERS.search(lowered) and not (_COMMAND_MARKERS.match(text) or _ACTION_QUESTION_MARKERS.search(text)):
            return self._result(
                IntentCategory.SOCIAL, 0.9,
                Interpretation("conversational/social exchange",
                               IntentCategory.SOCIAL), text)

        # 7. Goals
        is_goal = bool(_GOAL_MARKERS.search(text))
        is_command = bool(_COMMAND_MARKERS.match(text))

        if is_goal and not is_command:
            return self._result(
                IntentCategory.GOAL, 0.8,
                Interpretation("long-running objective spanning sessions",
                               IntentCategory.GOAL), text)

        # 8. Commands
        if is_command:
            return self._check_command(text, context)

        # 9. Follow-up heuristics: short referential input
        word_count = len(text.split())
        referential = bool(re.search(
            r"\b(it|that|this|they|them|one|again|also|more|first|second)\b",
            lowered))
        if _CLARIFICATION_MARKERS.search(lowered):
            return self._result(
                IntentCategory.CLARIFICATION, 0.75,
                Interpretation("user replying to a pending clarification",
                               IntentCategory.CLARIFICATION), text)
        if word_count <= 8 and referential:
            if context.get("in_conversation"):
                return self._result(
                    IntentCategory.SOCIAL, 0.8,
                    Interpretation("conversational referential follow-up", IntentCategory.SOCIAL),
                    text)
            return self._result(
                IntentCategory.FOLLOW_UP, 0.75,
                Interpretation("referential follow-up on prior context",
                               IntentCategory.FOLLOW_UP), text)

        # 10. Informational
        is_info = bool(_INFORMATIONAL_MARKERS.search(text))
        is_question = (bool(_QUESTION_MARKERS.search(text)) and "?" in text) or is_info

        if is_info or is_question:
            return self._result(
                IntentCategory.INFORMATIONAL, 0.85,
                Interpretation("informational query → answer",
                               IntentCategory.INFORMATIONAL), text)

        # Concrete filesystem path present → always an executable action request.
        # Fires here (after all verb-based checks) so it rescues requests whose
        # leading verb (inspect, read, look at, summarize, tell me what is in, ...)
        # is absent from _COMMAND_MARKERS without expanding that vocabulary.
        if _CONCRETE_PATH_SIGNAL.search(text):
            return self._check_command(text, context)

        # Default: open conversational / statement input with clear intent (Phase 13A)
        return self._result(
            IntentCategory.SOCIAL, 0.8,
            Interpretation("conversational statement/open exchange", IntentCategory.SOCIAL),
            text)


    def _check_command(self, text: str, context: dict) -> IntentResult:
        has_named_target = bool(
            _CONCRETE_PATH_SIGNAL.search(text)
            or re.search(r"\bthe\s+(?:file|document|folder|script|report|package)\s+['\"]?[A-Za-z0-9_.\-\\/:]+\.[a-zA-Z0-9]+['\"]?", text)
            or re.search(r"\bthe\s+(?:file|document|folder|script|report|package)\s+[A-Za-z]:\\", text)
        )
        has_antecedent = bool(context.get("has_file_context") or context.get("has_target_entity") or has_named_target)
        if _DEICTIC_ACTION_TARGETS.search(text) and not has_antecedent:
            res = self._result(
                IntentCategory.COMMAND, 0.5,
                Interpretation("action requested but lacks identifiable target entity", IntentCategory.COMMAND),
                text)
            res.ambiguity = AmbiguityLevel.AMBIGUOUS
            res.needs_clarification = True
            return res

        return self._result(
            IntentCategory.COMMAND, 0.85,
            Interpretation("immediate machine action requested", IntentCategory.COMMAND),
            text)

    def determine_interaction(
        self,
        user_input: str,
        intent: Optional[IntentResult] = None,
        context: Optional[dict] = None,
    ) -> InteractionType:
        """Phase 13A/13C deterministic interaction contract."""
        context = context or {}
        if intent is None:
            intent = self.interpret(user_input, context=context)
        cat = intent.category
        text = (user_input or "").strip().lower()

        # Actionable ambiguity check
        if intent.needs_clarification and cat in (IntentCategory.COMMAND, IntentCategory.GOAL):
            return InteractionType.CLARIFICATION_REQUIRED

        # Transitions
        trans_match = _TRANSITION_MARKERS.search(user_input)
        if trans_match:
            post_trans = user_input[trans_match.start():].strip()
            if re.search(r"\b(?:just want|opinion|chat|talk)\b", post_trans, re.I):
                return InteractionType.CONVERSATION
            clean_post = _TRANSITION_MARKERS.sub("", post_trans).strip().lstrip(",:;- ")
            if _COMMAND_MARKERS.match(clean_post) or _ACTION_QUESTION_MARKERS.search(clean_post):
                return InteractionType.ACTION
            if _INFORMATIONAL_MARKERS.search(clean_post) or ("why" in clean_post.lower() and "?" in clean_post):
                return InteractionType.INFORMATIONAL

        # Check goals first
        if _GOAL_MARKERS.search(user_input):
            return InteractionType.GOAL

        # Compound clauses: check if any clause contains an action
        clauses = [c.strip() for c in re.split(r"[,;]|\b(?:and|but|then|also)\b", user_input) if c.strip()]
        for c in clauses:
            clean_c = _TRANSITION_MARKERS.sub("", c).strip().lstrip(",:;- ")
            if _COMMAND_MARKERS.match(clean_c) or _ACTION_QUESTION_MARKERS.search(clean_c) or _CONCRETE_PATH_SIGNAL.search(clean_c):
                return InteractionType.ACTION

        # Compound / Action questions
        if _ACTION_QUESTION_MARKERS.search(user_input):
            return InteractionType.ACTION

        # Concrete filesystem path present → ACTION or GOAL
        if _CONCRETE_PATH_SIGNAL.search(user_input):
            if "create a report" in text or "generate a report" in text or len(text.split()) > 35:
                return InteractionType.GOAL
            return InteractionType.ACTION

        if _COMMAND_MARKERS.match(user_input):
            if "create a report" in text or "generate a report" in text or len(text.split()) > 35:
                return InteractionType.GOAL
            return InteractionType.ACTION

        if _INFORMATIONAL_MARKERS.search(user_input):
            return InteractionType.INFORMATIONAL

        if _SOCIAL_MARKERS.search(user_input):
            return InteractionType.CONVERSATION

        if cat == IntentCategory.SOCIAL:
            return InteractionType.CONVERSATION
        if cat == IntentCategory.CLARIFICATION:
            return InteractionType.CONVERSATION
        if cat == IntentCategory.CORRECTION:
            return InteractionType.ACTION
        if cat == IntentCategory.GOAL:
            return InteractionType.GOAL
        if cat == IntentCategory.COMMAND:
            return InteractionType.ACTION
        if cat == IntentCategory.INFORMATIONAL:
            return InteractionType.INFORMATIONAL

        return InteractionType.CONVERSATION

    def refine_with_llm(self, base: IntentResult, user_input: str,
                        llm_interpretations: Optional[List[dict]]) -> IntentResult:
        """Merge LLM-PROPOSED interpretations into the deterministic result.

        The LLM can add candidates but never override category when the
        deterministic interpreter is confident (>=0.8). Ambiguity may rise
        when materially different proposals appear.
        """
        if not llm_interpretations:
            return base
        merged = list(base.interpretations)
        for raw in llm_interpretations[:3]:
            try:
                interp = Interpretation(
                    description=str(raw.get("description", ""))[:200],
                    category=IntentCategory(str(raw.get("category", "informational"))),
                    confidence=float(raw.get("confidence", 0.4)),
                )
                if all(i.description != interp.description for i in merged):
                    merged.append(interp)
            except (ValueError, AttributeError, TypeError):
                continue  # malformed proposal ignored, never fatal

        categories = {i.category for i in merged}
        result = IntentResult(
            category=base.category,
            interpretations=merged,
            desired_outcome=base.desired_outcome,
            confidence=base.confidence,
            needs_clarification=base.needs_clarification,
            source="llm_refined" if len(merged) > len(base.interpretations) else base.source,
        )
        if len(categories) > 1 and base.confidence < 0.8:
            result.ambiguity = AmbiguityLevel.AMBIGUOUS
            result.needs_clarification = True
        return result

    @staticmethod
    def _result(category: IntentCategory, confidence: float,
                interp: Interpretation, text: str) -> IntentResult:
        ambiguity = AmbiguityLevel.CLEAR
        if confidence < 0.55:
            ambiguity = AmbiguityLevel.AMBIGUOUS
        elif confidence < 0.7:
            ambiguity = AmbiguityLevel.LOW_AMBIGUITY
        return IntentResult(
            category=category,
            interpretations=[interp],
            ambiguity=ambiguity,
            confidence=confidence,
        )