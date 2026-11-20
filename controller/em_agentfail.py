"""Replies that are Home Assistant apologising, not an answer.

A conversation agent that fails does not fail quietly: Home Assistant turns
the exception into the turn's spoken reply and runs it through text-to-speech
like any other sentence. The room hears "Unable to get response." at full
volume, and the satellite has no way to tell that apart from an answer — the
pipeline reports a perfectly ordinary turn, INTENT_END then TTS_START then a
TTS url, no error event anywhere (measured 2026-11-17 08:09 and 2026-11-20).

The commonest one is worth understanding, because it is usually NOT a
failure of the thing the person asked for:

    homeassistant/components/conversation/util.py
        if not isinstance((last_content := chat_log.content[-1]), AssistantContent):
            raise HomeAssistantError(translation_key="no_response")
    homeassistant/components/conversation/strings.json
        "no_response": {"message": "Unable to get response."}

The last entry in the chat log is a TOOL RESULT rather than a sentence: the
LLM called a Home Assistant tool — turned the light off, closed the cover —
and then stopped without writing a closing line. The command ran. All that is
missing is "Okay." So the right response to this reply is silence, not an
apology: the house already did what it was told.

Observed here on 'I need lights off.' (14.0s) and 'Close big window.' (15.9s)
— both long, because each is several tool round-trips — and on two garbled
transcripts. Each played 218400 bytes, 2.27s, of Home Assistant saying it had
nothing to say.

This module recognises those replies from their text alone. Pure, like
em_blankturn next door, so the suite can import it.
"""

from __future__ import annotations

import re

# The start of a reply that is the agent reporting its own failure. Matched
# against the front of the normalised reply, because every one of them is the
# whole reply — HA replaces the answer with the error, it does not append it.
#
# Each line is a string that exists in Home Assistant, not a guess:
#   conversation/strings.json                        no_response, process_failed
#   google_generative_ai_conversation/const.py       ERROR_GETTING_RESPONSE
#   openai_conversation, anthropic, ollama           the same shape
FAILURE_PREFIXES = (
    "unable to get response",
    "sorry i had a problem getting a response from",
    "sorry i had a problem talking to",
    "an unexpected error occurred",
    "sorry i had a problem with my template",
    "error preparing llm api",
)

_NOT_WORDS = re.compile(r"[^a-z0-9]+")


def normalise(text: str | None) -> str:
    """Lower-case, punctuation-free, single-spaced — matchable."""
    if not text:
        return ""
    return " ".join(w for w in _NOT_WORDS.sub(" ", text.lower()).split() if w)


def is_failure(text: str | None) -> bool:
    """
    Whether this reply is Home Assistant apologising rather than answering.

    An empty reply is not one: HA skips TTS entirely when the agent says
    nothing ("never mind"), and em_esphome already has a name for that.
    """
    said = normalise(text)
    if not said:
        return False
    return any(said.startswith(p) for p in FAILURE_PREFIXES)
