"""Turns where nothing was actually said.

An Echo that wakes on noise, or on another Echo's reply, sends Home Assistant
a transcript holding only the wake word — "Alexa." — or nothing at all. The
conversation agent cannot answer that, so it says so out loud: "Unable to get
response", at full volume, which is then heard by any other Echo in earshot
and wakes it, and the pair talk to each other.

Nothing was asked, so nothing should be answered. The turn ends quietly
instead, and the spoken failure never happens.

Recognised on 2026-11-17 from a loop of eight turns between two Echoes, which
began with one of them transcribing 'Alexa.' and playing 2.27s of that reply.
"""

from __future__ import annotations

import re

# Words that carry no request on their own. A transcript made only of these,
# in any order, asked for nothing.
FILLER = frozenset({
    "a", "ah", "eh", "er", "erm", "hey", "hi", "hello", "huh", "mm", "mmm",
    "oh", "ok", "okay", "please", "so", "uh", "uhm", "um", "well", "yeah",
    "the", "and", "but",
})

_NOT_WORDS = re.compile(r"[^a-z0-9]+")


def words(text: str | None) -> list[str]:
    """A transcript as bare lower-case words."""
    if not text:
        return []
    return [w for w in _NOT_WORDS.sub(" ", text.lower()).split() if w]


def is_blank(text: str | None, wake_word: str | None = None) -> bool:
    """Whether this transcript asked for nothing at all.

    True for an empty transcript, for one holding only the wake word, and for
    one holding only the wake word and filler. Anything else is a real request
    — including a single word like "lights", which is a command the agent can
    reasonably be asked to interpret.

    `wake_word` is the phrase the satellite advertised ("alexa", "hey jarvis").
    Its words are removed before judging, because speech-to-text usually keeps
    the wake word at the front of what it heard.
    """
    said = words(text)
    if not said:
        return True
    for w in words(wake_word):
        if w in said:
            said.remove(w)
    return all(w in FILLER for w in said)
