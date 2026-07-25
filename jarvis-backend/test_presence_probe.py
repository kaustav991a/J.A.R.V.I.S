"""Harness for modules/presence_probe.py (Track B phone presence).

Fake `arp`/`ping` output, fake TCP connect, fake clock — no network, no phone.
The asymmetric debounce is the part worth guarding: a phone sleeps its WiFi radio,
so treating one miss as "he left" would make JARVIS go quiet while he's reading.
"""

from modules import presence_probe as pp

WIN_ARP = """
Interface: 192.168.0.104 --- 0x8
  Internet Address      Physical Address      Type
  192.168.0.1           a0-b1-c2-d3-e4-f5     dynamic
  192.168.0.105         11-22-33-44-55-66     dynamic
  192.168.0.255         ff-ff-ff-ff-ff-ff     static
"""

LINUX_ARP = """
192.168.0.1 dev wlan0 lladdr a0:b1:c2:d3:e4:f5 REACHABLE
192.168.0.105 dev wlan0 lladdr 11:22:33:44:55:66 STALE
"""


# ---- parsing ------------------------------------------------------------ #

def test_normalise_mac_accepts_both_separators():
    assert pp.normalise_mac("11-22-33-44-55-66") == "112233445566"
    assert pp.normalise_mac("11:22:33:44:55:66") == "112233445566"
    assert pp.normalise_mac("AA:bb:CC:dd:EE:ff") == "aabbccddeeff"
    for bad in (None, "", "11-22-33", "not a mac", "112233445566778899"):
        assert pp.normalise_mac(bad) is None, bad


def test_parse_arp_handles_windows_and_linux():
    for text in (WIN_ARP, LINUX_ARP):
        pairs = pp.parse_arp_table(text)
        assert ("192.168.0.105", "112233445566") in pairs, text[:20]
        # the broadcast row must not look like a device
        assert all(m != "ffffffffffff" for _, m in pairs)


def test_parse_arp_survives_garbage():
    assert pp.parse_arp_table("") == []
    assert pp.parse_arp_table("no addresses here") == []
    assert pp.parse_arp_table("192.168.0.5 without a mac") == []


def test_arp_hit_prefers_mac_over_ip():
    # MAC match wins because it survives a DHCP move — this is the whole reason
    # JARVIS_PHONE_MAC is worth pinning
    assert pp.arp_hit(WIN_ARP, ip="192.168.0.9", mac="11:22:33:44:55:66") == "mac"
    assert pp.arp_hit(WIN_ARP, ip="192.168.0.105", mac=None) == "ip"
    assert pp.arp_hit(WIN_ARP, ip="192.168.0.9", mac="99:99:99:99:99:99") is None
    assert pp.arp_hit("", ip="192.168.0.105") is None


def test_ping_succeeded_reads_the_text_not_the_exit_code():
    assert pp.ping_succeeded("Reply from 192.168.0.105: bytes=32 time=3ms TTL=64") is True
    # Windows exits 0 for this — the text is the only honest signal
    assert pp.ping_succeeded("Reply from 192.168.0.1: Destination host unreachable.") is False
    assert pp.ping_succeeded("Request timed out.") is False
    assert pp.ping_succeeded("1 packets transmitted, 0 received, 100% packet loss") is False
    assert pp.ping_succeeded("") is False


def test_ping_cmd_sends_exactly_one_packet():
    cmd = pp.ping_cmd("192.168.0.105", 800)
    assert cmd[0] == "ping" and "192.168.0.105" in cmd
    assert ("-n" in cmd and "1" in cmd) or ("-c" in cmd and "1" in cmd)


# ---- debounce ----------------------------------------------------------- #

def test_any_hit_means_home_immediately():
    d = pp.PresenceDebounce(away_grace_s=180)
    assert d.state == pp.UNKNOWN
    assert d.update(True, 0.0) == pp.HOME
    assert d.changed is True


def test_away_needs_the_full_grace():
    d = pp.PresenceDebounce(away_grace_s=180)
    d.update(True, 0.0)
    assert d.update(False, 60.0) == pp.HOME     # radio asleep, not gone
    assert d.update(False, 179.0) == pp.HOME
    assert d.update(False, 180.0) == pp.AWAY
    assert d.changed is True


def test_a_single_hit_resets_the_away_countdown():
    d = pp.PresenceDebounce(away_grace_s=180)
    d.update(True, 0.0)
    d.update(False, 170.0)
    d.update(True, 175.0)                        # phone woke up for one probe
    assert d.update(False, 350.0) == pp.HOME     # countdown restarted at 175
    assert d.update(False, 356.0) == pp.AWAY


def test_cold_start_never_claims_home():
    """A phone that was never seen must not be reported HOME — and the grace is
    measured from the first probe, so a boot-with-nobody-home still reaches AWAY."""
    d = pp.PresenceDebounce(away_grace_s=180)
    assert d.update(False, 0.0) == pp.UNKNOWN
    assert d.update(False, 100.0) == pp.UNKNOWN
    assert d.update(False, 181.0) == pp.AWAY


def test_changed_flag_only_fires_on_transitions():
    d = pp.PresenceDebounce(away_grace_s=10)
    d.update(True, 0.0)
    assert d.changed is True
    d.update(True, 1.0)
    assert d.changed is False        # still HOME — don't log/notify every probe


# ---- fusion + routing --------------------------------------------------- #

def test_face_gate_outranks_the_lan_probe():
    # on camera = at the desk, whatever the phone is doing (charging elsewhere,
    # radio asleep, left at the office)
    assert pp.fuse(True, pp.AWAY) == pp.AT_DESK
    assert pp.fuse(True, pp.UNKNOWN) == pp.AT_DESK
    assert pp.fuse(False, pp.HOME) == pp.HOME
    assert pp.fuse(False, pp.AWAY) == pp.AWAY
    assert pp.fuse(False, pp.UNKNOWN) == pp.UNKNOWN


def test_routing_matches_the_spec():
    assert pp.routing(pp.AT_DESK) == {"speak": True, "phone": False}
    assert pp.routing(pp.HOME) == {"speak": True, "phone": True}
    assert pp.routing(pp.AWAY) == {"speak": False, "phone": True}


def test_unknown_presence_routes_everywhere():
    """The one failure mode an alert path must not have is silence: an
    unconfigured or not-yet-probed system behaves like the pre-Track-B build."""
    assert pp.routing(pp.UNKNOWN) == {"speak": True, "phone": True}
    assert pp.routing("nonsense") == {"speak": True, "phone": True}


# ---- desk presence ------------------------------------------------------ #

def test_stale_desk_sighting_is_not_at_the_desk():
    pp.note_desk_presence(True, now=1000.0)
    assert pp.at_desk(max_age_s=10.0, now=1005.0) is True
    # a sighting older than the window means the daemon died, NOT that he's there
    assert pp.at_desk(max_age_s=10.0, now=1020.0) is False
    pp.note_desk_presence(False, now=2000.0)
    assert pp.at_desk(max_age_s=10.0, now=2001.0) is False


# ---- config ------------------------------------------------------------- #

def test_config_disabled_without_ip_or_mac():
    assert pp.PresenceConfig.from_env({}).configured is False
    assert pp.PresenceConfig.from_env({"JARVIS_PHONE_IP": "192.168.0.105"}).configured is True
    assert pp.PresenceConfig.from_env({"JARVIS_PHONE_MAC": "11:22:33:44:55:66"}).configured is True
    # a malformed MAC is not configuration
    assert pp.PresenceConfig.from_env({"JARVIS_PHONE_MAC": "nope"}).configured is False


def test_config_reads_env():
    cfg = pp.PresenceConfig.from_env({
        "JARVIS_PHONE_IP": " 192.168.0.105 ",
        "JARVIS_PRESENCE_PORTS": "8080, 4747, junk",
        "JARVIS_PRESENCE_INTERVAL": "30",
        "JARVIS_PRESENCE_AWAY_GRACE": "240",
    })
    assert cfg.phone_ip == "192.168.0.105"
    assert cfg.ports == (8080, 4747)
    assert cfg.interval_s == 30.0 and cfg.away_grace_s == 240.0
    # bogus values fall back instead of raising
    bad = pp.PresenceConfig.from_env({"JARVIS_PHONE_IP": "1.2.3.4",
                                      "JARVIS_PRESENCE_INTERVAL": "soon"})
    assert bad.interval_s == 60.0 and bad.ports == pp.DEFAULT_PORTS


# ---- the ladder --------------------------------------------------------- #

def _runner(arp_text="", ping_text=""):
    calls = []

    def run(cmd, timeout=4.0):
        calls.append(cmd[0])
        return arp_text if cmd[0] == "arp" else ping_text

    return run, calls


def test_arp_hit_short_circuits_the_ladder():
    cfg = pp.PresenceConfig(phone_ip="192.168.0.105", phone_mac="11:22:33:44:55:66")
    run, calls = _runner(arp_text=WIN_ARP)
    hit, how = pp.probe_once(cfg, run=run,
                             connect=lambda *a: (_ for _ in ()).throw(OSError()))
    assert (hit, how) == (True, "arp:mac")
    assert "arp" in calls                    # primed with a ping, then read ARP


def test_tcp_rung_answers_when_arp_is_empty():
    cfg = pp.PresenceConfig(phone_ip="192.168.0.105", ports=(8080, 4747))
    run, _ = _runner(arp_text="")
    tried = []

    def connect(addr, timeout):
        tried.append(addr[1])
        if addr[1] != 4747:
            raise OSError("refused")

    hit, how = pp.probe_once(cfg, run=run, connect=connect)
    assert (hit, how) == (True, "tcp:4747")
    assert tried == [8080, 4747]              # in configured priority order


def test_icmp_is_the_last_rung():
    cfg = pp.PresenceConfig(phone_ip="192.168.0.105", ports=(8080,))
    run, _ = _runner(arp_text="", ping_text="Reply from 192.168.0.105: TTL=64")
    hit, how = pp.probe_once(cfg, run=run,
                             connect=lambda *a: (_ for _ in ()).throw(OSError()))
    assert (hit, how) == (True, "icmp")


def test_every_rung_missing_is_a_clean_miss():
    cfg = pp.PresenceConfig(phone_ip="192.168.0.105", ports=(8080,))
    run, _ = _runner(arp_text="", ping_text="Request timed out.")
    hit, how = pp.probe_once(cfg, run=run,
                             connect=lambda *a: (_ for _ in ()).throw(OSError()))
    assert (hit, how) == (False, None)


def test_mac_only_config_never_touches_tcp_or_ping():
    """With no IP there is nothing to connect to — probing must not invent one."""
    cfg = pp.PresenceConfig(phone_ip=None, phone_mac="11:22:33:44:55:66")
    run, calls = _runner(arp_text="")
    hit, how = pp.probe_once(cfg, run=run,
                             connect=lambda *a: (_ for _ in ()).throw(AssertionError("no TCP")))
    assert (hit, how) == (False, None)
    assert calls == ["arp"]                   # no ping, no connect


# ---- monitor (what production actually consumes) ------------------------ #

def test_tick_publishes_snapshot_and_routing():
    cfg = pp.PresenceConfig(phone_ip="192.168.0.105", away_grace_s=180)
    mon = pp.PresenceMonitor(cfg)
    hits = [True, False]
    real_probe = pp.probe_once
    pp.probe_once = lambda c, **kw: (hits.pop(0), "arp:ip")   # type: ignore[assignment]
    try:
        _tick_body(mon)
    finally:
        pp.probe_once = real_probe        # module global — restore or later tests inherit it


def _tick_body(mon):
    pp.note_desk_presence(False, now=0.0)
    assert mon.tick(now=0.0) == pp.HOME
    snap = pp.snapshot()
    assert snap["lan"] == pp.HOME and snap["presence"] == pp.HOME
    assert snap["how"] == "arp:ip" and snap["at_desk"] is False
    assert pp.presence_routing() == {"speak": True, "phone": True}

    # he sits down: the face gate outranks the probe, so no phone buzz
    pp.note_desk_presence(True)
    assert pp.snapshot()["presence"] == pp.AT_DESK
    assert pp.presence_routing() == {"speak": True, "phone": False}

    # ...and once he's gone AND the phone is gone, stop talking to the room
    pp.note_desk_presence(False, now=0.0)
    mon.tick(now=1000.0)
    assert pp.snapshot()["lan"] == pp.AWAY
    assert pp.presence_routing() == {"speak": False, "phone": True}


def test_tick_survives_a_probe_fault():
    """A broken/absent `arp` binary must degrade to a miss, not kill the thread."""
    cfg = pp.PresenceConfig(phone_ip="192.168.0.105")
    mon = pp.PresenceMonitor(cfg)

    def boom(c, **kw):
        raise OSError("arp: command not found")

    real_probe = pp.probe_once
    pp.probe_once = boom     # type: ignore[assignment]
    try:
        assert mon.tick(now=0.0) == pp.UNKNOWN      # no crash, no false HOME
    finally:
        pp.probe_once = real_probe


def test_monitor_refuses_to_start_unconfigured():
    mon = pp.PresenceMonitor(pp.PresenceConfig())
    mon.start()
    assert mon.running is False and mon.thread is None


if __name__ == "__main__":
    import sys
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
