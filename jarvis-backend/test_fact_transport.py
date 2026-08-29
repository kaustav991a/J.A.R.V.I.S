"""Harness for the sealed fact QUEUE + BRIDGE TRANSPORT — C#11a Step 4, Phase 2.

Phase 1 proved the seal. This proves delivery, and delivery is where a queue
actually fails. The four properties that were signed off:

  1. a fact sealed while the bridge is DOWN waits in the cloud outbox and
     arrives after reconnect — the bridge being down is the NORMAL case here,
     because the desk being off is the whole reason a fact got queued;
  2. a re-drained backlog does not double-store — proved through the real
     `memory_manager.add_memory()` and the real `content_hash` blind index,
     not a stand-in;
  3. one poison record quarantines and the rest of the batch still drains;
  4. a bridge that drops MID-BATCH resumes with no loss and no duplication.

There is no network here and no real WebSocket: FakeLink is both ends of the
bridge in memory, and it calls the same functions cloud_gateway.desk_link and
cloud_bridge._session call. Keys, databases and the dead-letter store are all
redirected into a temp directory — the real jarvis_key.* files, the real
long-term memory and the real ledger are never touched.
"""

import asyncio
import ast
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import memory_manager as mm
from modules import fact_drain as fd
from modules import fact_outbox as fo
from modules import fact_seal as fs
from modules import memory_crypto as mc

# ── isolation ───────────────────────────────────────────────────────────────

_TMP = Path(tempfile.mkdtemp(prefix="jarvis_facttransport_"))
_REAL_PATHS = (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE)
_REAL_LEDGER, _REAL_OUTBOX = fd.LEDGER_DB, fo.OUTBOX_FILE
# Live-gate session 4: these two lines asserted the real files did not EXIST,
# which is only true on a machine where JARVIS has never run. The desk ran during
# the gate, stored a fact, and created `jarvis_fact_ledger.db` — so three
# harnesses went red for the one reason that is not a defect: the product had been
# used. What they are actually for is proving this harness writes to its temp dir
# and not to the operator's data, so that is what they check now — a fingerprint
# taken at import, compared at the end. An untouched file passes whether or not it
# exists; a harness that writes to the real path still fails.
def _fingerprint(p):
    try:
        st = p.stat()
        return (True, st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return (False, 0, 0)


_REAL_LEDGER_FP = _fingerprint(_REAL_LEDGER)
_REAL_OUTBOX_FP = _fingerprint(_REAL_OUTBOX)


def _fingerprint_real_keys():
    return {p: (hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None)
            for p in _REAL_PATHS}


_REAL_KEYS_BEFORE = _fingerprint_real_keys()

mc.DPAPI_KEY_FILE = _TMP / "jarvis_key.dpapi"
mc.RECOVERY_KEY_FILE = _TMP / "jarvis_key.recovery"
mc.X25519_KEY_FILE = _TMP / "jarvis_x25519.enc"
mc.CANARY_FILE = _TMP / "jarvis_key.canary"
fs.QUARANTINE_DIR = _TMP / "fact_quarantine"
fo.OUTBOX_FILE = _TMP / "fact_outbox.jsonl"
fo.DESK_KEY_FILE = _TMP / "fact_desk_key.json"
fd.LEDGER_DB = _TMP / "jarvis_fact_ledger.db"
_DB = _TMP / "test_longterm.db"
mm._DB_PATH = str(_DB)

_GATEWAY_SRC = Path(__file__).parent.joinpath("cloud_gateway.py").read_text(encoding="utf-8")
_BRIDGE_SRC = Path(__file__).parent.joinpath("modules", "cloud_bridge.py").read_text(encoding="utf-8")
_OUTBOX_SRC = Path(__file__).parent.joinpath("modules", "fact_outbox.py").read_text(encoding="utf-8")


def _memory_sink(payload: dict) -> bool:
    """What Phase 3 replaces with the governed write path.

    Deliberately the REAL add_memory: the dedup claim is only worth anything if
    it is the production blind index doing the rejecting.
    """
    return mm.add_memory(payload["user_text"], "Fact", payload.get("who") or "KAUSTAV")


def _reset(sink=_memory_sink):
    """A clean world: fresh keys, empty outbox, empty ledger, empty memory."""
    for p in (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE):
        if p.exists():
            p.unlink()
    mc.clear_cache()
    mc.initialise_keys()

    shutil.rmtree(fs.QUARANTINE_DIR, ignore_errors=True)
    fo.reset_state()
    for p in (fo.OUTBOX_FILE, fo.DESK_KEY_FILE, fd.LEDGER_DB, _DB):
        if p.exists():
            p.unlink()
    fd.init_db()
    mm._init_db()
    fd.set_sink(sink)


def _redeploy() -> None:
    """A DEPLOY, not a restart: the container's memory and its disk both go.

    `reset_state` alone is a process restart - the spill file and the key cache
    survive it, which is what `test_the_outbox_survives_a_process_restart` above
    covers and says is not the durability story. This is the other one.
    """
    fo.reset_state()
    for p in (fo.OUTBOX_FILE, fo.DESK_KEY_FILE):
        if p.exists():
            p.unlink()


def _memory_rows() -> int:
    conn = sqlite3.connect(str(_DB))
    try:
        return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        conn.close()


# ── the fake bridge ─────────────────────────────────────────────────────────

class FakeLink:
    """Both ends of the desk<->cloud bridge, in memory.

    `up` models the socket. Sending on a down link raises, exactly as a dead
    WebSocket does, which is the only thing the real transport can tell us.
    """

    def __init__(self):
        self.up = True
        # Awaited after each delivered cloud->desk frame, as (link, frame_number).
        # A real socket interleaves: the desk drains and acks frame 1 while frame 2
        # is still in flight. Tests that care about a mid-batch death need that.
        self.on_cloud_frame = None
        # Monotonic, NOT len(_to_desk): the hook drains the buffer, so a length
        # would read 1 forever and every "die on frame 2" test would silently
        # become "die on frame 1".
        self.frames_sent = 0
        self._to_desk = []
        self._to_cloud = []

    async def cloud_send(self, frame: dict) -> None:
        if not self.up:
            raise ConnectionError("bridge is down")
        self._to_desk.append(frame)
        self.frames_sent += 1
        if self.on_cloud_frame is not None:
            await self.on_cloud_frame(self, self.frames_sent)

    async def desk_send(self, frame: dict) -> None:
        if not self.up:
            raise ConnectionError("bridge is down")
        self._to_cloud.append(frame)

    def take_to_desk(self):
        frames, self._to_desk = self._to_desk, []
        return frames

    def take_to_cloud(self):
        frames, self._to_cloud = self._to_cloud, []
        return frames


async def _connect(link: FakeLink) -> None:
    """What cloud_bridge._session does on connect, met by cloud_gateway.desk_link:
    the desk hands over its public half, and accepting it triggers the flush."""
    await fo.handle_desk_frame(fd.handshake_frame(), link.cloud_send)


async def _desk_drains(link: FakeLink) -> list:
    """What cloud_bridge._handle_facts does for each delivered frame, keeping the
    per-batch counts for assertions.

    `handle_cloud_frame` returns only the ack, so this calls the drain directly
    and rebuilds the same frame — `test_the_ack_frame_carries_exactly_what_the_
    drain_handled` pins the two together so this cannot drift from production.
    """
    results = []
    for frame in link.take_to_desk():
        if frame.get("type") != "facts":
            continue
        result = fd.drain_records(frame.get("records") or [])
        results.append(result)
        if result["ack"]:
            try:
                await link.desk_send({"type": "fact_ack", "ids": result["ack"]})
            except ConnectionError:
                pass          # the ack was lost; the cloud will re-offer
    return results


async def _cloud_reads_acks(link: FakeLink) -> None:
    for frame in link.take_to_cloud():
        await fo.handle_desk_frame(frame)


async def _round_trip(link: FakeLink) -> list:
    await _connect(link)
    results = await _desk_drains(link)
    await _cloud_reads_acks(link)
    return results


def _queue(n: int, prefix: str = "fact") -> list:
    return [fo.queue_fact(f"{prefix} number {i}", who="KAUSTAV", tier="admin",
                          reply=f"Noted {i}, Sir.") for i in range(n)]


# ── 1. bridge down -> outbox -> flush on reconnect ──────────────────────────

def test_a_fact_sealed_while_the_bridge_is_down_waits_in_the_outbox():
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))          # handshake while up, so the key is known
    link.up = False
    link.take_to_desk()

    assert all(e is not None for e in _queue(3))
    assert fo.depth() == 3
    assert link.take_to_desk() == [], "a down bridge somehow delivered frames"


def test_the_outbox_flushes_on_reconnect():
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(3, "offline fact")
    link.take_to_desk()

    link.up = True                        # the desk comes back
    asyncio.run(_round_trip(link))

    assert fo.depth() == 0, "records were not acked off the queue"
    assert _memory_rows() == 3
    assert fd.ledger_count() == 3


def test_nothing_is_queued_before_the_handshake_and_it_says_so():
    """No public half means no way to seal, and plaintext at rest is not on the
    table. That is real loss, so it is counted and announced."""
    _reset()
    assert fo.desk_public() is None
    assert fo.queue_fact("this one cannot be sealed") is None
    assert fo.depth() == 0
    assert fo.stats()["dropped_no_key"] == 1


def test_the_handshake_is_what_unlocks_queueing():
    _reset()
    assert fo.queue_fact("before") is None
    asyncio.run(_connect(FakeLink()))
    assert fo.queue_fact("after") is not None
    assert fo.depth() == 1


def test_the_outbox_survives_a_process_restart():
    """Render's disk is ephemeral, so this covers a restart inside a live
    container — not a redeploy, and it is not the durability story."""
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(4, "pre-restart")
    assert fo.depth() == 4

    fo.reset_state()                      # the process dies
    assert fo.depth() == 4, "the spill file did not come back"
    assert fo.desk_public() is not None, "the cached desk key did not come back"


def test_the_spill_file_holds_nothing_readable():
    _reset()
    asyncio.run(_connect(FakeLink()))
    fo.queue_fact("the safe combination is 41-19-6", who="KAUSTAV",
                  reply="Committed to memory, Sir.")
    spilled = fo.OUTBOX_FILE.read_text(encoding="utf-8")
    for secret in ("combination", "41-19-6", "KAUSTAV", "Committed"):
        assert secret not in spilled, f"{secret!r} hit Render's disk in the clear"


# ── 1b. the durable mirror: what a DEPLOY takes, and what it no longer does ──
#
# Measured on the live gateway on 2026-08-29, with the desk off - which is the
# ORDINARY state of this system, because the PC being off is exactly why a fact
# is being queued at all:
#
#     "fact_outbox": {"depth": 0, "has_desk_key": false, "dropped_no_key": 0, ...}
#
# `has_desk_key: false` is not a desk that never handshook. It is a desk that
# handshook, into a process that has since been redeployed - the key lived in
# memory and in a file on a disk Render throws away. And `queue_fact` DROPS a
# turn it cannot seal, because plaintext at rest is not on the table. Eighteen
# turns went that way in the week the row was written.
#
# The spill-file test above says in its own docstring that it is "not the
# durability story". These are.


class FakeMirror:
    """Stands in for `gateway_state` in Postgres: one row, written by a hook."""

    def __init__(self):
        self.row = None
        self.writes = 0

    def write(self, state):
        self.writes += 1
        # through JSON, because that is what the real column holds - a mirror
        # that only works for objects that never left the process is not a mirror
        self.row = json.loads(json.dumps(state))


def test_the_desk_key_reaches_the_mirror_the_moment_it_is_accepted():
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    asyncio.run(_connect(FakeLink()))
    assert mirror.row is not None, "the handshake never reached the mirror"
    assert mirror.row["desk_public"] == fo.desk_public()


def test_every_queued_fact_reaches_the_mirror():
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(3, "mirrored")
    assert len(mirror.row["records"]) == 3


def test_an_ack_reaches_the_mirror_too_or_a_deploy_would_redeliver():
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    envelope = fo.queue_fact("acked and gone")
    fo.ack([envelope["id"]])
    assert mirror.row["records"] == [], "the mirror still holds an acked record"


def test_a_deploy_no_longer_takes_the_desk_key_with_it():
    """The row this whole section exists for.

    A deploy is not a process restart: the disk goes too, so the spill file and
    the key cache are both gone. What comes back is whatever the database held.
    """
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    asyncio.run(_connect(FakeLink()))
    key_before = fo.desk_public()

    _redeploy()                       # container gone: memory AND disk
    assert fo.desk_public() is None, "the fixture did not actually wipe the disk"
    assert fo.queue_fact("dropped, the way it used to be") is None

    fo.set_change_hook(mirror.write)
    back = fo.restore(mirror.row)
    assert back["key"] is True
    assert fo.desk_public() == key_before
    # ...and the next PC-off turn is SEALED rather than dropped, which is the
    # only thing the operator ever sees of this
    assert fo.queue_fact("sealed on a fresh container") is not None
    assert fo.stats()["has_desk_key"] is True


def test_a_deploy_no_longer_takes_the_sealed_QUEUE_with_it():
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(4, "queued before the deploy")
    ids_before = [e.get("id") for e in fo.pending()]

    _redeploy()
    assert fo.depth() == 0
    fo.set_change_hook(mirror.write)
    back = fo.restore(mirror.row)

    assert back["records"] == 4
    assert [e.get("id") for e in fo.pending()] == ids_before, \
        "order was not preserved, so a mid-batch drop is no longer resumable"


def test_a_restore_adds_rather_than_replaces():
    """A restore that overwrote would race the container's own disk and anything
    queued in the seconds before the database answered."""
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(2, "from the mirror")
    stored = mirror.row

    _redeploy()
    fo.set_change_hook(mirror.write)
    fo.restore({"desk_public": stored["desk_public"], "records": []})
    fresh = fo.queue_fact("queued on the new container, before the database answered")
    assert fresh is not None

    fo.restore(stored)
    assert fo.depth() == 3, "the restore replaced the queue instead of adding to it"
    assert fresh["id"] in [e.get("id") for e in fo.pending()]


def test_restoring_the_same_row_twice_is_free():
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(2, "restored twice")
    stored = mirror.row

    _redeploy()
    fo.set_change_hook(mirror.write)
    fo.restore(stored)
    fo.restore(stored)
    assert fo.depth() == 2, "a second restore duplicated the backlog"


def test_a_restore_never_downgrades_a_key_the_desk_just_handed_over():
    """The desk rotates its keypair by simply connecting with a new one, and the
    handshake happens long before a database answers on a cold start. A stored
    key can therefore only be older than one this process already has.
    """
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    asyncio.run(_connect(FakeLink()))
    stale = mirror.row

    _redeploy()
    fo.set_change_hook(mirror.write)
    # the desk rotates by simply connecting with a new keypair - the handshake
    # is idempotent on this side, which is why rotation needs no coordination
    for path in (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE,
                 mc.CANARY_FILE):
        if path.exists():
            path.unlink()
    mc.clear_cache()
    mc.initialise_keys()
    asyncio.run(_connect(FakeLink()))
    current = fo.desk_public()
    assert current != stale["desk_public"], "the fixture did not rotate the key"

    fo.restore(stale)
    assert fo.desk_public() == current, "the mirror overwrote a fresher key"


def test_the_mirror_holds_nothing_readable():
    """Same rule as the spill file, and it matters more: this one leaves the
    machine. What crosses is ciphertext and one PUBLIC key."""
    _reset()
    mirror = FakeMirror()
    fo.set_change_hook(mirror.write)
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    fo.queue_fact("the safe combination is 41-19-6", who="KAUSTAV",
                  reply="Committed to memory, Sir.")
    written = json.dumps(mirror.row)
    for secret in ("combination", "41-19-6", "KAUSTAV", "Committed"):
        assert secret not in written, f"{secret!r} would reach the database in the clear"


def test_a_mirror_that_throws_never_costs_a_fact():
    """The hook runs on the path that is about to answer him."""
    _reset()

    def _explode(_state):
        raise RuntimeError("the database is down")

    asyncio.run(_connect(FakeLink()))
    fo.set_change_hook(_explode)
    assert fo.queue_fact("queued through a broken mirror") is not None
    assert fo.depth() == 1


def test_health_says_whether_the_mirror_is_armed_at_all():
    """`has_desk_key: true` with no mirror is the state that read as working for
    a week: true until the next spin-down, and nothing said otherwise."""
    _reset()
    assert fo.stats()["durable"] is False
    fo.set_change_hook(FakeMirror().write)
    assert fo.stats()["durable"] is True


def test_the_gateway_arms_the_mirror_and_reads_it_back():
    assert "fact_outbox.set_change_hook(" in _GATEWAY_SRC, \
        "nothing installs the durable mirror, so the hook is dead code"
    startup = _GATEWAY_SRC.split("async def _startup(")[1].split("\n@")[0]
    assert startup.index("set_change_hook") < startup.index("_restore_state"), \
        "the mirror is armed after the restore, so a boot-time write is lost"
    restore = _GATEWAY_SRC.split("async def _restore_state(")[1].split("\n\ndef ")[0]
    assert '"fact_outbox"' in restore and "fact_outbox.restore(" in restore, \
        "the gateway never reads the mirror back"


# ── 2. dedup: the real content_hash index, not a stand-in ───────────────────

def test_a_drained_fact_lands_in_memory_through_the_real_write_path():
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    fo.queue_fact("he takes the 8am train on Tuesdays")
    link.up = True
    asyncio.run(_round_trip(link))

    conn = sqlite3.connect(str(_DB))
    try:
        rows = conn.execute("SELECT content, content_hash FROM memories").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0].startswith(mc.FIELD_PREFIX), "the drained fact is not encrypted at rest"
    assert rows[0][1], "the drained fact has no blind index — dedup would be off"


def test_a_redrained_backlog_does_not_double_store():
    """The ack is lost, so the cloud re-offers everything. Memory must not move."""
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    envelopes = _queue(5, "durable fact")
    link.up = True
    asyncio.run(_round_trip(link))
    assert _memory_rows() == 5

    # Replay the exact same sealed records, twice over.
    for _ in range(2):
        result = fd.drain_records(envelopes)
        assert result["stored"] == 0
        assert result["duplicates"] == 5
        assert _memory_rows() == 5, "a re-drained backlog double-stored"


def test_the_ledger_stops_a_replay_before_it_is_even_unsealed():
    """Layer one: keyed on the RECORD. Cheap, and it saves the extractor entirely."""
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    envelopes = _queue(2, "ledgered")
    link.up = True
    asyncio.run(_round_trip(link))

    calls = []
    fd.set_sink(lambda payload: calls.append(payload) or True)
    fd.drain_records(envelopes)
    assert calls == [], "a replayed record reached the sink"
    fd.set_sink(_memory_sink)


def test_the_blind_index_stops_a_duplicate_the_ledger_cannot_see():
    """Layer two: keyed on the FACT. This is the guarantee that actually protects
    the store — it holds for a fact arriving as a brand-new record."""
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    fo.queue_fact("the mortgage renews in March")
    link.up = True
    asyncio.run(_round_trip(link))
    assert _memory_rows() == 1

    # Same fact, different record id — the ledger has never seen this UUID.
    link.up = False
    again = fo.queue_fact("the mortgage renews in March")
    link.up = True
    assert not fd.already_drained(again["id"])

    result = fd.drain_records([again])
    assert result["stored"] == 0 and result["duplicates"] == 1
    assert _memory_rows() == 1, "content_hash did not stop a re-worded replay"


# ── 3. poison records ───────────────────────────────────────────────────────

def test_a_poison_record_quarantines_and_the_rest_of_the_batch_drains():
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    first = fo.queue_fact("first real fact")
    last = fo.queue_fact("last real fact")
    link.up = True

    poison = {"v": 1, "id": "d" * 32, "sealed": "@@@ not base64 @@@"}
    result = fd.drain_records([first, poison, last])

    assert result["stored"] == 2
    assert result["quarantined"] == 1
    assert _memory_rows() == 2
    assert fs.quarantine_count() == 1
    assert set(result["ack"]) == {first["id"], poison["id"], last["id"]}, \
        "the poison record was not acked — it would be redelivered forever"


def test_a_quarantined_record_is_ledgered_so_a_replay_is_cheap():
    _reset()
    poison = {"v": 1, "id": "e" * 32, "sealed": "not base64!!"}
    fd.drain_records([poison])
    assert fd.ledger_count(fd.QUARANTINED) == 1
    again = fd.drain_records([poison])
    assert again["duplicates"] == 1 and again["quarantined"] == 0
    assert fs.quarantine_count() == 1, "the same poison record was quarantined twice"


def test_a_record_the_desk_keeps_HOLDING_is_never_dropped():
    """Review finding M2. This test used to assert the opposite.

    Four unacked offers used to dead-letter the record and REMOVE it with no
    copy kept. But `fact_drain` acks every verdict it reaches — opened,
    duplicate, quarantined, sink-refused — so a record only comes back unacked
    when the desk HELD it: a locked key store, a faulting sink. That is the one
    state the queue exists to survive.
    """
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    fo.queue_fact("the desk cannot unwrap its key store today")
    link.up = True

    for _ in range(fo.OFFER_WARN_AT * 3):
        asyncio.run(fo.flush(link.cloud_send))
        link.take_to_desk()               # delivered, held, never acked
    assert fo.depth() == 1, "a held record was dropped — this is M2"
    assert fo.stats()["dead_lettered"] == 0
    assert fo.stats()["max_offers"] >= fo.OFFER_WARN_AT, \
        "the offer count is still tracked, it just no longer kills anything"

    # And it is intact, not merely present: the desk recovers, and the fact
    # lands in real memory.
    asyncio.run(fo.flush(link.cloud_send))
    frames = [f for f in link.take_to_desk() if f["type"] == "facts"]
    result = fd.drain_records(frames[-1]["records"])
    assert result["stored"] == 1, f"the held fact did not survive: {result}"


def test_an_extractor_FAILURE_holds_the_record_instead_of_acking_it():
    """Review finding M1, end to end — the same fact dying twice with M2.

    The extractor reported a rate limit by returning `[]`, which is also how it
    says "this turn had no fact in it". `governed_write` turned that into False,
    the drain read False as a VERDICT — ledger STORED, ack — and the cloud
    dropped the sealed original permanently. Nothing was written and the log
    said `0 new, N already known`.

    Driven through the REAL drain with a sink that fails the way a 429 does.
    """
    def _rate_limited(payload):
        raise mm.ExtractionFailedError("429 across every rotation key")

    _reset(sink=_rate_limited)
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    fo.queue_fact("I moved to Kolkata in March")
    link.up = True

    asyncio.run(fo.flush(link.cloud_send))
    frames = [f for f in link.take_to_desk() if f["type"] == "facts"]
    result = fd.drain_records(frames[-1]["records"])

    assert result["held"] == 1, f"a failed extraction was not HELD: {result}"
    assert result["ack"] == [], "the record was acked despite nothing being written"
    assert result["stored"] == 0 and result["duplicates"] == 0, \
        f"a failure was counted as a verdict: {result}"
    assert fd.ledger_count() == 0, \
        "the ledger recorded a fact that was never stored — a redelivery is now skipped"
    assert fs.quarantine_count() == 0, "a transient failure quarantined a good record"
    assert fo.depth() == 1, "the cloud dropped the sealed original"

    # The extractor recovers, the desk reconnects, and the fact lands.
    fd.set_sink(_memory_sink)
    asyncio.run(fo.flush(link.cloud_send))
    frames = [f for f in link.take_to_desk() if f["type"] == "facts"]
    again = fd.drain_records(frames[-1]["records"])
    assert again["stored"] == 1, f"the held fact never arrived: {again}"
    assert _memory_rows() == 1


def test_a_record_the_desk_could_never_ACK_is_dead_lettered_with_a_copy():
    """The genuinely undeliverable shape — and the only one.

    `fact_drain` acks by id, including records it quarantines, so an envelope
    with no usable id is kept by the desk and named by nobody: the cloud would
    re-offer it on every connect forever. It is recognised up front now, not
    after four blind attempts — and the ciphertext is KEPT, because a queue that
    quietly empties itself looks exactly like success.
    """
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    fo.queue_fact("this one is fine")
    link.up = True

    before = fs.quarantine_count()
    # What a torn spill file recovers as: an envelope with no id.
    fo._outbox.append({"envelope": {"v": 1, "sealed": "abc"}, "attempts": 0})
    assert fo.depth() == 2

    asyncio.run(fo.flush(link.cloud_send))
    assert fo.depth() == 1, "the undeliverable record was not removed"
    assert fo.stats()["dead_lettered"] == 1
    assert fs.quarantine_count() == before + 1, \
        "the sealed record was dropped without a copy being kept"

    # The good record beside it was neither dropped nor delayed.
    frames = [f for f in link.take_to_desk() if f["type"] == "facts"]
    ids = [r["id"] for f in frames for r in f["records"]]
    assert len(ids) == 1, f"the healthy record did not go out: {ids}"




# ── 4. ordering and a mid-batch drop ────────────────────────────────────────

def test_facts_drain_oldest_first():
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(6, "ordered")
    link.up = True

    asyncio.run(_connect(link))
    frames = [f for f in link.take_to_desk() if f["type"] == "facts"]
    records = [r for f in frames for r in f["records"]]
    raw = fs.desk_private_raw()
    texts = [fs.open_envelope(r, raw)["user_text"] for r in records]
    assert texts == [f"ordered number {i}" for i in range(6)], f"out of order: {texts}"


def test_a_bridge_that_drops_mid_batch_resumes_without_loss_or_duplication():
    """The reliability claim, end to end.

    60 facts is three frames at BATCH=25. Frame 1 is drained and acked. Frame 2 is
    drained and then the socket dies BEFORE its ack — the worst realistic split,
    because the desk now knows about 50 records while the cloud believes only 25
    are gone. Frame 3 never ships at all.

    So the resume must redeliver 25 already-drained records as cheap duplicates
    plus the 10 that never left, and end with exactly 60 facts in memory.
    """
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(60, "batched")
    link.take_to_desk()

    drained = []

    async def desk_reacts(lnk, frame_number):
        for frame in lnk.take_to_desk():
            drained.append(fd.drain_records(frame["records"]))
            if frame_number == 2:
                lnk.up = False            # drained, but the ack never leaves
            else:
                await lnk.desk_send({"type": "fact_ack", "ids": drained[-1]["ack"]})

    link.up = True
    link.on_cloud_frame = desk_reacts
    asyncio.run(_connect(link))           # frame 1 ok, frame 2 orphaned, frame 3 raises
    asyncio.run(_cloud_reads_acks(link))

    assert sum(r["stored"] for r in drained) == 50, "the desk did not drain two frames"
    assert _memory_rows() == 50
    assert fo.depth() == 35, f"expected 25 acked, 35 left; got {fo.depth()}"

    # Reconnect: everything unacked is re-offered, in order.
    link.up = True
    link.on_cloud_frame = None
    asyncio.run(_connect(link))
    resumed = asyncio.run(_desk_drains(link))
    asyncio.run(_cloud_reads_acks(link))

    assert sum(r["duplicates"] for r in resumed) == 25, "the replay was not deduped"
    assert sum(r["stored"] for r in resumed) == 10, "the tail was lost"
    assert _memory_rows() == 60
    assert fo.depth() == 0
    assert fd.ledger_count() == 60


# ── 5. the safe defaults ────────────────────────────────────────────────────

def test_without_a_sink_records_are_held_and_nothing_is_acked():
    """Phase 2's own state. Nothing written, nothing lost — the cloud keeps them."""
    _reset(sink=None)
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(3, "held")
    link.up = True

    asyncio.run(_round_trip(link))
    assert fo.depth() == 3, "records were acked away without ever being stored"
    assert _memory_rows() == 0
    assert fd.ledger_count() == 0
    fd.set_sink(_memory_sink)


def test_a_sink_fault_holds_the_record_instead_of_losing_it():
    _reset(sink=lambda payload: (_ for _ in ()).throw(sqlite3.OperationalError("db locked")))
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    fo.queue_fact("survives a database fault")
    link.up = True

    asyncio.run(_round_trip(link))
    assert fo.depth() == 1, "a transient store fault lost a fact"
    assert fd.ledger_count() == 0
    fd.set_sink(_memory_sink)


def test_a_locked_key_store_acks_nothing_and_leaves_the_backlog():
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    _queue(3, "locked")
    link.up = True
    asyncio.run(_connect(link))
    frames = [f for f in link.take_to_desk() if f["type"] == "facts"]

    mc.DPAPI_KEY_FILE.unlink()
    mc.clear_cache()
    try:
        fd.handle_cloud_frame(frames[0])
    except mc.MemoryLockedError:
        assert fo.depth() == 3, "a locked key store cost us the backlog"
        assert fs.quarantine_count() == 0, "a locked key store quarantined good records"
        assert fd.ledger_count() == 0
        return
    finally:
        _reset()
    raise AssertionError("a locked key store drained silently")


def test_the_outbox_cap_is_announced_not_silent():
    _reset()
    asyncio.run(_connect(FakeLink()))
    before = fs.quarantine_count()
    fo.MAX_OUTBOX, real = 5, fo.MAX_OUTBOX
    try:
        _queue(7, "capped")
        assert fo.depth() == 5
        assert fo.stats()["dropped_overflow"] == 2
    finally:
        fo.MAX_OUTBOX = real
    # M2's sibling: announcing a drop is not the same as keeping it. The
    # envelope is ciphertext, so the copy costs disk and nothing else.
    assert fs.quarantine_count() == before + 2, \
        "an evicted record left no copy behind — the queue emptied itself silently"


# ── 6. the wiring is real, not just the modules ─────────────────────────────

def test_the_gateway_queues_on_every_path_the_desk_did_not_handle():
    """Both of them: the PC-off path AND the connected-but-wedged fallback."""
    assert _GATEWAY_SRC.count("_queue_offline_fact(ident, text, reply)") == 2, \
        "a cloud-answered path is not queueing its fact"


def test_the_gateway_queues_before_it_replies():
    answer = _GATEWAY_SRC.split("async def _answer(")[1].split("@router.message")[0]
    assert answer.index("_queue_offline_fact") < answer.index("await _send_chunked"), \
        "the fact is queued after the reply — a crash in between loses it"


def test_the_gateway_handles_the_two_fact_frames():
    assert "fact_outbox.handle_desk_frame" in _GATEWAY_SRC
    link = _GATEWAY_SRC.split("async def desk_link(")[1]
    assert "handle_desk_frame" in link, "desk_link never looks at the fact frames"


def test_the_desk_hands_over_its_public_half_on_every_connect():
    assert "fact_drain.handshake_frame()" in _BRIDGE_SRC
    session = _BRIDGE_SRC.split("async def _session(")[1]
    assert "handshake_frame" in session, "the handshake is not sent on connect"
    assert 'ftype == "facts"' in _BRIDGE_SRC, "the desk ignores the facts frame"


def test_the_ack_frame_carries_exactly_what_the_drain_handled():
    """Pins handle_cloud_frame — the wrapper this harness stands in for — so the
    two cannot drift."""
    _reset()
    link = FakeLink()
    asyncio.run(_connect(link))
    link.up = False
    envelope = fo.queue_fact("pinned by the wrapper")
    link.up = True

    assert fd.handle_cloud_frame({"type": "facts", "records": [envelope]}) == {
        "type": "fact_ack", "ids": [envelope["id"]]}
    assert fd.handle_cloud_frame({"type": "facts", "records": []}) is None
    assert fd.handle_cloud_frame({"type": "cmd", "text": "hello"}) is None


def test_the_drain_runs_off_the_event_loop():
    """It opens sqlite and walks DPAPI — blocking it would stall the bridge."""
    assert "asyncio.to_thread(fact_drain.handle_cloud_frame" in _BRIDGE_SRC


def test_the_outbox_stays_importable_on_render():
    """cloud_gateway imports this; requirements-cloud.txt has no `cryptography`."""
    tree = ast.parse(_OUTBOX_SRC)
    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    allowed = {"__future__", "base64", "binascii", "json", "collections", "pathlib",
               "typing", "modules"}
    for name in names:
        assert name.split(".")[0] in allowed, f"{name} would have to ship to Render"
        assert "memory_crypto" not in name, "the cloud cannot import memory_crypto"


def test_pynacl_is_pinned_for_both_sides():
    root = Path(__file__).parent
    for req in ("requirements.txt", "requirements-cloud.txt"):
        text = root.joinpath(req).read_text(encoding="utf-8").lower()
        assert "nacl==1.6.2" in text, f"{req} does not pin pynacl"
    desk = root.joinpath("requirements.txt").read_text(encoding="utf-8")
    assert "protobuf==6.33.6" in desk, "the protobuf pin moved"


# ── 7. the harness's own boundaries ─────────────────────────────────────────

def test_the_harness_never_touched_the_real_key_files():
    assert _fingerprint_real_keys() == _REAL_KEYS_BEFORE, \
        "a real jarvis_key.* file changed during the run"


def test_the_harness_never_touched_the_real_ledger_or_outbox():
    assert fd.LEDGER_DB.parent == _TMP and fo.OUTBOX_FILE.parent == _TMP
    assert _fingerprint(_REAL_LEDGER) == _REAL_LEDGER_FP,         "this harness touched the operator's real fact ledger"
    assert _fingerprint(_REAL_OUTBOX) == _REAL_OUTBOX_FP,         "this harness touched the operator's real outbox spill file"


def test_the_harness_wrote_memory_only_into_its_temp_database():
    assert Path(mm._DB_PATH) == _DB
    assert _DB.parent == _TMP


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print(f"PASS  {name}")
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
        print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
