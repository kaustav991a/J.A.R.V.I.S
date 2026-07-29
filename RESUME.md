# RESUME — pick up here

> Written 2026-07-30 at `c2d1a8c`. Read this first, then `JARVIS_MASTER_ROADMAP.md`.
> Delete or rewrite this file once the list below is empty — it is a bookmark, not a plan.

## Current state

- **C#11a memory-at-rest encryption: DONE, live, and on origin.** `jarvis_longterm.db` is
  encrypted (58 rows, all verified to decrypt back byte-identical); `jarvis_memory.db` is
  retired into it; keys exist and the recovery code is in Kaustav's hands.
- **Branch `feat/cloud-gateway` is level with origin** at `c2d1a8c`. Not merged to `main`.
- **Suite: 874 checks / 39 harnesses, 0 failed.** One command:
  `venv\Scripts\python.exe run_harnesses.py` (use the venv python — system python fakes failures).
- Working tree clean apart from a pre-existing untracked `jarvis-frontend/public/favicon.zip`.

The three commits that got here:

| Hash | What |
|---|---|
| `312bf5c` | encryption subsystem (DPAPI + scrypt recovery, AES-256-GCM fields, blind-index dedup) |
| `e93cc34` | D#13 harness conversions, `tests/` retired, the cp1252 crash fixed |
| `c2d1a8c` | docs — C#11a folded into the roadmap, stale rows fixed, lock live-gate added |

## Next session, in priority order

1. **Three encryption CLIs still crash on non-ASCII under cp1252.** They print `→`/`✅`-style
   characters and never call `reconfigure`, so a piped or service stdout kills them mid-run.
   Three blocks matching `main.py:7-12`:
   - `manage_keys.py` (9 such prints) — **do this one first: it prints the recovery code
     during the key ceremony**, which is the worst possible moment to die.
   - `migrate_memory_encryption.py` (15)
   - `retire_jarvis_memory_db.py` (10)

   Context: this root cause has now bitten three times. `run_harnesses.py` already passes
   `PYTHONIOENCODING=utf-8` to child harnesses, and anything imported into `main.py` inherits
   its reconfigured stdout — so the gap is exactly these standalone CLIs.

2. **Deferred guard (fix #3): cp1252 regression tests in `test_governance.py`** — one that runs
   `governance_manager.check()` against a strict cp1252 stdout (fails before the fix, passes
   after), and one asserting `run_harnesses.py` still sets `PYTHONIOENCODING` for children.
   *Not* a repo-wide non-ASCII lint: 169 such prints across 44 files, so a hard gate would be
   switched off within a week.

3. **AWAITING KAUSTAV'S SIGN-OFF — decisions, not builds.** No code until he rules:
   - **Step 3** — move `.env` secrets into the key store. Deliberately sequenced last.
   - **Cloud→desk sealed fact backlog** — turns the cloud brain answered while the desk was OFF
     are never persisted. Design is in roadmap §5 #11a; the X25519 keypair it needs already
     exists from the Step 1 ceremony.

4. **The big live-gate desk session (roadmap §7)** — needs his hands, a phone, and a second
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
