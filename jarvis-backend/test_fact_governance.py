"""Harness for the GOVERNED SINK — C#11a Step 4, Phase 3.

Phase 2 proved delivery. This proves the gate, and the gate is the whole point:
a drained fact is an UNATTENDED write, arriving from a queue whose seal is
anonymous (`crypto_box_seal` proves only that this desk can open a record — never
who wrote it). So `who` and `tier` inside a payload are claims, and the desk has
to treat them as claims.

What is asserted here, in the order it matters:

  1. a drained fact lands in memory VIA the gated path — governance is INVOKED,
     with the real ruleset, and it is invoked BEFORE the row exists;
  2. every refusal path dead-letters instead of writing: BLOCK, an unattended
     CONFIRM, an unknown verdict, an unrecognised `who`, an unissued `tier`,
     a wrong version, empty or oversized text;
  3. one refused record does not cost the batch — the rest still drains;
  4. dedup holds end to end through the real `content_hash` blind index;
  5. there is NO path from the drain to memory that skips the gate — proved
     structurally, not just behaviourally: the drain imports no store, the only
     `set_sink()` caller in the tree is `fact_sink.install()`, and the bridge
     installs it before the handshake that triggers the flush;
  6. a FAULT (locked store, a governance engine that throws) HOLDS the record
     rather than acking it away.

Keys, ledger, outbox, dead-letter store and the memory database are all
redirected into a temp directory. The only thing stubbed is the extractor's LLM
call — the write path, the encryption, the blind index and the governance engine
are all the production ones.
"""

import ast
import hashlib
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import memory_manager as mm
from governance_manager import governance_manager as gm
from modules import fact_drain as fd
from modules import fact_outbox as fo
from modules import fact_seal as fs
from modules import fact_sink as sink
from modules import memory_crypto as mc

# -- isolation ---------------------------------------------------------------

_TMP = Path(tempfile.mkdtemp(prefix="jarvis_factgov_"))
_REAL_PATHS = (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE)
_REAL_LEDGER, _REAL_OUTBOX = fd.LEDGER_DB, fo.OUTBOX_FILE


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

_HERE = Path(__file__).resolve().parent
_SINK_SRC = _HERE.joinpath("modules", "fact_sink.py").read_text(encoding="utf-8")
_DRAIN_SRC = _HERE.joinpath("modules", "fact_drain.py").read_text(encoding="utf-8")
_BRIDGE_SRC = _HERE.joinpath("modules", "cloud_bridge.py").read_text(encoding="utf-8")
_BRAIN_SRC = _HERE.joinpath("brain.py").read_text(encoding="utf-8")

# The bound method as the production singleton exposes it, captured once so a
# patched test can always put the real engine back.
_REAL_CHECK = gm.check


def _fake_extract(user_text, user="KAUSTAV"):
    """Stands in for the Groq extraction call ONLY.

    Everything downstream of it — add_memory, AES-256-GCM, the content_hash blind
    index, the duplicate check — is the real thing, which is the only reason the
    dedup assertions below mean anything.
    """
    return [{"category": "Fact", "content": user_text}]


def _reset():
    """A clean world: fresh keys, empty stores, the production sink installed."""
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

    mm.extract_memories_from_input = _fake_extract
    gm.check = _REAL_CHECK
    gm.cancel_pending()
    sink.install()                       # the production install path, not a stub


def _rows():
    conn = sqlite3.connect(str(_DB))
    try:
        return conn.execute(
            "SELECT user, content, content_hash FROM memories").fetchall()
    finally:
        conn.close()


def _memory_rows() -> int:
    return len(_rows())


def _payload(**over) -> dict:
    payload = {
        "v": fs.RECORD_VERSION,
        "id": uuid.uuid4().hex,
        "ts": "2026-08-01T09:00:00+00:00",
        "who": "KAUSTAV",
        "tier": "admin",
        "user_text": "the spare key lives in the hall drawer",
        "reply": "Noted, Sir.",
    }
    payload.update(over)
    return payload


def _seal(**over) -> dict:
    """One sealed envelope, addressed to this run's temp desk key."""
    return fs.seal_fact(_payload(**over), fs.desk_public_b64())


class _Spy:
    """Wraps governance_manager.check so a test can see it was really consulted.

    Records the memory row count AT THE MOMENT of each call, which is what turns
    "governance was invoked" into "governance was invoked BEFORE the write".
    """

    def __init__(self, verdict=None):
        self.calls = []
        self.rows_at_call = []
        self._verdict = verdict

    def __call__(self, payload):
        self.calls.append(payload)
        self.rows_at_call.append(_memory_rows())
        if self._verdict is not None:
            return dict(self._verdict)
        return _REAL_CHECK(payload)


# -- 1. the happy path goes THROUGH the gate ---------------------------------

def test_a_drained_fact_lands_in_memory_via_the_gated_write_path():
    _reset()
    spy = _Spy()
    gm.check = spy
    try:
        result = fd.drain_records([_seal(user_text="he takes the 8am train")])
    finally:
        gm.check = _REAL_CHECK

    assert result["stored"] == 1, result
    assert len(spy.calls) == 1, "governance was skipped on the write path"
    assert spy.calls[0]["action_type"] == sink.ACTION_TYPE
    assert spy.calls[0]["user"] == "KAUSTAV"
    assert spy.calls[0]["permission_tier"] == "admin"

    rows = _rows()
    assert len(rows) == 1
    assert rows[0][0] == "KAUSTAV", "attribution changed on the way through"
    assert rows[0][1].startswith(mc.FIELD_PREFIX), "the drained fact is not encrypted"
    assert rows[0][2], "no blind index — dedup would be silently off"


def test_governance_is_consulted_before_the_row_exists_not_after():
    _reset()
    spy = _Spy()
    gm.check = spy
    try:
        fd.drain_records([_seal(user_text="the boiler is serviced in October")])
    finally:
        gm.check = _REAL_CHECK
    assert spy.rows_at_call == [0], \
        f"the row already existed when governance was asked: {spy.rows_at_call}"
    assert _memory_rows() == 1


def test_the_production_ruleset_actually_passes_this_action():
    """The gate has to be real in BOTH directions: a rule that blocked every
    drain would pass every test above and quietly store nothing."""
    _reset()
    assert gm.get_tier(sink.ACTION_TYPE) == "AUTO", \
        f"'{sink.ACTION_TYPE}' is not AUTO in governance.json — the drain cannot write"


def test_a_partner_fact_drains_under_her_own_identity():
    """Parity with the live path, which extracts for every recognised identity
    (main.py fires it for partners deliberately). The queue must not store more
    than live — and it must not quietly store less, either."""
    _reset()
    fd.drain_records([_seal(who="MOUSUMI", tier="vip_guest",
                            user_text="her recital is on the 14th")])
    rows = _rows()
    assert len(rows) == 1
    assert rows[0][0] == "MOUSUMI", f"filed under {rows[0][0]}"


# -- 2. every refusal dead-letters, and none of them writes ------------------

def _assert_refused(envelope, expect_rows=0):
    """A refusal: nothing written, the record KEPT, ledgered and acked."""
    before = fs.quarantine_count()
    result = fd.drain_records([envelope])
    assert result["stored"] == 0, result
    assert result["quarantined"] == 1, result
    assert result["ack"] == [envelope["id"]], "a refused record was not acked"
    assert _memory_rows() == expect_rows, "a refused fact reached memory"
    assert fs.quarantine_count() == before + 1, "the refused record was not kept"
    assert fd.ledger_count(fd.QUARANTINED) >= 1
    return result


def test_a_blocked_verdict_never_reaches_memory():
    """The real engine, told the real way: the ruleset says BLOCK."""
    _reset()
    gm._rules[sink.ACTION_TYPE] = "BLOCK"
    try:
        _assert_refused(_seal(user_text="this must never be stored"))
    finally:
        gm.reload_ruleset()


def test_an_unattended_confirm_is_refused_and_leaves_no_pending_slot():
    """The trap this closes is not the refusal — it is the leftover.

    check() parks a CONFIRM in a single pending slot before returning. If the
    drain walked away from it, the operator's next spoken 'yes', meant for
    something he actually saw, would confirm a write he never did.
    """
    _reset()
    gm._rules[sink.ACTION_TYPE] = "CONFIRM"
    try:
        _assert_refused(_seal(user_text="pending forever"))
        assert not gm.has_pending(), \
            "the drain left a pending confirmation the operator could approve blind"
    finally:
        gm.reload_ruleset()
        gm.cancel_pending()


def test_an_unrecognised_verdict_is_refused_rather_than_assumed_safe():
    _reset()
    gm.check = _Spy(verdict={"signal": "MAYBE", "tier": "AUTO",
                             "action_type": sink.ACTION_TYPE, "reason": "who knows"})
    try:
        _assert_refused(_seal(user_text="not a signal this desk knows"))
    finally:
        gm.check = _REAL_CHECK


def test_a_who_the_desk_does_not_recognise_is_refused():
    """The seal is anonymous, so `who` is a claim. An unrecognised one is exactly
    what a compromised cloud would send."""
    _reset()
    _assert_refused(_seal(who="STRANGER", user_text="trust me, I am the owner"))


def test_a_tier_the_desk_does_not_issue_is_refused():
    _reset()
    _assert_refused(_seal(tier="superuser", user_text="promoted myself"))
    _reset()
    _assert_refused(_seal(tier=None, user_text="no tier at all"))


def test_a_payload_from_a_future_version_is_refused():
    _reset()
    _assert_refused(_seal(v=fs.RECORD_VERSION + 1, user_text="from a later desk"))


def test_empty_and_oversized_text_are_both_refused():
    _reset()
    _assert_refused(_seal(user_text="   "))
    _reset()
    _assert_refused(_seal(user_text="x" * (sink.MAX_FACT_CHARS + 1)))


def test_a_refused_record_is_kept_as_ciphertext_not_plaintext():
    """The dead-letter file is for inspection, and it must not become the one
    place a PC-off turn sits readable on disk.

    The envelope is ciphertext by construction; the REASON beside it is not, and
    it is generated from the opened payload. Every refusal path is walked here,
    because it only takes one message that quotes a value.
    """
    _reset()
    _assert_refused(_seal(who="STRANGER", user_text="the alarm code is 4417"))
    _assert_refused(_seal(tier="superuser", user_text="the safe combination is 41-19-6"))
    _assert_refused(_seal(user_text="x" * (sink.MAX_FACT_CHARS + 1)))
    gm._rules[sink.ACTION_TYPE] = "BLOCK"
    try:
        _assert_refused(_seal(user_text="his salary is 42 lakh"))
    finally:
        gm.reload_ruleset()

    dumped = "\n".join(p.read_text(encoding="utf-8") for p in fs.list_quarantined())
    # The ledger sits beside it, unencrypted, and a refused record's claimed
    # identity is the field that just failed to check out.
    conn = sqlite3.connect(str(fd.LEDGER_DB))
    try:
        dumped += "\n" + "\n".join(
            str(r) for r in conn.execute("SELECT * FROM drained").fetchall())
    finally:
        conn.close()

    for secret in ("alarm code", "4417", "STRANGER", "combination", "41-19-6",
                   "superuser", "salary", "42 lakh", "x" * 40):
        assert secret not in dumped, f"{secret!r} was dead-lettered in the clear"


# -- 3. one bad record does not cost the batch -------------------------------

def test_a_refused_record_quarantines_and_the_rest_of_the_batch_still_drains():
    """Both flavours of bad at once: one that will not OPEN, and one that opens
    fine and is then refused by the gate."""
    _reset()
    good_one = _seal(user_text="first real fact")
    unopenable = {"v": 1, "id": "d" * 32, "sealed": "@@@ not base64 @@@"}
    refused = _seal(who="STRANGER", user_text="second real fact")
    good_two = _seal(user_text="last real fact")

    result = fd.drain_records([good_one, unopenable, refused, good_two])

    assert result["stored"] == 2, result
    assert result["quarantined"] == 2, result
    assert result["held"] == 0, result
    assert set(result["ack"]) == {good_one["id"], unopenable["id"],
                                  refused["id"], good_two["id"]}, \
        "something bad was left unacked — it would be redelivered forever"
    assert _memory_rows() == 2
    assert fs.quarantine_count() == 2

    stored = {r[0] for r in _rows()}
    assert stored == {"KAUSTAV"}, f"the refused record's identity got in: {stored}"


def test_a_refused_record_is_ledgered_so_a_replay_costs_nothing():
    _reset()
    bad = _seal(who="STRANGER", user_text="I will be back")
    fd.drain_records([bad])
    assert fs.quarantine_count() == 1

    spy = _Spy()
    gm.check = spy
    try:
        again = fd.drain_records([bad])
    finally:
        gm.check = _REAL_CHECK
    assert again["duplicates"] == 1 and again["quarantined"] == 0, again
    assert spy.calls == [], "a ledgered record was re-opened and re-judged"
    assert fs.quarantine_count() == 1, "the same record was dead-lettered twice"


# -- 4. dedup, end to end through the real blind index -----------------------

def test_a_duplicate_drained_fact_is_a_no_op():
    """A brand-new record id carrying a fact already in the store. The ledger
    cannot see this one — only `content_hash` can."""
    _reset()
    first = _seal(user_text="the mortgage renews in March")
    assert fd.drain_records([first])["stored"] == 1
    assert _memory_rows() == 1

    second = _seal(user_text="the mortgage renews in March")
    assert not fd.already_drained(second["id"]), "the ids collided; test is void"

    result = fd.drain_records([second])
    assert result["stored"] == 0 and result["duplicates"] == 1, result
    assert result["ack"] == [second["id"]], "the duplicate was not acked"
    assert _memory_rows() == 1, "content_hash did not stop the duplicate"
    assert fs.quarantine_count() == 0, "a duplicate is not a poison record"


def test_a_duplicate_still_passes_the_gate_before_being_recognised():
    """Ordering check: a duplicate is discovered by the STORE, downstream of
    governance. It must not become an accidental way to skip the gate."""
    _reset()
    fd.drain_records([_seal(user_text="the mortgage renews in March")])
    spy = _Spy()
    gm.check = spy
    try:
        fd.drain_records([_seal(user_text="the mortgage renews in March")])
    finally:
        gm.check = _REAL_CHECK
    assert len(spy.calls) == 1, "the duplicate bypassed governance"
    assert _memory_rows() == 1


# -- 5. no ungoverned path exists (structural, not just behavioural) ---------

def test_the_drain_cannot_reach_memory_on_its_own():
    """If fact_drain could import a store, the sink would be a convention rather
    than a chokepoint."""
    tree = ast.parse(_DRAIN_SRC)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    for name in imported:
        head = name.split(".")[-1]
        assert head not in {"memory_manager", "brain", "memory", "episodic_memory",
                            "personal_rag", "memory_engine"}, \
            f"fact_drain imports {name} — it could write around the sink"
    assert "add_memory" not in _DRAIN_SRC, "fact_drain names a store write directly"


def test_the_only_sink_installed_anywhere_is_the_governed_one():
    """Scan the whole backend, not just the two files we happen to trust.

    Parsed, not grepped: a docstring that merely mentions `set_sink()` is not an
    installation, and a scan that cannot tell the difference would either cry
    wolf or be quietly loosened until it stops.
    """
    callers, unparsed = [], []
    for path in sorted(_HERE.rglob("*.py")):
        if path.name.startswith("test_") or "venv" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            unparsed.append(path.name)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name == "set_sink":
                callers.append((path.name, node.lineno, ast.unparse(node)))
    assert not unparsed, f"unscanned files could hide a sink: {unparsed}"
    assert len(callers) == 1, f"more than one sink is installed in the tree: {callers}"
    name, _, call = callers[0]
    assert name == "fact_sink.py" and "governed_write" in call, callers


def test_the_sink_runs_governance_before_it_touches_the_store():
    body = _SINK_SRC.split("def governed_write(")[1].split("\ndef ")[0]
    assert body.index("_governance_gate(") < body.index("extract_and_persist"), \
        "the write is ordered before the gate"
    assert body.index("_validate(") < body.index("_governance_gate("), \
        "governance is judging an unvalidated payload"
    assert body.index("_require_unlocked_store()") < body.index("extract_and_persist"), \
        "the store lock is checked after the write is attempted"


def test_the_bridge_installs_the_sink_before_the_handshake_that_flushes():
    session = _BRIDGE_SRC.split("async def _session(")[1].split("\nasync def ")[0]
    assert "fact_sink.install()" in session, "the bridge never installs a sink"
    assert session.index("fact_sink.install()") < session.index("handshake_frame()"), \
        "the key handshake triggers the flush before a sink exists"


def test_the_sink_uses_the_same_persist_call_the_live_path_delegates_to():
    """'The existing extract_and_store_memory path' has to stay true as a fact
    about the code, not just as a sentence in a docstring."""
    live = _BRAIN_SRC.split("def extract_and_store_memory(")[1].split("\ndef ")[0]
    assert "memory_manager.extract_and_persist(" in live
    assert "extract_and_persist" in _SINK_SRC
    assert "extract_and_store_memory" not in _SINK_SRC.split('"""')[2], \
        "the sink calls the wrapper that swallows exceptions"


def test_tier_allows_was_not_weakened_to_make_this_fit():
    from action_engine import VIP_GUEST_ALLOWED_ACTIONS, tier_allows
    assert VIP_GUEST_ALLOWED_ACTIONS == frozenset({"tavily_search", "web_search"}), \
        f"the VIP allowlist grew: {sorted(VIP_GUEST_ALLOWED_ACTIONS)}"
    assert sink.ACTION_TYPE not in VIP_GUEST_ALLOWED_ACTIONS
    assert tier_allows("admin", sink.ACTION_TYPE) is True
    assert tier_allows("vip_guest", sink.ACTION_TYPE) is False
    assert tier_allows("", sink.ACTION_TYPE) is False


def test_the_drain_writes_no_partner_transcript():
    """The partner-chat flag governs a VERBATIM store. A drained record's `who`
    is unauthenticated, so this path writes the extraction and nothing else."""
    for src, name in ((_SINK_SRC, "fact_sink"), (_DRAIN_SRC, "fact_drain")):
        assert "partner_log" not in src.split('"""')[2], \
            f"{name} writes a raw partner transcript on drain"


def test_the_roster_comes_from_the_registry_that_already_owns_it():
    from modules import partner_registry
    roster = sink.recognised_users()
    assert sink.OWNER in roster
    for meta in partner_registry.SLOTS.values():
        assert meta["user"] in roster, f"{meta['user']} is registered but not drainable"
    assert "STRANGER" not in roster


# -- 6. faults HOLD; they never ack a fact away ------------------------------

def test_a_locked_key_store_holds_the_write_instead_of_writing_plaintext():
    """Not just about secrecy: with no key there is no blind index either, so the
    row would land unencrypted AND with dedup silently switched off."""
    _reset()
    payload = _payload(user_text="written only when the store is open")
    mc.DPAPI_KEY_FILE.unlink()
    mc.clear_cache()
    try:
        sink.governed_write(payload)
    except mc.MemoryLockedError:
        assert _memory_rows() == 0
        assert not isinstance(mc.MemoryLockedError("x"), fs.FactSealError), \
            "a locked store would dead-letter instead of holding"
        return
    finally:
        _reset()
    raise AssertionError("a locked key store wrote anyway")


def test_a_governance_engine_that_throws_holds_the_record():
    """Fail closed in the ugliest direction: the gate itself is broken."""
    _reset()

    def _explode(payload):
        raise RuntimeError("governance.json went missing mid-flight")

    gm.check = _explode
    try:
        result = fd.drain_records([_seal(user_text="not while the gate is down")])
    finally:
        gm.check = _REAL_CHECK

    assert result["held"] == 1, result
    assert result["stored"] == 0 and result["quarantined"] == 0, result
    assert result["ack"] == [], "a record was acked while the gate was broken"
    assert _memory_rows() == 0
    assert fd.ledger_count() == 0, "a held record was ledgered"


def test_a_store_fault_holds_the_record_rather_than_losing_it():
    _reset()
    real = mm.extract_and_persist

    def _boom(text, user="KAUSTAV"):
        raise sqlite3.OperationalError("database is locked")

    mm.extract_and_persist = _boom
    try:
        result = fd.drain_records([_seal(user_text="survives a database fault")])
    finally:
        mm.extract_and_persist = real

    assert result["held"] == 1 and result["ack"] == [], result
    assert _memory_rows() == 0
    assert fs.quarantine_count() == 0, "a transient fault dead-lettered a good fact"


def test_a_turn_with_nothing_worth_keeping_is_acked_not_held():
    """Live, a turn that yields no memory stores none and moves on. Same here —
    holding it would re-offer it forever."""
    _reset()
    mm.extract_memories_from_input = lambda text, user="KAUSTAV": []
    try:
        envelope = _seal(user_text="thanks, that is all")
        result = fd.drain_records([envelope])
    finally:
        mm.extract_memories_from_input = _fake_extract

    assert result["stored"] == 0 and result["duplicates"] == 1, result
    assert result["ack"] == [envelope["id"]]
    assert result["held"] == 0
    assert _memory_rows() == 0


def test_no_sink_still_means_held_not_dropped():
    """Phase 2's safety net has to survive Phase 3."""
    _reset()
    fd.set_sink(None)
    try:
        result = fd.drain_records([_seal(user_text="nobody is home")])
    finally:
        sink.install()
    assert result["held"] == 1 and result["ack"] == [], result
    assert _memory_rows() == 0 and fd.ledger_count() == 0


# -- 7. the harness's own boundaries -----------------------------------------

def test_the_harness_never_touched_the_real_key_files():
    assert _fingerprint_real_keys() == _REAL_KEYS_BEFORE, \
        "a real jarvis_key.* file changed during the run"


def test_the_harness_never_touched_the_real_ledger_or_outbox():
    assert fd.LEDGER_DB.parent == _TMP and fo.OUTBOX_FILE.parent == _TMP
    assert not _REAL_LEDGER.exists(), "the real fact ledger was created"
    assert not _REAL_OUTBOX.exists(), "the real outbox spill file was created"


def test_the_harness_wrote_memory_only_into_its_temp_database():
    assert Path(mm._DB_PATH) == _DB and _DB.parent == _TMP


def test_the_harness_left_the_governance_engine_as_it_found_it():
    assert gm.check == _REAL_CHECK, "a patched governance check leaked out of a test"
    assert gm.get_tier(sink.ACTION_TYPE) == "AUTO", "the ruleset was left mutated"
    assert not gm.has_pending(), "a pending confirmation leaked out of a test"


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
        gm.check = _REAL_CHECK
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
