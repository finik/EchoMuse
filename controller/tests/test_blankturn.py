"""A turn that asked nothing must not be answered out loud.

Two Echoes spent eight turns answering each other on 2026-11-17. It began
with one of them waking on noise, transcribing 'Alexa.', and the conversation
agent replying "Unable to get response" at full volume — which the Echo next
to it heard as a wake word, and so on.
"""

import pytest

import em_blankturn


class TestNothingWasSaid:
    @pytest.mark.parametrize("said", [
        "Alexa.", "alexa", "ALEXA", "Alexa, um", "um", "uh", "hey",
        "", "   ", None, "Alexa. Okay.",
    ])
    def test_blank(self, said):
        assert em_blankturn.is_blank(said, "alexa")

    @pytest.mark.parametrize("said", [
        "Alexa what time is it",
        "lights",                       # one word, but a real request
        "turn off the kitchen light",
        "Call Jarvis.",
        "Unable to get response.",      # the reply itself is not blank
    ])
    def test_a_real_request(self, said):
        assert not em_blankturn.is_blank(said, "alexa")

    def test_the_wake_word_is_the_one_that_fired(self):
        """Each satellite advertises its own, so the bare phrase is only
        blank for the Echo that uses it."""
        assert em_blankturn.is_blank("hey jarvis", "hey jarvis")
        assert not em_blankturn.is_blank("hey jarvis", "alexa")

    def test_a_button_turn_has_no_wake_word(self):
        """wake_word_phrase is "" for a button turn; a bare word is then
        still a request, and silence is still blank."""
        assert em_blankturn.is_blank("", "")
        assert not em_blankturn.is_blank("lights", "")

    def test_the_wake_word_is_removed_once_not_everywhere(self):
        """"Alexa, ask Alexa" keeps the second one, so it stays a request."""
        assert not em_blankturn.is_blank("Alexa ask Alexa", "alexa")

    def test_punctuation_and_case_do_not_matter(self):
        assert em_blankturn.is_blank("  ALEXA!!  ", "alexa")
