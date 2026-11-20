"""
Home Assistant's own failure is never spoken at the room.

An agent that cannot answer does not fail quietly: HA turns the exception
into the turn's reply and synthesises it. "Unable to get response." arrives
as a perfectly ordinary turn — INTENT_END, TTS_START, a TTS url, no error
event — so the only thing that can tell it apart is the reply text, which
TTS_START carries.

Four of these were played in three days on one Echo (2026-11-17, 2026-11-20),
twice after a command that had in fact been carried out.
"""

import _oww_stub

_oww_stub.install()
try:
    import em_agentfail
    import em_controller
    import em_esphome
finally:
    _oww_stub.uninstall()

from _sources import def_source  # noqa: E402
from esphome.vendor import api_pb2  # noqa: E402
from esphome.vendor.api_pb2 import VoiceAssistantEvent as ET  # noqa: E402


# ── The text test ───────────────────────────────────────────────────────────

def test_home_assistants_no_response_message_is_a_failure():
    # homeassistant/components/conversation/strings.json, key "no_response",
    # raised by util.async_get_result_from_chat_log.
    assert em_agentfail.is_failure("Unable to get response.")


def test_case_and_punctuation_do_not_matter():
    assert em_agentfail.is_failure("unable to get response")
    assert em_agentfail.is_failure("  Unable  to get response!  ")


def test_the_llm_integrations_own_apologies_are_failures():
    assert em_agentfail.is_failure(
        "Sorry, I had a problem getting a response from Google Generative AI."
    )
    assert em_agentfail.is_failure("Sorry, I had a problem talking to OpenAI")


def test_an_answer_is_not_a_failure():
    assert not em_agentfail.is_failure("It is twenty past four.")
    assert not em_agentfail.is_failure("Okay")
    # Said ABOUT the phrase rather than as the reply — the match is anchored
    # at the front, because HA replaces the answer, it never appends to one.
    assert not em_agentfail.is_failure(
        "When I cannot reach it I say unable to get response"
    )


def test_an_empty_reply_is_not_a_failure():
    # HA skips TTS entirely for an empty reply; em_esphome calls that
    # silent_reply and it has its own cue.
    assert not em_agentfail.is_failure("")
    assert not em_agentfail.is_failure(None)


# ── The turn test ───────────────────────────────────────────────────────────

def satellite() -> "em_esphome.EchoMuseSatellite":
    s = em_esphome.EchoMuseSatellite(
        device_id="G090LF0000000001", label="Test", mac_address="02:EC:00:00:00:01",
        oww_model_id="hey_jarvis_v0.1", on_disconnected_cb=lambda *_: None,
        oww_model_info=None)
    s._turn_active = True
    return s


def send(s, event_type, **data) -> None:
    msg = api_pb2.VoiceAssistantEventResponse(event_type=event_type)
    for name, value in data.items():
        msg.data.add(name=name, value=value)
    s._handle_voice_event(msg)


def up_to_tts(s, reply: str) -> None:
    send(s, ET.VOICE_ASSISTANT_RUN_START)
    send(s, ET.VOICE_ASSISTANT_STT_START)
    send(s, ET.VOICE_ASSISTANT_STT_END, text="close big window")
    send(s, ET.VOICE_ASSISTANT_INTENT_START)
    send(s, ET.VOICE_ASSISTANT_INTENT_END)
    send(s, ET.VOICE_ASSISTANT_TTS_START, text=reply)


def test_a_failure_reply_stops_the_turn_before_the_url_is_wanted():
    s = satellite()
    up_to_tts(s, "Unable to get response.")
    assert s._agent_failed == "Unable to get response."
    assert s._tts_event.is_set(), "the turn would sit out the whole TTS wait"
    assert not s._tts_audio_url


def test_a_url_arriving_afterwards_is_still_not_played():
    # TTS_END follows TTS_START even for a failure, carrying a url that
    # works. The turn must not fetch it.
    s = satellite()
    up_to_tts(s, "Unable to get response.")
    send(s, ET.VOICE_ASSISTANT_TTS_END, url="http://ha.local/x.wav")
    body = def_source("run_esphome_voice_turn")
    assert "if self._agent_failed:" in body
    assert body.index("if self._agent_failed:") < body.index("if self._tts_audio_url:")


def test_an_ordinary_reply_is_played():
    s = satellite()
    up_to_tts(s, "The big window is closed.")
    assert not s._agent_failed
    send(s, ET.VOICE_ASSISTANT_TTS_END, url="http://ha.local/x.wav")
    assert s._tts_audio_url == "http://ha.local/x.wav"


def test_the_turn_ends_on_the_error_cue():
    # Nothing is heard, so the ring carries the whole message.
    assert em_controller._OUTCOME_ANIM["agent_failed"] == "error_anim"


def test_the_reply_text_is_logged_on_every_turn():
    # The only place the satellite ever learns what was said.
    body = def_source("_handle_voice_event")
    assert 'TTS starting: {reply!r}' in body
