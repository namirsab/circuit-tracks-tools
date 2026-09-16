"""Listening state machine: push-to-talk, toggle, open microphone."""

from __future__ import annotations

from circuit_voice.listen import ListenController, key_name


def make(mode, **kw):
    events = []
    ctl = ListenController(mode, key="alt_r", max_utterance_s=5, on_event=events.append, **kw)
    return ctl, events


def test_ptt_hold_and_release():
    ctl, events = make("ptt")
    assert ctl.forwarding is False
    ctl.key_down("alt_r")
    ctl.key_down("alt_r")  # key repeat
    assert ctl.forwarding is True
    ctl.key_up("alt_r")
    assert ctl.forwarding is False
    assert events == ["listen_start", "listen_stop"]


def test_other_keys_are_ignored():
    ctl, events = make("ptt")
    ctl.key_down("space")
    ctl.key_up("space")
    assert events == [] and ctl.forwarding is False


def test_toggle_press_press_and_auto_stop():
    now = [100.0]
    ctl, events = make("toggle", clock=lambda: now[0])
    ctl.key_down("alt_r")
    ctl.key_up("alt_r")
    assert ctl.forwarding is True
    ctl.key_down("alt_r")
    ctl.key_up("alt_r")
    assert ctl.forwarding is False
    assert events == ["listen_start", "listen_stop"]
    ctl.key_down("alt_r")
    ctl.key_up("alt_r")
    now[0] += 4.0
    ctl.tick()
    assert ctl.forwarding is True
    now[0] += 1.5
    ctl.tick()
    assert ctl.forwarding is False
    assert events[-1] == "listen_stop"


def test_open_mode_always_forwards_and_ignores_keys():
    ctl, events = make("open")
    assert ctl.forwarding is True
    ctl.key_down("alt_r")
    ctl.key_up("alt_r")
    assert ctl.forwarding is True
    assert events == []


def test_set_mode_switch_stops_listening_cleanly():
    ctl, events = make("ptt")
    ctl.key_down("alt_r")
    ctl.set_mode("open")
    assert events == ["listen_start", "listen_stop"]
    assert ctl.mode == "open" and ctl.forwarding is True
    ctl.set_mode("toggle")
    assert ctl.forwarding is False


def test_key_name_from_pynput_like_objects():
    class K:
        name = "alt_r"

    class C:
        char = "x"

    assert key_name(K()) == "alt_r"
    assert key_name(C()) == "x"
    assert key_name(object()) is None


def test_check_permissions_reports_failure():
    def failing_factory(**kw):
        raise OSError("not trusted")

    ctl, _ = make("ptt", listener_factory=failing_factory)
    msg = ctl.start()
    assert msg is not None and "Bedienungshilfen" in msg
    ctl.stop()
