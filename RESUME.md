# RESUME — pick up here

> Written 2026-07-30, last updated 2026-08-01 at the cp1252 commit (head of
> `feat/cloud-gateway`). Read this first, then `JARVIS_MASTER_ROADMAP.md`.
> Delete or rewrite this file once the list below is empty — it is a bookmark, not a plan.

## Current state

- **C#11a memory-at-rest encryption: DONE, live, and on origin.** `jarvis_longterm.db` is
  encrypted (58 rows, all verified to decrypt back byte-identical); `jarvis_memory.db` is
  retired into it; keys exist and the recovery code is in Kaustav's hands.
- **Branch `feat/cloud-gateway` is level with origin**, at the cp1252 commit that carries this
  file. Not merged to `main`.
- **Suite: 876 checks / 39 harnesses, 0 failed.** One command:
  `venv\Scripts\python.exe run_harnesses.py` (use the venv python — system python fakes failures).
- **cp1252 is closed** (2026-08-01, committed and on origin): the three encryption CLIs
  reconfigure stdout, and `test_governance.py` guards both halves of the fix. Details below.
- Working tree clean apart from the pre-existing untracked `jarvis-frontend/public/favicon.zip`.

The commits that got here:

| Hash | What |
|---|---|
| `312bf5c` | encryption subsystem (DPAPI + scrypt recovery, AES-256-GCM fields, blind-index dedup) |
| `e93cc34` | D#13 harness conversions, `tests/` retired, the cp1252 crash fixed |
| `c2d1a8c` | docs — C#11a folded into the roadmap, stale rows fixed, lock live-gate added |
| head | cp1252: the three encryption CLIs hardened + two guards in `test_governance.py` |

## Done 2026-08-01 (items 1 and 2 of the old list)

- **The three encryption CLIs are hardened.** `manage_keys.py`, `migrate_memory_encryption.py`
  and `retire_jarvis_memory_db.py` each reconfigure stdout/stderr to UTF-8 after their imports
  (the `watchdog.py` placement, not `main.py`'s — these files open with
  `from __future__ import annotations`, which must stay the first statement).
  Verified the real way: `PYTHONIOENCODING=cp1252` with stdout piped — a plain `print('→')`
  dies with `UnicodeEncodeError` in that exact shell, and all three CLIs then ran their
  read-only modes (`status`, `--report`, `--report`) to `exit=0`.
- **Two guards in `test_governance.py`** (suite 874 → 876):
  `test_check_survives_a_strict_cp1252_stdout` drives every tier through a
  `cp1252 / errors="strict"` stdout, and self-checks that the stream is genuinely strict before
  trusting the result — so it can't pass vacuously. `test_run_harnesses_forces_utf8_on_children`
  asserts both `_CHILD_ENV["PYTHONIOENCODING"] == "utf-8"` *and* that `main()` still passes
  `env=_CHILD_ENV`, since dropping either one silently reopens the hole.
- Deliberately *not* done: a repo-wide non-ASCII lint. 169 such prints across 44 files — a hard
  gate would be switched off within a week.

## Next session, in priority order

1. **AWAITING KAUSTAV'S SIGN-OFF — decisions, not builds.** No code until he rules:
   - **Step 3** — move `.env` secrets into the key store. Deliberately sequenced last.
   - **Cloud→desk sealed fact backlog** — turns the cloud brain answered while the desk was OFF
     are never persisted. Design is in roadmap §5 #11a; the X25519 keypair it needs already
     exists from the Step 1 ceremony.

2. **The big live-gate desk session (roadmap §7)** — needs his hands, a phone, and a second
   person for the stranger-debounce row. Includes the new C#11a "locked, not amnesia" gate.
   This blocks Electron packaging, which blocks the mobile app.

## Off-machine TODO (no code, only Kaustav can do these)

- [ ] **Confirm the recovery code is stored OFF this disk** — password manager or paper. If both
      it and the Windows profile are lost, the encrypted memory is unreadable. Permanently.
- [x] Moved-aside plaintext original still on disk — verified 2026-07-30 at
      `F:\work\JARVIS-BACKUPS\plaintext-originals\jarvis_longterm.db.plaintext-20260730-002550`
      (28 KB). Keep until he is satisfied the encrypted store is trustworthy, then delete it
      deliberately — it is the last plaintext copy of his memory.
- [ ] The `JARVIS-BACKUPS` folder contains `.env` in the clear. Keep it on this machine — do not
      sync, zip to a shared drive, or upload it.

## Two things worth not re-learning

- **`UNIQUE(user, content)` cannot work on encrypted columns** — random nonces mean the same fact
  never produces the same ciphertext. Dedup lives in the keyed blind index `memories.content_hash`.
  Any future encrypted column needs the same treatment.
- **A crashed harness reports `0 failed`.** `run_harnesses.py` counts `broken` separately, so the
  line to trust is `N/N harnesses green`, never `0 failed` on its own.
