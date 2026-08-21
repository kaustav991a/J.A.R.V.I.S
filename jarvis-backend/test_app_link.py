"""Harness for /app-link — the phone's front door onto the cloud gateway.

The app half of this contract is already written and shipped (`src/link/` in
J.A.R.V.I.S-Mobile), so the properties below are not opinions about a design —
they are what that client already assumes, and each one is a way the phone
breaks if the gateway gets it wrong:

  1. an ungated socket is never opened. No token configured, no token presented,
     or a wrong token — all refused BEFORE accept, because this socket reaches a
     brain that answers as him;
  2. `/health` declares `app_link` only when the route can actually serve. The
     phone flips to CLOUD on that flag alone, and a 200 with a dead socket is
     worse for it than staying dark;
  3. bare text is a command — that is literally what `LinkMachine.send` writes —
     and a command that merely looks like JSON is still asked, not parsed;
  4. voice arrives as bytes or as a base64 envelope, and the transcript is
     attributed to HIM. A transcript sent as a status message would be logged in
     the phone's chat as J.A.R.V.I.S. having said it;
  5. a linked desk answers; a desk that is off or silent falls back to the cloud
     brain, and a turn the desk never saw is queued for it;
  6. desk frames belonging to a phone NEVER reach Telegram.

No network: `think`, the transcriber and the desk socket are all stubbed, and the
gateway's own module globals are neutered on import so nothing here can touch the
live bot (see `_neuter` below).
"""

import asyncio
import base64
import json
import os
import sys

# Set BEFORE the import so the module-level config picks these up. `.env` wins
# over os.environ (the gateway calls load_dotenv(override=True)), so this is a
# best-effort head start — `_neuter()` below is the part that actually holds.
os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

TOKEN = "harness-app-token"


def _neuter() -> None:
    """Make it impossible for this harness to reach the real bot.

    A blank BOT_TOKEN makes `_startup` return before `_ensure_bot`, and a blank
    PUBLIC_URL makes webhook registration impossible even if it did not. The
    TestClient is also never used as a context manager below, so no lifespan
    event fires at all — belt and braces, because a harness that re-points the
    live gateway's webhook would be a genuinely expensive mistake.
    """
    cg.BOT_TOKEN = ""
    cg.PUBLIC_URL = ""
    cg.MODE = "webhook"
    cg.APP_TOKEN = TOKEN
    cg.APP_KEEPALIVE_SECS = 3600.0     # off unless a test asks for it
    cg.APP_TELEMETRY_SECS = 3600.0
    cg._desk_ws = None
    cg._app_sinks.clear()
    cg._app_clients.clear()
    cg._IDENTITIES.clear()
    cg._IDENTITIES[1] = {"who": "Kaustav", "honorific": "Sir", "tier": cg._ADMIN_TIER}
    # Push addresses are persisted, so point the harness at its own file: a test
    # run must never write a phone into the set the live gateway would push to.
    cg._PUSH_FILE = os.path.join(os.path.dirname(os.path.abspath(cg.__file__)),
                                 "app_push_tokens.test.json")
    cg._push_targets.clear()
    cg._last_push_at = 0.0


_neuter()
client = TestClient(cg.app)

# What the stubbed brain says, so an assertion can tell a cloud answer from a
# desk one at a glance.
CLOUD_SAID = "cloud brain answering"
DESK_SAID = "desk brain answering"

_asked: list[tuple] = []
_queued: list[tuple] = []


# The stub MUST tolerate think()'s real signature growing, and the reason is not
# tidiness. When `think()` gained `context=` (the fix that stopped the model
# reciting his coordinates every turn), this stub still took four arguments. The
# call raised, so no `reply` frame was ever put in the sink, and `_drain`'s
# `receive_json()` below blocked FOREVER — wedging not just this test but the
# whole suite, silently, with no failure to read. `**_extra` is the guard: a
# signature drift must cost a wrong assertion, never an infinite wait.
async def _fake_think(chat_id, text, who, honorific, context="", **_extra):
    _asked.append((chat_id, text, who, honorific, context))
    return CLOUD_SAID


cg.think = _fake_think
cg._queue_offline_fact = lambda ident, text, reply: _queued.append((text, reply))


class FakeDeskWS:
    """Stands in for a linked desk on the other end of /desk-link.

    `send_json` is what the gateway calls; this pushes the desk's answer straight
    back into the sink the gateway registered for that req_id, which is the same
    queue the real desk-link reader writes to.
    """

    def __init__(self, *, reply=DESK_SAID, silent=False, notify=0, telemetry=None):
        self.reply = reply
        self.silent = silent
        self.notify = notify
        self.telemetry = telemetry
        self.sent: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.sent.append(frame)
        rid = frame.get("req_id")
        sink = cg._app_sinks.get(rid)
        if sink is None or self.silent:
            return
        if frame.get("type") == "hud_req":
            sink.put_nowait({"type": "hud", "req_id": rid, "data": self.telemetry or {}})
            return
        for _ in range(self.notify):
            sink.put_nowait({"type": "notify", "req_id": rid, "chat_id": frame.get("chat_id")})
        if self.reply is not None:
            sink.put_nowait({"type": "reply", "req_id": rid,
                             "chat_id": frame.get("chat_id"), "text": self.reply})
        sink.put_nowait({"type": "done", "req_id": rid})


def _open(token=TOKEN):
    url = "/app-link" if token is None else f"/app-link?token={token}"
    return client.websocket_connect(url)


def _drain(ws, *, want, limit=8):
    """Read frames until one satisfies `want`, or give up. Returns (hit, all)."""
    seen = []
    for _ in range(limit):
        frame = ws.receive_json()
        seen.append(frame)
        if want(frame):
            return frame, seen
    return None, seen


# ── auth ────────────────────────────────────────────────────────────────────

def test_a_gateway_with_no_app_token_refuses_every_connection():
    # The doc this route came from is explicit: do not ship an ungated /app-link,
    # because it reaches a brain that can answer as you.
    cg.APP_TOKEN = ""
    try:
        try:
            with _open("anything"):
                raise AssertionError("an ungated gateway accepted a phone")
        except WebSocketDisconnect:
            pass
    finally:
        cg.APP_TOKEN = TOKEN


def test_a_missing_token_is_refused():
    try:
        with _open(None):
            raise AssertionError("a tokenless phone was accepted")
    except WebSocketDisconnect:
        pass


def test_a_wrong_token_is_refused():
    try:
        with _open("not-the-token"):
            raise AssertionError("a forged token was accepted")
    except WebSocketDisconnect:
        pass


def test_health_declares_app_link_only_when_the_route_can_serve():
    # The phone reads exactly this flag and flips to CLOUD on it. Claiming it
    # while every connection would be refused strands the phone on a dead socket.
    cg.APP_TOKEN = ""
    try:
        assert client.get("/health").json()["app_link"] is False
    finally:
        cg.APP_TOKEN = TOKEN
    body = client.get("/health").json()
    assert body["app_link"] is True
    assert body["status"] == "ok"


# ── the cloud brain path (desk off) ─────────────────────────────────────────

def test_the_greeting_says_the_desk_is_off_when_no_desk_is_linked():
    cg._desk_ws = None
    with _open() as ws:
        first = ws.receive_json()
    assert first["status"] == "online"
    assert "desk" in first["message"].lower()
    assert first["user"] == "KAUSTAV"


def test_bare_text_is_a_command_and_the_cloud_brain_answers_it():
    cg._desk_ws = None
    _asked.clear()
    with _open() as ws:
        ws.receive_json()                       # greeting
        ws.send_text("what is the weather")
        hit, seen = _drain(ws, want=lambda f: f.get("status") == "speaking")
    assert hit is not None and hit["message"] == CLOUD_SAID
    assert [f["status"] for f in seen][:2] == ["thinking", "speaking"]
    assert _asked and _asked[-1][1] == "what is the weather"
    # its own memory thread, not Telegram's
    assert _asked[-1][0] == cg.APP_CHAT_ID


def test_a_turn_the_desk_never_saw_is_queued_for_it():
    cg._desk_ws = None
    _queued.clear()
    with _open() as ws:
        ws.receive_json()
        ws.send_text("remember I parked on level 3")
        _drain(ws, want=lambda f: f.get("status") == "speaking")
    assert _queued and _queued[-1] == ("remember I parked on level 3", CLOUD_SAID)


def test_the_session_returns_to_online_after_answering():
    cg._desk_ws = None
    with _open() as ws:
        ws.receive_json()
        ws.send_text("hello")
        hit, _ = _drain(ws, want=lambda f: f.get("status") == "online")
    assert hit is not None and hit["message"] == ""


def test_a_brain_fault_is_reported_rather_than_dropped():
    cg._desk_ws = None

    async def _boom(*a, **k):
        raise RuntimeError("groq is down")

    cg.think = _boom
    try:
        with _open() as ws:
            ws.receive_json()
            ws.send_text("hello")
            hit, _ = _drain(ws, want=lambda f: f.get("status") == "error")
    finally:
        cg.think = _fake_think
    # Same rule as the transcriber fault below: the operator gets a sentence, the
    # log gets the detail. "Reported rather than dropped" is still the property
    # under test; leaking "groq is down" into the bubble is not part of it.
    assert hit is not None and hit["message"].strip(), "the brain fault was dropped, not reported"
    assert "groq is down" not in hit["message"], (
        f"the provider's error leaked into the chat: {hit['message']!r}")


# ── the desk path ───────────────────────────────────────────────────────────

def test_a_linked_desk_answers_instead_of_the_cloud_brain():
    desk = FakeDeskWS()
    cg._desk_ws = desk
    _asked.clear()
    try:
        with _open() as ws:
            ws.receive_json()
            ws.send_text("open my inbox")
            hit, _ = _drain(ws, want=lambda f: f.get("status") == "speaking")
    finally:
        cg._desk_ws = None
    assert hit is not None and hit["message"] == DESK_SAID
    assert not _asked, "the cloud brain answered while a desk was linked"
    cmd = desk.sent[-1]
    assert cmd["type"] == "cmd" and cmd["text"] == "open my inbox"
    # the desk keys persona and memory off an UPPERCASE user string
    assert cmd["user"] == "KAUSTAV" and cmd["honorific"] == "Sir"


def test_a_phone_command_never_carries_a_real_telegram_chat_id():
    # Its own conversation on the desk, and an id the Telegram relay refuses.
    desk = FakeDeskWS()
    cg._desk_ws = desk
    try:
        with _open() as ws:
            ws.receive_json()
            ws.send_text("status")
            _drain(ws, want=lambda f: f.get("status") == "speaking")
    finally:
        cg._desk_ws = None
    assert desk.sent[-1]["chat_id"] == cg.APP_CHAT_ID
    assert cg.APP_CHAT_ID < 0


def test_a_silent_desk_falls_back_to_the_cloud_brain():
    # A connected-but-wedged desk must not black-hole the phone, exactly as it
    # must not black-hole a Telegram message.
    cg._desk_ws = FakeDeskWS(silent=True)
    real_timeout = cg._DESK_REPLY_TIMEOUT
    cg._DESK_REPLY_TIMEOUT = 0.4
    _asked.clear()
    try:
        with _open() as ws:
            ws.receive_json()
            ws.send_text("anything")
            hit, _ = _drain(ws, want=lambda f: f.get("status") == "speaking")
    finally:
        cg._DESK_REPLY_TIMEOUT = real_timeout
        cg._desk_ws = None
    assert hit is not None and hit["message"] == CLOUD_SAID
    assert _asked, "nothing reached the cloud brain after the desk went quiet"


def test_a_desk_that_only_heartbeats_keeps_the_phone_in_thinking():
    # notify frames are signs of life: a long command that shows them must not be
    # double-answered by the fallback.
    cg._desk_ws = FakeDeskWS(notify=2)
    try:
        with _open() as ws:
            ws.receive_json()
            ws.send_text("run the long one")
            hit, seen = _drain(ws, want=lambda f: f.get("status") == "speaking", limit=10)
    finally:
        cg._desk_ws = None
    assert hit is not None and hit["message"] == DESK_SAID
    assert [f["status"] for f in seen].count("thinking") >= 3


def test_a_desk_reply_belonging_to_a_phone_is_not_relayed_to_telegram():
    # The property, stated directly: while a phone's request is in flight its
    # req_id is registered as a sink, and the desk-link reader hands frames to
    # that sink and stops before the Telegram relay.
    seen_sink = {}

    class Watcher(FakeDeskWS):
        async def send_json(self, frame):
            seen_sink["registered"] = frame.get("req_id") in cg._app_sinks
            await super().send_json(frame)

    cg._desk_ws = Watcher()
    try:
        with _open() as ws:
            ws.receive_json()
            ws.send_text("hello")
            _drain(ws, want=lambda f: f.get("status") == "speaking")
    finally:
        cg._desk_ws = None
    assert seen_sink.get("registered") is True
    assert not cg._app_sinks, "a sink outlived its request"


def test_desk_vitals_reach_the_phone_and_are_never_invented():
    cg._desk_ws = FakeDeskWS(telemetry={"cpu_percent": 12.5, "ram_percent": 48.0})
    cg.APP_TELEMETRY_SECS = 0.05
    try:
        with _open() as ws:
            hit, _ = _drain(ws, want=lambda f: f.get("type") == "telemetry", limit=6)
    finally:
        cg.APP_TELEMETRY_SECS = 3600.0
        cg._desk_ws = None
    assert hit is not None
    assert hit["status"] == "sync" and hit["data"]["cpu_percent"] == 12.5

    # …and with no desk there are no numbers at all, rather than made-up ones.
    cg._desk_ws = None
    cg.APP_TELEMETRY_SECS = 0.05
    try:
        with _open() as ws:
            ws.receive_json()
            ws.send_text("ping")
            _, seen = _drain(ws, want=lambda f: f.get("status") == "online" and not f.get("message"))
    finally:
        cg.APP_TELEMETRY_SECS = 3600.0
    assert not [f for f in seen if f.get("type") == "telemetry"]


# ── voice ───────────────────────────────────────────────────────────────────

def test_a_voice_clip_sent_as_bytes_is_transcribed_and_answered():
    cg._desk_ws = None
    heard = []
    cg._groq_transcribe = lambda audio, filename="voice.ogg": (
        heard.append((audio, filename)) or "kal ki hobe"
    )
    with _open() as ws:
        ws.receive_json()
        ws.send_bytes(b"RIFFfake-audio")
        hit, seen = _drain(ws, want=lambda f: f.get("status") == "speaking")
    assert heard and heard[-1][0] == b"RIFFfake-audio"
    assert hit is not None and hit["message"] == CLOUD_SAID
    transcript = [f for f in seen if f.get("type") == "transcript"]
    assert transcript and transcript[0]["text"] == "kal ki hobe"


def test_a_voice_clip_sent_as_a_base64_envelope_carries_its_format():
    cg._desk_ws = None
    heard = []
    cg._groq_transcribe = lambda audio, filename="voice.ogg": (
        heard.append((audio, filename)) or "hello there"
    )
    payload = json.dumps({"type": "voice", "format": "m4a",
                          "audio": base64.b64encode(b"clip").decode()})
    with _open() as ws:
        ws.receive_json()
        ws.send_text(payload)
        hit, _ = _drain(ws, want=lambda f: f.get("status") == "speaking")
    assert heard and heard[-1] == (b"clip", "voice.m4a")
    assert hit is not None


def test_the_transcript_is_attributed_to_him_not_to_jarvis():
    # A transcript sent as a status message would be appended to the phone's chat
    # log as J.A.R.V.I.S. having said it — a lie about who spoke.
    cg._desk_ws = None
    cg._groq_transcribe = lambda audio, filename="voice.ogg": "turn on the lights"
    with _open() as ws:
        ws.receive_json()
        ws.send_bytes(b"clip")
        hit, _ = _drain(ws, want=lambda f: f.get("type") == "transcript")
    assert hit is not None
    assert "status" not in hit and hit["user"] == "KAUSTAV"


def test_an_unintelligible_clip_says_so_instead_of_asking_the_brain():
    cg._desk_ws = None
    _asked.clear()
    cg._groq_transcribe = lambda audio, filename="voice.ogg": "   "
    with _open() as ws:
        ws.receive_json()
        ws.send_bytes(b"silence")
        hit, _ = _drain(ws, want=lambda f: f.get("status") == "error")
    assert hit is not None and "hear" in hit["message"].lower()
    assert not _asked


def test_a_transcriber_fault_is_reported_and_the_socket_survives():
    cg._desk_ws = None

    def _boom(audio, filename="voice.ogg"):
        raise RuntimeError("whisper key exhausted")

    cg._groq_transcribe = _boom
    with _open() as ws:
        ws.receive_json()
        ws.send_bytes(b"clip")
        hit, _ = _drain(ws, want=lambda f: f.get("status") == "error")
        # still usable afterwards
        ws.send_text("plain text still works")
        after, _ = _drain(ws, want=lambda f: f.get("status") == "speaking")
    # Reported, and reported WITHOUT the provider's words. This used to assert
    # that "whisper key exhausted" reached the bubble; `_excuse` deliberately
    # stopped doing that, because a provider's error object in a persistent chat
    # log is the right information for whoever runs this and the wrong
    # information for whoever is talking to it.
    assert hit is not None and hit["message"].strip(), "the fault was dropped, not reported"
    assert "whisper key exhausted" not in hit["message"], (
        f"the provider's error leaked into the chat: {hit['message']!r}")
    assert after is not None and after["message"] == CLOUD_SAID


# ── the envelope parser ─────────────────────────────────────────────────────

# Compared against `cg.AppMessage`, NOT a bare tuple. These four asserted
# 3-tuples and broke the day `photo` was added for the camera feature — the
# gateway was right and the test was stale, but a 4-field NamedTuple simply is
# not equal to a 3-tuple, so it read as six real failures. Constructing the
# NamedTuple lets its defaults fill the fields this test does not care about, so
# the next defaulted field costs nothing here.

def test_bare_text_stays_a_command():
    assert cg._decode_app_message("  what time is it  ") == cg.AppMessage("what time is it", None, "")


def test_a_command_that_merely_looks_like_json_is_still_asked():
    # Someone pasting a JSON snippet at J.A.R.V.I.S. is asking about it.
    raw = '{"broken": '
    assert cg._decode_app_message(raw) == cg.AppMessage(raw.strip(), None, "")
    unknown = '{"type": "something-else", "x": 1}'
    assert cg._decode_app_message(unknown) == cg.AppMessage(unknown, None, "")


def test_an_explicit_command_envelope_is_unwrapped():
    assert cg._decode_app_message('{"type": "cmd", "text": "lock the pc"}') == cg.AppMessage("lock the pc", None, "")


def test_a_voice_envelope_yields_bytes_and_a_filename():
    raw = json.dumps({"type": "voice", "format": ".ogg",
                      "audio": base64.b64encode(b"xyz").decode()})
    assert cg._decode_app_message(raw) == cg.AppMessage("", b"xyz", "voice.ogg")


def test_a_voice_envelope_defaults_its_format():
    raw = json.dumps({"type": "voice", "audio": base64.b64encode(b"xyz").decode()})
    assert cg._decode_app_message(raw)[2] == "voice.m4a"


# ── identity ────────────────────────────────────────────────────────────────

def test_the_phone_speaks_as_the_admin_identity():
    assert cg._app_identity()["tier"] == cg._ADMIN_TIER


def test_a_gateway_with_no_identities_still_serves_the_phone():
    # The app must work on a gateway wired for nothing but this socket.
    saved = dict(cg._IDENTITIES)
    cg._IDENTITIES.clear()
    try:
        ident = cg._app_identity()
    finally:
        cg._IDENTITIES.update(saved)
    assert ident["who"] and ident["tier"] == cg._ADMIN_TIER


# ── keepalive ───────────────────────────────────────────────────────────────

def test_the_keepalive_carries_no_message_so_it_cannot_write_a_chat_line():
    # The phone re-probes after 30s without a frame; the keepalive exists only to
    # refresh that clock, and a message would land in the chat log every 20s.
    cg._desk_ws = None
    cg.APP_KEEPALIVE_SECS = 0.05
    try:
        with _open() as ws:
            ws.receive_json()                   # greeting
            beats = [ws.receive_json() for _ in range(2)]
    finally:
        cg.APP_KEEPALIVE_SECS = 3600.0
    assert all(b["status"] == "online" and b["message"] == "" for b in beats)


def test_the_desk_bridge_answers_a_vitals_request():
    # The desk half of the same round trip, exercised through the real handler.
    from modules import cloud_bridge

    sent = []

    class WS:
        async def send(self, raw):
            sent.append(json.loads(raw))

    asyncio.run(cloud_bridge._handle_hud_req(WS(), asyncio.Lock(), {"type": "hud_req", "req_id": 7}))
    assert sent and sent[0]["type"] == "hud" and sent[0]["req_id"] == 7
    assert isinstance(sent[0]["data"], dict) and "cpu_percent" in sent[0]["data"]


class _Phone:
    """A phone socket, for the announcement tests. `_announce_desk` only ever
    calls `send_json` on the members of `_app_clients`."""

    def __init__(self, dead: bool = False):
        self.dead = dead
        self.got: list = []

    async def send_json(self, payload: dict) -> None:
        if self.dead:
            raise RuntimeError("socket is gone")
        self.got.append(payload)


def test_desk_arrival_is_announced_as_a_frame_the_app_parses():
    """`{"type":"desk","linked":true}` — the shape `parseFrame` reads.

    Without this the phone learns the desk is up only by re-dialling, so a
    session that began as cloud-only stayed labelled cloud-only while quietly
    having gained PC control.
    """
    phone = _Phone()
    cg._app_clients.add(phone)
    asyncio.run(cg._announce_desk(True))
    assert phone.got == [{"type": "desk", "linked": True}]

    phone.got.clear()
    asyncio.run(cg._announce_desk(False))
    assert phone.got == [{"type": "desk", "linked": False}]


def test_a_phone_holding_a_socket_is_never_also_pushed():
    """One event, one notification.

    The app raises its own local notification from the frame. A push on top of
    that is the same news twice, which you feel in your pocket.
    """
    pushes: list = []
    original = cg._push_all

    async def _spy(title, body, data=None, kind="general", force=False):
        pushes.append(title)

    cg._push_all = _spy
    try:
        cg._push_targets["ExponentPushToken[seed]"] = "android"
        phone = _Phone()
        cg._app_clients.add(phone)
        asyncio.run(cg._announce_desk(True))
        assert pushes == []
        # ...and with nobody listening, the same event does push
        cg._app_clients.clear()
        asyncio.run(cg._announce_desk(True))
        assert pushes == ["J.A.R.V.I.S. is on full power"]
        # losing the desk is a quiet downgrade, never a notification
        asyncio.run(cg._announce_desk(False))
        assert len(pushes) == 1
    finally:
        cg._push_all = original


def test_a_dead_phone_socket_is_dropped_rather_than_raised_through():
    """One phone that has gone away must not stop the others being told."""
    dead, live = _Phone(dead=True), _Phone()
    cg._app_clients.update({dead, live})
    asyncio.run(cg._announce_desk(True))
    assert dead not in cg._app_clients
    assert live.got == [{"type": "desk", "linked": True}]


def test_a_desk_watch_alert_reaches_a_phone_that_is_asleep():
    """The one alert that must not depend on the app being open.

    It travelled only down the phone's socket, so it could reach a running app
    and nothing else — and the phone is in a pocket exactly when someone is at
    the desk. The desk locks itself when its own countdown expires whether or not
    anyone answered, so an unseen alert is the desk's silence deciding.
    """
    pushes: list = []
    original = cg._push_all

    async def _spy(title, body, data=None, kind="general", force=False):
        pushes.append({"title": title, "body": body, "data": data,
                       "kind": kind, "force": force})

    cg._push_all = _spy
    try:
        cg._push_targets["ExponentPushToken[x]"] = "android"
        alert = {"type": "intruder", "id": "a1", "expires_in": 30,
                 "user": "KAUSTAV", "trigger": "unlock", "image": "/shot.jpg"}

        # nobody attached — it goes out as a push
        asyncio.run(cg._relay_watch(alert))
        assert len(pushes) == 1
        # the interrupting kind, and not rate-limited: refusing a lock warning
        # because a status notification fired four minutes ago is the wrong trade.
        # `watch` is the logical name; `_channel_for` maps it to desk-watch-v2
        assert pushes[0]["kind"] == "watch"
        assert pushes[0]["force"] is True
        assert pushes[0]["data"]["id"] == "a1"

        # a phone holding a socket gets the frame verbatim and is NOT pushed:
        # `parseFrame` reads this shape already, and one event must not arrive twice
        pushes.clear()
        phone = _Phone()
        cg._app_clients.add(phone)
        asyncio.run(cg._relay_watch(alert))
        assert phone.got == [alert]
        assert pushes == []

        # a resolution closes the window, so there is nothing left to answer
        cg._app_clients.clear()
        asyncio.run(cg._relay_watch({"type": "intruder_resolved", "id": "a1",
                                     "outcome": "locked"}))
        assert pushes == []
    finally:
        cg._push_all = original


def test_push_registration_is_gated_by_the_pairing_token():
    """The push address is what gets told the desk is up. Same credential as the
    socket, because it reaches the same phone."""
    body = {"push_token": "ExponentPushToken[abc]", "platform": "android"}
    assert client.post("/app-push/register", json=body).status_code == 401
    assert client.post("/app-push/register", json=body,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401

    good = {"Authorization": f"Bearer {TOKEN}"}
    res = client.post("/app-push/register", json=body, headers=good)
    assert res.status_code == 200 and res.json()["targets"] == 1
    # the same install registering again is not a second phone
    assert client.post("/app-push/register", json=body,
                       headers=good).json()["targets"] == 1
    assert client.get("/health").json()["push_targets"] == 1

    # an address that is not an address is refused rather than stored
    assert client.post("/app-push/register", json={"push_token": "  "},
                       headers=good).status_code == 400


# ── The commute schedule, and why the gateway must hold it ───────────────────
# The morning briefing was local to the phone, and measured on the device on
# 2026-08-20 it cannot be: `expo-background-task` requires a connected network on
# every run and the app's uid reads
# `Network: 108 (blocked=REASON_APP_BACKGROUND|REASON_APP_STANDBY)` with
# `#netAvail=0` in a RARE standby bucket. Logcat caught the pending worker firing
# 200ms after a cold launch — so the app was the only thing that could unblock its
# own briefing, exactly as reported: "it arrives after I open the app".
#
# These pin the contract the phone already sends against. The scheduler that reads
# the stored schedule is not built yet; refusing a schedule it cannot act on is
# the half that must be right first.
GOOD_COMMUTE = {
    "tz": "Asia/Kolkata",
    "days": [False, True, True, True, True, True, False],
    "departures": [
        {"place_id": "office", "label": "Office", "hour": 19, "minute": 0,
         "lat": 22.5726, "lon": 88.3639},
    ],
}


def test_the_commute_schedule_is_gated_by_the_pairing_token():
    """It decides when a notification wakes him up. Same credential as the rest."""
    assert client.post("/app-commute", json=GOOD_COMMUTE).status_code == 401
    assert client.post("/app-commute", json=GOOD_COMMUTE,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401

    good = {"Authorization": f"Bearer {TOKEN}"}
    res = client.post("/app-commute", json=GOOD_COMMUTE, headers=good)
    assert res.status_code == 200 and res.json()["departures"] == 1
    said = client.get("/health").json()["commute"]
    assert said["tz"] == "Asia/Kolkata" and said["departures"] == 1 and said["days_on"] == 5


def test_a_schedule_replaces_the_last_one_rather_than_adding_to_it():
    """Switching a departure off has to be able to SILENCE the gateway.

    Merging would leave a briefing firing on a schedule the operator had already
    turned off — the one failure this feature cannot afford, because it teaches
    him to distrust every notification the app sends.
    """
    good = {"Authorization": f"Bearer {TOKEN}"}
    client.post("/app-commute", json=GOOD_COMMUTE, headers=good)
    off = dict(GOOD_COMMUTE, departures=[])
    res = client.post("/app-commute", json=off, headers=good)
    assert res.status_code == 200 and res.json()["departures"] == 0
    assert client.get("/health").json()["commute"]["departures"] == 0


def test_a_schedule_that_cannot_be_trusted_is_refused_whole():
    """Not repaired, not partially stored.

    A schedule the gateway understood differently from the phone would fire at a
    time nobody chose. Every case below is a 400 rather than a best guess.
    """
    good = {"Authorization": f"Bearer {TOKEN}"}
    for bad in (
        {},                                                   # nothing at all
        dict(GOOD_COMMUTE, tz=""),                             # no zone: cannot schedule
        dict(GOOD_COMMUTE, tz="   "),
        dict(GOOD_COMMUTE, days=[True] * 6),                   # six days is not a week
        dict(GOOD_COMMUTE, days="weekdays"),                   # not a list at all
        dict(GOOD_COMMUTE, departures="office at seven"),
    ):
        assert client.post("/app-commute", json=bad,
                           headers=good).status_code == 400, bad


def test_an_unusable_departure_is_dropped_without_failing_the_schedule():
    """One bad row must not silence a good one.

    The phone filters before sending, so a row like these means the two sides
    disagree — but refusing the whole upload would take the working departure down
    with it, and the operator would get nothing with no way to tell why.
    """
    good = {"Authorization": f"Bearer {TOKEN}"}
    mixed = dict(GOOD_COMMUTE, departures=[
        GOOD_COMMUTE["departures"][0],
        {"place_id": "home", "label": "Home", "hour": 25, "minute": 0,     # no such hour
         "lat": 22.5, "lon": 88.3},
        {"place_id": "moon", "label": "Moon", "hour": 8, "minute": 0,      # no such place
         "lat": 999.0, "lon": 0.0},
        {"place_id": "", "label": "Nowhere", "hour": 8, "minute": 0,       # no id
         "lat": 22.5, "lon": 88.3},
    ])
    res = client.post("/app-commute", json=mixed, headers=good)
    assert res.status_code == 200 and res.json()["departures"] == 1


# ── Expo's answer is per-token, and it used to be thrown away ────────────────
# Pre-Electron review, 2026-08-15. `_push_all` printed 400 characters of Expo's
# reply and moved on. Expo returns HTTP 200 even when it accepted nothing — the
# verdict is in `data[i].status`, in the order the tokens were sent — so a push
# that reached nobody was logged exactly like one that worked. It matters most
# on the one path that cannot afford it: the desk-watch alert is a 30-second
# window on whether the machine locks itself.

def _push_replying(tickets):
    """Stub Expo, returning the given per-token tickets."""
    def _fake(tokens, title, body, data, kind="general"):
        return {"data": tickets}
    return _fake


def _run_push(tickets, targets):
    saved = []
    orig_expo, orig_save = cg._expo_push_blocking, cg._save_push_targets
    cg._expo_push_blocking = _push_replying(tickets)
    cg._save_push_targets = lambda: saved.append(dict(cg._push_targets))
    cg._push_targets.clear()
    cg._push_targets.update(targets)
    cg._last_push_at = 0.0
    try:
        asyncio.run(cg._push_all("t", "b", force=True))
    finally:
        cg._expo_push_blocking, cg._save_push_targets = orig_expo, orig_save
    return saved


def test_an_unregistered_push_target_is_pruned_not_pushed_forever():
    # A reinstall issues a new Expo token and the old one answers
    # DeviceNotRegistered for the rest of time. Unpruned, the registry grows on
    # every reinstall and each push pays for an address that cannot answer.
    saved = _run_push(
        [{"status": "ok", "id": "1"},
         {"status": "error", "details": {"error": "DeviceNotRegistered"}}],
        {"ExponentPushToken[live]": "android",
         "ExponentPushToken[dead]": "android"},
    )
    assert "ExponentPushToken[dead]" not in cg._push_targets
    assert "ExponentPushToken[live]" in cg._push_targets
    assert saved, "the pruned registry must be persisted, not just held in memory"


def test_a_transient_push_error_does_not_prune_the_target():
    # Only DeviceNotRegistered is permanent. A rate-limit or a server fault must
    # not cost the address — that would turn a bad minute into a silent phone.
    _run_push(
        [{"status": "error", "details": {"error": "MessageRateExceeded"}}],
        {"ExponentPushToken[live]": "android"},
    )
    assert "ExponentPushToken[live]" in cg._push_targets


def test_an_unreadable_expo_reply_prunes_nothing():
    # Unparseable is not a verdict. Pruning on it would delete every target the
    # first time Expo returned an error page.
    orig = cg._expo_push_blocking
    cg._expo_push_blocking = lambda *a, **k: {"_unparsed": "<html>502</html>"}
    cg._push_targets.clear()
    cg._push_targets["ExponentPushToken[live]"] = "android"
    cg._last_push_at = 0.0
    try:
        asyncio.run(cg._push_all("t", "b", force=True))
    finally:
        cg._expo_push_blocking = orig
    assert "ExponentPushToken[live]" in cg._push_targets


def test_expo_blocking_returns_a_decoded_reply_not_a_truncated_string():
    # The truncation was the bug: 400 characters of JSON cannot be read for a
    # per-token status, so the caller had nothing to check even if it wanted to.
    import inspect
    src = inspect.getsource(cg._expo_push_blocking)
    assert "[:400]" not in src.split("_unparsed")[0], \
        "the success path must not truncate Expo's reply"
    assert "json.loads" in src


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        _neuter()
        cg.think = _fake_think
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
