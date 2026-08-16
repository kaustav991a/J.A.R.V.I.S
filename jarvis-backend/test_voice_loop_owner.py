"""
test_voice_loop_owner.py — one microphone, one wake-word loop (F-11, R5)
=======================================================================

F-11 (live gate, 2026-08-08): the HUD started a SECOND wake-word loop when the
page was reloaded. The loop lives inside `websocket_endpoint`, so it started
once per connection — two threads in `wait_for_wake_word`, every `[VAD]`/`[STT]`
line printed twice, one spoken "wake up" running the boot sequence twice.

R5 (review, 2026-08-16): the fix for that had the opposite failure. The owner
sits blocked inside the mic thread, and starlette only moves `client_state` to
DISCONNECTED inside `receive()` — which the handler never called while
listening. So a reloaded HUD could not be seen to have replaced the old one:
the token was held by a dead socket forever, the new connection made ONE claim
attempt, lost, and parked view-only. **Reload while idle and the microphone is
dead while the HUD says `SYSTEM OFFLINE // STANDBY FOR VOICE INPUT`.**

The state machine now lives in `modules/voice_loop.py` and is imported and
driven directly here — the previous version of this harness lifted the two
helpers out of `main.py` with `ast` because importing `main` drags in pygame,
TensorFlow and MediaPipe. That trick is no longer needed for the behaviour; it
is still needed for the WIRING, which is asserted structurally at the bottom.

Both contracts are pinned, because they pull in opposite directions:
  * a LIVE second HUD must NOT get the microphone   (F-11)
  * a DEAD owner must NOT keep it                    (R5)
"""

import ast
import pathlib
import sys
import threading
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
MAIN = HERE / "main.py"

from modules.voice_loop import VoiceLoopOwnership, socket_is_live  # noqa: E402

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


class _State:
    """Stands in for `starlette.websockets.WebSocketState`."""

    def __init__(self, value):
        self.value = value


CONNECTED = 1
DISCONNECTED = 2


class _FakeSocket:
    """A socket the ownership code can read liveness off, as it does live."""

    def __init__(self, name, connected=True):
        self.name = name
        self.client_state = _State(CONNECTED if connected else DISCONNECTED)
        self.application_state = _State(CONNECTED)

    def die(self):
        """What the disconnect watcher's `receive()` does to a closed socket."""
        self.client_state = _State(DISCONNECTED)

    def __repr__(self):
        return f"<sock {self.name}>"


def _fresh():
    return VoiceLoopOwnership()


# ── F-11: exactly one loop ───────────────────────────────────────────────────

def test_first_connection_owns_the_loop():
    o = _fresh()
    check(o.claim(_FakeSocket("a")) is True, "first connection claims the loop")


def test_a_second_LIVE_connection_does_not_get_a_rival_loop():
    """The F-11 contract. Two tabs open is one microphone and one listener."""
    o = _fresh()
    a, b = _FakeSocket("a"), _FakeSocket("b")
    o.claim(a)
    check(o.claim(b) is False,
          "a second LIVE connection is refused the loop — this is F-11")
    check(o.owner is a, "the live owner keeps it")
    check(o.evictions == 0, "nothing was evicted")


def test_a_reload_storm_still_yields_exactly_one_owner():
    o = _fresh()
    owner = _FakeSocket("owner")
    o.claim(owner)
    granted = sum(1 for i in range(20) if o.claim(_FakeSocket(f"reload{i}")))
    check(granted == 0,
          f"20 further LIVE connections started 0 rival loops, got {granted}")


def test_claiming_twice_is_idempotent_for_the_owner():
    o = _fresh()
    a = _FakeSocket("a")
    check(o.claim(a) is True and o.claim(a) is True,
          "the owner re-claiming its own loop is not a refusal")


def test_a_non_owner_cannot_release_someone_elses_loop():
    o = _fresh()
    a, b = _FakeSocket("a"), _FakeSocket("b")
    o.claim(a)
    o.release(b)                       # b never owned it
    check(o.claim(b) is False,
          "a stale socket disconnecting does not hand the mic away from the live one")


def test_the_owner_disconnecting_frees_the_loop():
    o = _fresh()
    a, b = _FakeSocket("a"), _FakeSocket("b")
    o.claim(a)
    o.release(a)
    check(o.claim(b) is True,
          "next HUD to connect gets a working microphone after the owner leaves")


def test_reconnect_cycles_never_leak_ownership():
    o = _fresh()
    for i in range(10):
        s = _FakeSocket(f"cycle{i}")
        check(o.claim(s) is True, f"cycle {i}: reconnect claims the loop")
        o.release(s)
    check(o.owner is None,
          "no ownership left stranded after 10 connect/disconnect cycles")


def test_release_is_safe_to_call_twice():
    """It is called from the handler's finally AND from the watcher task."""
    o = _fresh()
    a, b = _FakeSocket("a"), _FakeSocket("b")
    o.claim(a)
    o.release(a)
    o.claim(b)
    o.release(a)                       # the late second release from a's finally
    check(o.owner is b,
          "a doubled release from the old owner does not steal the loop from the new one")


# ── R5: a dead owner is evicted ──────────────────────────────────────────────

def test_a_dead_owner_is_evicted():
    """THE BUG. The old connection is blocked in the mic thread, so its
    `finally` cannot run — the claim itself has to break the deadlock."""
    o = _fresh()
    old, new = _FakeSocket("old"), _FakeSocket("new")
    o.claim(old)
    check(o.claim(new) is False, "...refused while the old socket looks alive")
    old.die()                          # what the watcher's receive() observes
    check(o.claim(new) is True,
          "the reloaded HUD takes the loop from a socket whose client is gone")
    check(o.owner is new and o.evictions == 1, "and the eviction is counted")


def test_a_failed_send_also_counts_as_dead():
    """starlette flips `application_state` when send() hits an OSError and
    re-raises it as WebSocketDisconnect(1006). That is the other way a socket
    is seen to have gone, and it must count too."""
    o = _fresh()
    old, new = _FakeSocket("old"), _FakeSocket("new")
    o.claim(old)
    old.application_state = _State(DISCONNECTED)
    check(o.claim(new) is True, "an owner whose sends have failed is evicted")


def test_liveness_of_an_object_with_no_states_is_assumed():
    check(socket_is_live(object()) is True,
          "an object carrying no socket state reads as live, not as dead")


def test_a_view_only_connection_gets_the_loop_on_the_next_attempt():
    """The other half of R5: one attempt was all the new connection made.

    Drives the retry the endpoint now performs — the claim is re-attempted, so
    the handover happens the moment the old socket is observed to be gone.
    """
    o = _fresh()
    old, new = _FakeSocket("old"), _FakeSocket("new")
    o.claim(old)

    attempts = 0
    for i in range(5):
        if o.claim(new):
            break
        attempts += 1
        if i == 2:
            old.die()                  # the watcher fires mid-wait
    check(o.owner is new, f"the retrying connection ends up owning the loop")
    check(attempts == 3, f"and it took the attempts it should have; got {attempts}")


# ── R5: the DEVICE is handed over, not just the token ────────────────────────

def test_the_mic_interlock_tracks_the_blocking_call():
    o = _fresh()
    check(o.mic_is_free() is True, "no mic session, mic reads free")
    with o.mic_session():
        check(o.mic_is_free() is False, "inside the blocking call, mic reads busy")
    check(o.mic_is_free() is True, "and free again when the call returns")


def test_the_mic_interlock_releases_on_an_exception():
    o = _fresh()
    try:
        with o.mic_session():
            raise RuntimeError("the microphone fell over")
    except RuntimeError:
        pass
    check(o.mic_is_free() is True,
          "a mic call that raises still releases the device — otherwise one "
          "fault deafens every later connection")


def test_waiting_for_the_mic_times_out_rather_than_hanging():
    o = _fresh()
    with o.mic_session():
        t0 = time.monotonic()
        got = o.wait_for_mic_release(timeout=0.3)
        waited = time.monotonic() - t0
    check(got is False, "a wedged listener does not block the new HUD forever")
    check(0.25 <= waited < 2.0, f"it waited its timeout and no longer; {waited:.2f}s")


def test_the_full_reload_handover_never_runs_two_mic_threads():
    """The whole R5 sequence, with real threads.

    Old owner parked in the mic loop → its client goes → new connection claims
    (evicting it) → old thread notices it is no longer the owner and leaves the
    device → new connection's wait returns and it opens the mic. The counter
    asserts the property F-11 cares about: never two at once.
    """
    o = _fresh()
    old, new = _FakeSocket("old"), _FakeSocket("new")
    o.claim(old)

    inside = []
    peak = [0]
    lock = threading.Lock()
    stood_down = threading.Event()

    def mic_thread(sock, done):
        """A faithful miniature of wait_for_wake_word's loop."""
        with o.mic_session():
            with lock:
                inside.append(sock.name)
                peak[0] = max(peak[0], len(inside))
            try:
                while o.owns(sock):        # the should_abort predicate
                    time.sleep(0.02)
            finally:
                with lock:
                    inside.remove(sock.name)
        done.set()

    t_old = threading.Thread(target=mic_thread, args=(old, stood_down), daemon=True)
    t_old.start()
    time.sleep(0.1)
    check(not o.mic_is_free(), "the old owner is holding the microphone")

    old.die()                                     # the reload
    check(o.claim(new) is True, "the reloaded HUD evicts the dead owner")
    check(o.wait_for_mic_release(timeout=3.0) is True,
          "the old listener let go of the DEVICE, not just the token")
    check(stood_down.wait(1.0), "the old mic thread actually exited")

    new_done = threading.Event()
    t_new = threading.Thread(target=mic_thread, args=(new, new_done), daemon=True)
    t_new.start()
    time.sleep(0.1)
    check(not o.mic_is_free(), "the new owner is now listening")
    o.release(new)
    check(new_done.wait(1.0), "and stands down when released in turn")

    check(peak[0] == 1,
          f"never two threads on one microphone at any point; peak was {peak[0]}")


def test_ownership_reads_are_safe_under_concurrent_claims():
    """`owns()` is polled from the mic thread while the event loop claims and
    releases. A torn read here is a second wake-word loop."""
    o = _fresh()
    socks = [_FakeSocket(f"s{i}") for i in range(4)]
    stop = threading.Event()
    errors = []

    def churn():
        try:
            while not stop.is_set():
                for s in socks:
                    o.claim(s)
                    o.owns(s)
                    o.release(s)
        except Exception as e:            # noqa: BLE001 — the point is to see it
            errors.append(e)

    threads = [threading.Thread(target=churn, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    stop.set()
    for t in threads:
        t.join(2.0)
    check(not errors, f"no fault under concurrent claim/release; got {errors}")
    check(o.owner is None or o.owner in socks, "the token is never left corrupt")


# ── the mic loop actually stands down ────────────────────────────────────────

def _stub_speech_recognition(listen_calls, exited):
    """A speech_recognition stand-in: no hardware, records what was asked of it."""
    class _Mic:
        def __enter__(self):
            return "source"

        def __exit__(self, *exc):
            exited.append(True)
            return False

    class _Rec:
        energy_threshold = 0
        pause_threshold = 0.0
        dynamic_energy_threshold = False

        def adjust_for_ambient_noise(self, source, duration=1):
            pass

        def listen(self, source, timeout=5, phrase_time_limit=5):
            listen_calls.append(True)
            return "audio"

    mod = types.SimpleNamespace(
        Recognizer=_Rec, Microphone=_Mic,
        WaitTimeoutError=type("WaitTimeoutError", (Exception,), {}),
        UnknownValueError=type("UnknownValueError", (Exception,), {}),
    )
    return mod


def test_the_wake_loop_stands_down_without_taking_the_microphone_with_it():
    """Drives the REAL `wait_for_wake_word` with the abort predicate set.

    The important assertion is not the return value — it is that the
    `sr.Microphone()` context was left. Handing the token over while still
    holding the device is F-11 with extra steps.
    """
    import wakeword

    listen_calls, exited = [], []
    real_sr = wakeword.sr
    fake_engine = types.ModuleType("modules.wake_engine")
    fake_engine.has_human_speech = lambda audio: True
    sys.modules["modules.wake_engine"] = fake_engine
    wakeword.sr = _stub_speech_recognition(listen_calls, exited)
    try:
        out = wakeword.wait_for_wake_word(should_abort=lambda: True)
    finally:
        wakeword.sr = real_sr
        sys.modules.pop("modules.wake_engine", None)

    check(out == "", f"an aborted wait returns falsy, not a phrase; got {out!r}")
    check(listen_calls == [],
          "it stands down at the top of the window, before opening a listen")
    check(exited == [True],
          "and it LEFT the microphone context — the device is free for the new owner")


def test_the_wake_loop_is_unchanged_when_nobody_asks_it_to_stand_down():
    """The default is the old behaviour exactly: `should_abort=None` must not
    make the loop exit, or every ordinary boot would stop listening."""
    import wakeword

    listen_calls, exited = [], []
    real_sr, real_transcribe = wakeword.sr, wakeword._transcribe
    fake_engine = types.ModuleType("modules.wake_engine")
    fake_engine.has_human_speech = lambda audio: True
    sys.modules["modules.wake_engine"] = fake_engine
    wakeword.sr = _stub_speech_recognition(listen_calls, exited)
    wakeword._transcribe = lambda rec, audio: "wake up"
    try:
        out = wakeword.wait_for_wake_word()
    finally:
        wakeword.sr, wakeword._transcribe = real_sr, real_transcribe
        sys.modules.pop("modules.wake_engine", None)

    check(out == "wake up", f"a spoken wake word still boots him; got {out!r}")
    check(len(listen_calls) == 1, "and it listened exactly once to hear it")


# ── the wiring, asserted structurally ────────────────────────────────────────

_SOURCE = MAIN.read_text(encoding="utf-8", errors="replace")
_TREE = ast.parse(_SOURCE)


def _node(name):
    for n in ast.walk(_TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _calls_named(node, name):
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id == name for c in ast.walk(node))


def test_the_endpoint_actually_claims_before_listening():
    ep = _node("websocket_endpoint")
    check(ep is not None, "websocket_endpoint found in main.py")
    check(ep is not None and _calls_named(ep, "_claim_voice_loop"),
          "the endpoint calls _claim_voice_loop — without this the guard is dead code")


def test_the_endpoint_releases_in_a_finally():
    ep = _node("websocket_endpoint")
    released_in_finally = False
    for n in ast.walk(ep) if ep else []:
        if isinstance(n, ast.Try) and n.finalbody:
            if any(_calls_named(stmt, "_release_voice_loop") for stmt in n.finalbody):
                released_in_finally = True
    check(released_in_finally,
          "release happens in a finally — a crashing socket must not keep the mic forever")


def test_the_endpoint_retries_the_claim():
    """R5's other half. A single attempt is what left the reloaded HUD deaf."""
    ep = _node("websocket_endpoint")
    retried = False
    for n in ast.walk(ep) if ep else []:
        if isinstance(n, ast.While) and _calls_named(n.test, "_claim_voice_loop"):
            retried = True
    check(retried,
          "the losing connection re-attempts the claim rather than parking forever")


def test_the_endpoint_watches_for_its_own_disconnect():
    """Without a reader, `client_state` never leaves CONNECTED and a dead
    socket is indistinguishable from an idle one — which is the whole reason
    the token was never released."""
    ep = _node("websocket_endpoint")
    watcher = _node("_watch_for_disconnect")
    check(watcher is not None, "_watch_for_disconnect exists")
    check(ep is not None and _calls_named(ep, "_watch_for_disconnect"),
          "the endpoint starts a disconnect watcher for its own socket")
    check(watcher is not None and _calls_named(watcher, "_release_voice_loop"),
          "the watcher releases ownership itself — it must not wait for the "
          "handler's finally, which is blocked in the mic thread")
    receives = any(isinstance(c, ast.Attribute) and c.attr == "receive"
                   for c in ast.walk(watcher)) if watcher else False
    check(receives, "and it does so by actually calling receive()")


def test_the_endpoint_scopes_the_wake_wait_to_its_ownership():
    """The blocking call must be the ownership-aware wrapper, not the bare one
    — otherwise nothing can tell the mic thread to let go."""
    ep = _node("websocket_endpoint")
    wrapper = _node("_wake_word_for")
    check(wrapper is not None, "_wake_word_for exists")
    check(ep is not None and not _calls_named(ep, "wait_for_wake_word"),
          "the endpoint no longer calls the bare wait_for_wake_word")
    args = [a.arg for c in ast.walk(wrapper) if isinstance(c, ast.Call)
            for a in c.keywords] if wrapper else []
    check("should_abort" in args,
          "the wrapper passes should_abort into the mic loop")
    check(wrapper is not None and _calls_named(wrapper, "mic_session") or (
          wrapper is not None and "mic_session" in ast.dump(wrapper)),
          "and holds the hand-over interlock while it blocks")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 60)
    print("voice-loop ownership harness (F-11 + R5)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
