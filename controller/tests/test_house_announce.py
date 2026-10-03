"""The announce command is the utterance and nothing else."""

import em_house_announce as ha


def test_bare_command():
    assert ha.is_announce_command("announce")
    assert ha.is_announce_command("Announce.")
    assert ha.is_announce_command("make an announcement please")


def test_a_message_is_not_the_command():
    assert not ha.is_announce_command("announce dinner is ready")
    assert not ha.is_announce_command("set a timer")
    assert not ha.is_announce_command("")


def test_one_sentence_keeps_the_message():
    assert ha.announcement_body("announce dinner is ready") == "dinner is ready"
    assert ha.announcement_body("Announce.") is None
    assert ha.announcement_body("set a timer") is None


def test_speaker_rate_is_triple():
    pcm = (b"\x01\x00" * 16)
    out = ha.to_speaker(pcm)
    assert len(out) == len(pcm) * 3
