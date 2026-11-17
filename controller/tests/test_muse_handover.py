"""Handing a turn from Home Assistant to Muse, driven through the real event
handler with the messages HA sends.

"Alexa, call Muse" reaches the controller as an ordinary transcript. The turn
is already running and HA is already working on an answer nobody wants, so the
handover has to stop that run, release the reply wait, and mark the turn as
Muse's — all from inside STT_END.
"""

import _oww_stub

_oww_stub.install()
try:
    import em_esphome
finally:
    _oww_stub.uninstall()

from esphome.vendor import api_pb2  # noqa: E402
from esphome.vendor.api_pb2 import VoiceAssistantEvent as ET  # noqa: E402


async def _never_called(device):
    raise AssertionError("the call handler should not have run")


def satellite(muse_call=_never_called) -> "em_esphome.EchoMuseSatellite":
    s = em_esphome.EchoMuseSatellite(
        device_id="G090LF0000000001", label="Test", mac_address="02:EC:00:00:00:01",
        oww_model_id="alexa_v0.1", on_disconnected_cb=lambda *_: None,
        oww_model_info=None)
    s._turn_active = True
    s._muse_call_cb = muse_call
    s.aborts = 0
    s.abort_ha_run = lambda: setattr(s, "aborts", s.aborts + 1)
    return s


def send(s, event_type, **data) -> None:
    msg = api_pb2.VoiceAssistantEventResponse(event_type=event_type)
    for name, value in data.items():
        msg.data.add(name=name, value=value)
    s._handle_voice_event(msg)


def speak(s, text: str) -> None:
    send(s, ET.VOICE_ASSISTANT_RUN_START)
    send(s, ET.VOICE_ASSISTANT_STT_START)
    send(s, ET.VOICE_ASSISTANT_STT_END, text=text)


def test_asking_for_muse_takes_the_turn_from_home_assistant():
    s = satellite()
    speak(s, "call Muse")
    assert s._muse_call, "the turn was left with Home Assistant"
    assert s.aborts == 1, "HA was left running an answer nobody will hear"
    assert s._tts_event.is_set(), (
        "the turn would sit out the whole reply timeout before calling Muse")


def test_jarvis_is_the_same_handover():
    s = satellite()
    speak(s, "Call Jarvis.")
    assert s._muse_call
    assert s.aborts == 1


def test_an_ordinary_command_is_left_alone():
    s = satellite()
    speak(s, "turn off the kitchen light")
    assert not s._muse_call
    assert s.aborts == 0
    assert not s._tts_event.is_set(), "HA was not given its chance to answer"


def test_a_sentence_that_merely_mentions_muse_is_left_alone():
    """A substring match would steal the turn here, and a stolen turn is worse
    than a handover the listener has to repeat."""
    s = satellite()
    speak(s, "ask Muse about the weather later")
    assert not s._muse_call
    assert s.aborts == 0


def test_without_a_pairing_the_phrase_is_an_ordinary_turn():
    """em_muselink.available() gates this: no SDK or no pairing means no
    handler, and "call Muse" must then behave like any other sentence rather
    than failing in front of the listener."""
    s = satellite(muse_call=None)
    speak(s, "call Muse")
    assert not s._muse_call
    assert s.aborts == 0
    assert not s._tts_event.is_set()


def test_a_house_announcement_still_wins():
    """The two interceptions share STT_END; announce was there first and must
    keep its turn."""
    s = satellite()
    speak(s, "announce")
    assert s._house_announce
    assert not s._muse_call
    assert s.aborts == 0


def test_a_bare_wake_word_is_never_answered_out_loud():
    """The conversation agent answers "Alexa." with a spoken failure, which
    wakes the next Echo in earshot. The turn ends quietly instead."""
    s = satellite()
    s._wake_word_phrase = "alexa"
    speak(s, "Alexa.")
    assert s._nothing_said
    assert s.aborts == 1, "HA was left to produce a reply nobody asked for"
    assert s._tts_event.is_set()
    assert not s._muse_call


def test_a_real_request_is_still_answered():
    s = satellite()
    s._wake_word_phrase = "alexa"
    speak(s, "Alexa, turn off the kitchen light")
    assert not s._nothing_said
    assert s.aborts == 0
    assert not s._tts_event.is_set()


def test_asking_for_muse_is_not_mistaken_for_silence():
    s = satellite()
    s._wake_word_phrase = "alexa"
    speak(s, "Call Jarvis.")
    assert not s._nothing_said
    assert s._muse_call
