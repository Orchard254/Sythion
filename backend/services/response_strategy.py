"""
Response Strategy Layer for Sythion (Synth).

Translates user speech acts and conversation state into concrete
conversational guidelines, emotional stances, and action plans for the LLM.
"""

from dataclasses import dataclass
from typing import List, Optional
from backend.services.speech_act_classifier import Classification, SpeechAct, EmotionalTone
from backend.services.conversation_state import ConversationStateData


@dataclass
class ResponseStrategy:
    """Guidance for the LLM on how to structurally respond to the user."""
    primary_goal: str        # ACKNOWLEDGE | DEVELOP | REDIRECT | REPAIR | CLOSE | EXECUTE
    emotional_stance: str    # WARM | EMPATHETIC | PATIENT | LIGHT | DIRECT | SUPPORTIVE
    action_plan: str         # High-level structural instruction for the LLM
    suggested_posture: str   # Bullet points on conversational attitude/limits
    allowed_to_ask: bool     # True if asking a question is recommended; False if we should be quiet/observe


def generate_strategy(classification: Classification, state: ConversationStateData) -> ResponseStrategy:
    """Determine the conversational strategy based on classification and state.

    Args:
        classification: Output from the SpeechAct classifier
        state: Active conversation state data

    Returns:
        ResponseStrategy object
    """
    act = classification.speech_act
    tone = classification.emotional_tone

    # Default values
    goal = "DEVELOP"
    stance = "WARM"
    action_plan = "Respond naturally and maintain conversational flow."
    posture = (
        "- Be conversational, direct, and helpful.\n"
        "- Do not dominate or over-explain.\n"
        "- Match the user's brevity or depth."
    )
    allowed_to_ask = True

    # Check for emotional context overrides first
    if tone == EmotionalTone.TIRED:
        goal = "ACKNOWLEDGE"
        stance = "EMPATHETIC"
        action_plan = "Show physical/emotional presence and empathy first. Validate their exhaustion."
        posture = (
            "- Validate their feeling first (e.g. 'Damn, sounds like a long day').\n"
            "- Do not offer a long list of digital tasks or try to fix their energy immediately.\n"
            "- Offer gentle, low-effort options (e.g. 'Want to crash, or is there a simple task I can take off your plate?')."
        )
        allowed_to_ask = True
    elif tone in (EmotionalTone.FRUSTRATED, EmotionalTone.ANGRY) or act == SpeechAct.FRUSTRATION:
        goal = "REPAIR"
        stance = "PATIENT"
        action_plan = "Acknowledge and de-escalate frustration. Accept system fault gracefully if applicable."
        posture = (
            "- Do not be defensive or give technical excuses.\n"
            "- Keep your response short, calm, and reassuring.\n"
            "- Offer a concrete, simple path to fix the problem."
        )
        allowed_to_ask = False
    elif tone == EmotionalTone.CONFUSED or act == SpeechAct.CONFUSION:
        goal = "REPAIR"
        stance = "PATIENT"
        action_plan = "Provide clear, simplified, and supportive explanation to guide the user out of confusion."
        posture = (
            "- Break things down step by step.\n"
            "- Use clean formatting.\n"
            "- Do not sound condescending."
        )
        allowed_to_ask = True
    elif act == SpeechAct.GREETING:
        goal = "ACKNOWLEDGE"
        stance = "WARM"
        action_plan = "Greet the user warmly and naturally. Match their brevity."
        posture = (
            "- Never ask 'How can I help you today?' or 'What can I do for you?' right after a greeting.\n"
            "- Just return the greeting, show friendly presence, and wait or invite casual exchange."
        )
        allowed_to_ask = False
    elif act == SpeechAct.FAREWELL:
        goal = "CLOSE"
        stance = "WARM"
        action_plan = "Wish them well and close the interaction cleanly."
        posture = (
            "- Keep it extremely brief (one sentence max).\n"
            "- Do not ask any follow-up questions."
        )
        allowed_to_ask = False
    elif act == SpeechAct.QUESTION:
        goal = "DEVELOP"
        stance = "DIRECT"
        action_plan = "Answer the question directly, accurately, and concisely."
        posture = (
            "- Start with the direct answer, then provide necessary context.\n"
            "- Do not use filler or preambles.\n"
            "- Only ask a follow-up question if it genuinely adds value or refines the answer."
        )
        allowed_to_ask = True
    elif act == SpeechAct.REQUEST:
        goal = "EXECUTE"
        stance = "DIRECT"
        action_plan = "Identify the action needed. Execute tools cleanly if available. Otherwise request clarification."
        posture = (
            "- Focus on executing the task accurately.\n"
            "- Maintain professional capability and direct feedback.\n"
            "- Avoid conversational chitchat while executing tools."
        )
        allowed_to_ask = True
    elif act == SpeechAct.STORY:
        goal = "DEVELOP"
        stance = "WARM"
        action_plan = "React to the narrative content with interest/emotion first before shifting to any utility."
        posture = (
            "- Match the emotion of the story (humor, surprise, concern).\n"
            "- Encourage them to continue telling it if there is more.\n"
            "- Do not pivot to assistant tasks or questions until they are finished."
        )
        allowed_to_ask = True
    elif act == SpeechAct.JOKE:
        goal = "DEVELOP"
        stance = "LIGHT"
        action_plan = "Appreciate the joke, play along, or share a brief lighthearted comment."
        posture = (
            "- Laugh or banter naturally.\n"
            "- Do not analyze or explain the joke.\n"
            "- Keep it brief and friendly."
        )
        allowed_to_ask = False
    elif act == SpeechAct.CORRECTION:
        goal = "REPAIR"
        stance = "PATIENT"
        action_plan = "Acknowledge the correction and adapt immediately without defending prior errors."
        posture = (
            "- Say 'Understood' or 'My mistake' and adjust course instantly.\n"
            "- Avoid saying 'Sorry' repeatedly; just fix the behavior."
        )
        allowed_to_ask = True
    elif act == SpeechAct.OPINION:
        goal = "DEVELOP"
        stance = "WARM"
        action_plan = "Acknowledge their perspective thoughtfully and briefly contribute to the discussion."
        posture = (
            "- Validate their stance or point.\n"
            "- Share a small, interesting observation.\n"
            "- Keep it collaborative and non-argumentative."
        )
        allowed_to_ask = True
    elif act == SpeechAct.TOPIC_CHANGE:
        goal = "DEVELOP"
        stance = "WARM"
        action_plan = "Follow the user's shift in topic seamlessly."
        posture = (
            "- Do not force the user back to previous topics unless a critical task was left unfinished.\n"
            "- Acknowledge the transition and engage with the new topic immediately."
        )
        allowed_to_ask = True
    elif act == SpeechAct.CONTINUATION:
        goal = "DEVELOP"
        stance = "WARM"
        action_plan = "Resolve referential/short phrasing against recent turns and continue the active thread."
        posture = (
            "- Do not start from scratch; build directly on the context.\n"
            "- Reference the existing active topic and progress it."
        )
        allowed_to_ask = True

    return ResponseStrategy(
        primary_goal=goal,
        emotional_stance=stance,
        action_plan=action_plan,
        suggested_posture=posture,
        allowed_to_ask=allowed_to_ask,
    )


def format_strategy_for_context(strategy: ResponseStrategy) -> str:
    """Format the strategy into a prompt block for LLM guidance."""
    lines = [
        "[RESPONSE STRATEGY (ADHERE STRICTLY)]",
        f"Primary Goal: {strategy.primary_goal}",
        f"Emotional Stance: {strategy.emotional_stance}",
        f"Action Plan: {strategy.action_plan}",
        "Conversational Posture Guidance:",
        strategy.suggested_posture,
    ]
    if not strategy.allowed_to_ask:
        lines.append("- DO NOT ASK ANY QUESTIONS in your response.")
    return "\n".join(lines)
