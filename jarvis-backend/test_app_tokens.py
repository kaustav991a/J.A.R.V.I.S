"""Harness: one secret opened five doors, and nothing expired.

WHY THIS EXISTS
---------------
Two rows of the security goal in `jarvis-mobile`'s ledger, and they are one
change: `token-split` ("One string gates the socket, push, the commute route and
app state alike. **A token that can register a push and a token that can read
your day must not be the same secret**") and `token-expiry` ("Nothing expires.").

The credential in the phone's SecureStore opened, in one string:

  * `/app-link`          — the socket onto a brain that answers as him
  * `/app-push/register` — the address his phone is reachable at while asleep
  * `/app-commute`       — where he goes and when
  * `/app-fact`          — what the assistant believes about him, in every prompt
  * `/app-say`           — words placed in the assistant's mouth, unprompted

Any leak of any one of those was a leak of all five, permanently: there was no
expiry anywhere and no way to hand out less than everything.

WHAT THIS PINS
--------------
Offline and deterministic — no network, no model, no clock beyond `time.time()`,
and every token here is minted in-process.

  * a capability token opens ITS door and is refused at the other four, with
    `403 wrong_capability` rather than a bare 401, because a client presenting
    the wrong one of its own tokens is a bug, not an intruder;
  * expiry is enforced, and says `token_expired` so the app can refresh instead
    of reporting a mysterious refusal to him;
  * **minting is master-only.** A capability token that could mint would make
    expiry decorative;
  * **rotating `APP_TOKEN` revokes every derived token in the same instant**, with
    no table to clean up. That is the property that makes `bridge-secret`
    rotation survivable, and it is asserted here rather than assumed;
  * the master still opens everything, and every use is COUNTED. His installed
    app presents it, and an auth change that locks him out of his own assistant
    to prove a point would be worse than the thing being fixed. `/health` turns
    the migration into a number;
  * no route is left comparing against the master by hand — asserted over the
    source, because that is exactly how five gates came to share one secret.

Run standalone: `python test_app_tokens.py`
"""

import base64
import hmac
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MASTER = "harness-master-token"

_fails: list = []
_checks = 0


def check(ok: bool, why: str) -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"PASS  {why}")
    else:
        print(f"FAIL  {why}")
        _fails.append(why)


def _neuter() -> None:
    """Never the real bot, never the real push registry, never the real disk."""
    cg.BOT_TOKEN = ""
    cg.PUBLIC_URL = ""
    cg.MODE = "webhook"
    cg.APP_TOKEN = MASTER
    cg.APP_KEEPALIVE_SECS = 3600.0
    cg.APP_TELEMETRY_SECS = 3600.0
    cg._desk_ws = None
    cg._app_clients.clear()
    cg._legacy_master_use.clear()
    here = os.path.dirname(os.path.abspath(cg.__file__))
    cg._PUSH_FILE = os.path.join(here, "app_push_tokens.test.json")
    cg._COMMUTE_FILE = os.path.join(here, "app_commute.test.json")
    cg._BRIEFED_FILE = os.path.join(here, "app_briefed.test.json")
    cg._NUDGE_FILE = os.path.join(here, "app_nudge.test.json")
    cg._VOICE_FILE = os.path.join(here, "app_voice.test.json")
    cg._push_targets.clear()
    cg._last_push_at = 0.0


_neuter()
client = TestClient(cg.app)

# One representative request per gate: the route, the capability that should open
# it, and a body it will accept. The bodies are deliberately harmless — this
# harness is about who may knock, not about what happens after.
GATES = [
    ("/app-fact", "memory", {}),
    ("/app-say", "say", {"message": "a test line"}),
    ("/app-push/register", "push", {"push_token": "ExponentPushToken[harness]"}),
    ("/app-commute", "state", {"tz": "Asia/Kolkata",
                               "days": [False, True, True, True, True, True, False],
                               "departures": [{"place_id": "office", "label": "Office",
                                               "hour": 19, "minute": 0,
                                               "lat": 22.57, "lon": 88.36}]}),
]


def post(path: str, token: str, body: dict):
    return client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


# ── the master still works, and is counted ──────────────────────────────────

def test_the_master_still_opens_every_door():
    """His installed app presents it. Breaking that to prove a point about
    tokens would be a worse outcome than the thing being fixed."""
    _neuter()
    for path, _cap, body in GATES:
        r = post(path, MASTER, body)
        check(r.status_code == 200, f"master opens {path} ({r.status_code})")
    with client.websocket_connect(f"/app-link?token={MASTER}") as ws:
        check(ws.receive_json().get("status") == "online",
              "master opens the socket")


def test_every_master_use_is_counted_so_the_migration_is_a_number():
    _neuter()
    post("/app-fact", MASTER, {})
    post("/app-fact", MASTER, {})
    post("/app-say", MASTER, {"message": "hello"})
    counted = cg._legacy_master_use
    check(counted.get("app-fact") == 2 and counted.get("app-say") == 1,
          f"the master is counted per route: {dict(counted)}")
    health = client.get("/health").json()["app_auth"]
    check(health["master_calls"].get("app-fact") == 2,
          "/health reports how far the migration has NOT got")
    check(set(health["capabilities"]) == set(cg.APP_CAPABILITIES),
          "/health names the capabilities that exist")


# ── one token, one door ─────────────────────────────────────────────────────

def test_a_capability_token_opens_its_own_door():
    _neuter()
    for path, cap, body in GATES:
        r = post(path, cg.mint_app_token(cap), body)
        check(r.status_code == 200, f"the {cap} token opens {path} ({r.status_code})")


def test_a_capability_token_is_refused_at_every_other_door():
    """The whole row: a token handed to a notification service must not read a
    life. Refused with 403 and a NAMED reason, because a client presenting the
    wrong one of its own tokens is a bug rather than an intruder."""
    _neuter()
    push_token = cg.mint_app_token("push")
    for path, cap, body in GATES:
        if cap == "push":
            continue
        r = post(path, push_token, body)
        ok = r.status_code == 403 and r.json().get("error") == "wrong_capability"
        check(ok, f"the push token is refused at {path} ({r.status_code})")


def test_the_socket_takes_only_the_link_token():
    _neuter()
    with client.websocket_connect(f"/app-link?token={cg.mint_app_token('link')}") as ws:
        check(ws.receive_json().get("status") == "online",
              "the link token opens the socket")
    refused = False
    try:
        with client.websocket_connect(
                f"/app-link?token={cg.mint_app_token('push')}") as ws:
            ws.receive_json()
    except Exception:  # noqa: BLE001
        refused = True
    check(refused, "the push token is refused at the socket")


# ── expiry ──────────────────────────────────────────────────────────────────

def test_an_expired_token_is_refused_and_says_so():
    """Expiry is the ordinary end of a token's life, not an incident. The app's
    move is to mint a new one, and it can only do that if it is told."""
    _neuter()
    stale = cg.mint_app_token("memory", ttl_days=-1)
    r = post("/app-fact", stale, {})
    check(r.status_code == 401, f"an expired token is refused ({r.status_code})")
    check(r.json().get("error") == "token_expired",
          "...and the refusal names expiry, so the app knows to refresh")


def test_a_token_one_second_from_expiry_still_works():
    # the boundary, because "expired" must mean past, not nearly
    _neuter()
    fresh = cg.mint_app_token("memory", ttl_days=1.0 / 86400)
    check(cg.read_app_token(fresh, "memory") == "capability",
          "a token expiring in a second is still a token")


def test_the_ttl_can_be_shortened_by_the_caller_but_never_lengthened():
    _neuter()
    short = client.post("/app-tokens", json={"ttl_days": 1},
                        headers={"Authorization": f"Bearer {MASTER}"}).json()
    greedy = client.post("/app-tokens", json={"ttl_days": 3650},
                         headers={"Authorization": f"Bearer {MASTER}"}).json()
    check(short["ttl_days"] == 1, f"a shorter ttl is honoured ({short['ttl_days']})")
    check(greedy["ttl_days"] == cg.APP_TOKEN_TTL_DAYS,
          f"a longer one is clamped to {cg.APP_TOKEN_TTL_DAYS} "
          f"({greedy['ttl_days']})")


# ── minting ─────────────────────────────────────────────────────────────────

def test_minting_needs_the_master():
    """A capability token that could mint would make expiry decorative."""
    _neuter()
    for cap in cg.APP_CAPABILITIES:
        r = client.post("/app-tokens", json={},
                        headers={"Authorization": f"Bearer {cg.mint_app_token(cap)}"})
        check(r.status_code == 401,
              f"the {cap} token cannot mint another ({r.status_code})")


def test_the_mint_hands_back_one_token_per_capability():
    _neuter()
    body = client.post("/app-tokens", json={},
                       headers={"Authorization": f"Bearer {MASTER}"}).json()
    check(set(body["tokens"]) == set(cg.APP_CAPABILITIES),
          f"one token per capability: {sorted(body['tokens'])}")
    for cap, token in body["tokens"].items():
        check(cg.read_app_token(token, cap) == "capability",
              f"the minted {cap} token verifies as {cap}")


def test_a_minted_token_carries_no_part_of_the_master():
    _neuter()
    for token in client.post("/app-tokens", json={},
                             headers={"Authorization": f"Bearer {MASTER}"}
                             ).json()["tokens"].values():
        check(MASTER not in token, "the master is not inside the token it derives")


# ── forgery, and rotation as revocation ─────────────────────────────────────

def test_a_forged_signature_is_refused():
    _neuter()
    good = cg.mint_app_token("memory")
    prefix, cap, exp, mac = good.split(".")
    forged = ".".join([prefix, cap, exp, mac[:-4] + "AAAA"])
    check(cg.read_app_token(forged, "memory") == "bad", "a tampered mac is refused")
    # and the obvious forgery: a longer expiry with the old signature
    stretched = ".".join([prefix, cap, str(int(exp) + 86400 * 365), mac])
    check(cg.read_app_token(stretched, "memory") == "bad",
          "an expiry extended by hand does not verify")
    check(cg.read_app_token("j1.memory.not-a-number.x", "memory") == "bad",
          "a malformed expiry is refused rather than raising")
    check(cg.read_app_token("j1.admin.99999999999.x", "admin") == "bad",
          "an invented capability is refused")
    check(cg.read_app_token("", "memory") == "bad", "an empty credential is refused")


def test_rotating_the_master_revokes_every_derived_token():
    """The property that makes rotating a leaked secret survivable: there is no
    table of issued tokens to clean up, because there was never a table."""
    _neuter()
    before = cg.mint_app_token("memory")
    check(cg.read_app_token(before, "memory") == "capability",
          "the token verifies under the master it was minted with")
    cg.APP_TOKEN = "a-rotated-master"
    try:
        check(cg.read_app_token(before, "memory") == "bad",
              "...and stops the moment the master rotates")
        r = post("/app-fact", before, {})
        check(r.status_code == 401, f"the route refuses it too ({r.status_code})")
    finally:
        cg.APP_TOKEN = MASTER


def test_a_gateway_with_no_master_configured_refuses_and_mints_nothing():
    _neuter()
    cg.APP_TOKEN = ""
    try:
        check(post("/app-fact", "anything", {}).status_code == 503,
              "no master configured: the route is 503, not open")
        check(client.post("/app-tokens", json={},
                          headers={"Authorization": "Bearer anything"}
                          ).status_code == 503,
              "...and nothing can be minted")
        check(cg.read_app_token("anything", "memory") == "bad",
              "...and no credential verifies")
    finally:
        cg.APP_TOKEN = MASTER


# ── the wiring, because this is how the sprawl happened the first time ──────

def test_no_route_compares_against_the_master_by_hand_except_the_mint():
    """Five gates came to share one secret because each spelled out its own
    comparison. Exactly one is allowed to now: the mint, which takes the master
    and nothing else."""
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    # only the ROUTES half: `read_app_token` compares against the master too, and
    # that one is the gate itself rather than a route going around it
    routes = src.split('@app.get("/")')[1]
    hand_rolled = routes.count("hmac.compare_digest(presented, APP_TOKEN)")
    check(hand_rolled == 1,
          f"exactly one route compares against the master by hand, and it is the "
          f"mint (found {hand_rolled})")
    mint = src.split('@app.post("/app-tokens")')[1].split("@app.post")[0]
    check("hmac.compare_digest(presented, APP_TOKEN)" in mint,
          "...and it is the mint that has it")


def test_every_app_route_declares_a_capability():
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    wanted = {'"memory"', '"say"', '"push"', '"state"'}
    used = {line.split("app_auth(presented, ")[1].split(",")[0]
            for line in src.splitlines() if "app_auth(presented, " in line}
    check(used == wanted, f"every gate names its capability: {sorted(used)}")
    check('read_app_token(presented, "link")' in src,
          "the socket declares one too, and it is `link`")


def test_the_mac_is_what_it_claims_to_be():
    """Recomputed here from the primitives rather than trusted: an implementation
    that quietly changed to something weaker would still pass every test above."""
    _neuter()
    token = cg.mint_app_token("push")
    _, cap, exp, mac = token.split(".")
    body = f"j1.{cap}.{exp}".encode("utf-8")
    expect = base64.urlsafe_b64encode(
        hmac.new(MASTER.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    check(mac == expect, "the signature is HMAC-SHA256 over the token body")
    check(len(base64.urlsafe_b64decode(mac + "==")) == 32,
          "...and the full digest, not a prefix of one")


# ── rotating the bridge secret without locking the desk out ──────────────
#
# The `bridge-secret` row of the same goal: the live value went through Render's
# access log before redaction landed, and it still opens `/desk-link`. It stayed
# live for weeks, and the row names why - "the only item whose cost grows while
# deferred".
#
# What blocked it was ordering, not work. The gateway and the desk read the same
# secret from two different places, so whichever moved first locked the other
# out - and the desk is a machine that may be off when the change is made.
# Accepting the outgoing secret for one window removes the ordering entirely.


def test_the_current_bridge_secret_is_accepted():
    _neuter()
    cg.BRIDGE_SECRET = "new-secret"
    cg.BRIDGE_SECRET_OLD = ""
    check(cg.bridge_secret_ok("new-secret") == "current",
          "the current secret opens the desk link")
    check(cg.bridge_secret_ok("something-else") == "no",
          "anything else is refused")
    check(cg.bridge_secret_ok("") == "no", "an empty header is refused")


def test_the_outgoing_secret_is_accepted_but_named_as_old():
    """Named rather than silently as good: a secret known to have reached a log
    is not equivalent to the one replacing it, and the point of accepting it at
    all is to reach the moment it can be deleted."""
    _neuter()
    cg.BRIDGE_SECRET = "new-secret"
    cg.BRIDGE_SECRET_OLD = "the-leaked-one"
    check(cg.bridge_secret_ok("new-secret") == "current", "the new secret works")
    check(cg.bridge_secret_ok("the-leaked-one") == "old",
          "the desk that has not moved yet still connects")
    check(cg.bridge_secret_ok("neither") == "no", "and nothing else does")


def test_deleting_the_old_secret_closes_the_door_again():
    _neuter()
    cg.BRIDGE_SECRET = "new-secret"
    cg.BRIDGE_SECRET_OLD = ""
    check(cg.bridge_secret_ok("the-leaked-one") == "no",
          "with the old value removed, the leaked secret is refused")


def test_an_unset_bridge_secret_opens_nothing():
    """The empty string is not a password, and `compare_digest("", "")` is True -
    which is exactly the shape of accident that leaves a door open."""
    _neuter()
    cg.BRIDGE_SECRET = ""
    cg.BRIDGE_SECRET_OLD = ""
    check(cg.bridge_secret_ok("") == "no", "an unconfigured gateway accepts nothing")
    check(cg.bridge_secret_ok("anything") == "no", "...not even a guess")


def test_health_says_a_rotation_is_under_way_and_when_it_is_finished():
    _neuter()
    cg.BRIDGE_SECRET = "new-secret"
    cg.BRIDGE_SECRET_OLD = "the-leaked-one"
    cg._legacy_bridge_use = 3
    try:
        rot = client.get("/health").json()["bridge_rotation"]
        check(rot == {"old_accepted": True, "connects_on_old": 3},
              f"/health carries the rotation and its count: {rot}")
        cg.BRIDGE_SECRET_OLD = ""
        check(client.get("/health").json()["bridge_rotation"] is None,
              "...and says nothing at all when no rotation is under way")
    finally:
        cg._legacy_bridge_use = 0


def test_the_desk_link_reads_the_verdict_rather_than_comparing_by_hand():
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    link = src.split("async def desk_link(")[1].split("@app.")[0]
    check("bridge_secret_ok(presented)" in link,
          "the desk link asks the one helper")
    check("compare_digest(presented, BRIDGE_SECRET)" not in link,
          "...and does not compare the secret itself, which is how the two "
          "accepted values would drift apart")


# ── which build is answering ───────────────────────────────────
#
# Not a security row, and it belongs here anyway: every rule above is a claim
# about the code that is RUNNING, and until this field existed `/health` could
# confirm the service was up but never that it was the service you just pushed.
# That is a whole class of afternoon - a fix deployed, a symptom unchanged, and
# no way to tell which of the two did not happen.


def test_health_names_the_commit_it_is_running():
    _neuter()
    was = os.environ.get("RENDER_GIT_COMMIT")
    os.environ["RENDER_GIT_COMMIT"] = "0123456789abcdef"
    try:
        check(client.get("/health").json()["commit"] == "0123456",
              "the deployed commit is on /health, short-form")
    finally:
        if was is None:
            os.environ.pop("RENDER_GIT_COMMIT", None)
        else:
            os.environ["RENDER_GIT_COMMIT"] = was


def test_health_says_nothing_rather_than_guessing_off_render():
    _neuter()
    was = os.environ.pop("RENDER_GIT_COMMIT", None)
    try:
        # empty, not "unknown" and certainly not the local HEAD: a desk run has
        # no deployed commit, and inventing one would make the field a liar in
        # exactly the situation it exists to clarify
        check(client.get("/health").json()["commit"] == "",
              "off Render the field is empty rather than invented")
    finally:
        if was is not None:
            os.environ["RENDER_GIT_COMMIT"] = was


if __name__ == "__main__":
    import traceback

    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)),
                   key=lambda nf: nf[1].__code__.co_firstlineno)
    for name, fn in tests:
        try:
            fn()
        except Exception:
            _fails.append(name)
            print(f"FAIL  {name} raised")
            traceback.print_exc()
    # The runner parses "<n>/<n> passed": the word "checks" between the count
    # and "passed" makes the line unparseable, and a harness it cannot parse is
    # reported as 0 checks and BROKEN - which is what this one did on its first
    # suite run, while printing 55 PASS lines above it.
    print(f"\n{_checks - len(_fails)}/{_checks} passed.")
    sys.exit(1 if _fails else 0)
