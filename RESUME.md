# RESUME — pick up here

> Written 2026-07-30, rewritten 2026-08-01 at the head of `feat/cloud-gateway`.
> Read this first, then `JARVIS_MASTER_ROADMAP.md` (the single source of truth).
> Delete or rewrite this file once the list below is empty — it is a bookmark, not a plan.

## THE ENCRYPTION ARC (C#11a) IS FULLY CLOSED

Nothing about memory-at-rest encryption is outstanding. Do not reopen it looking for work.

- `jarvis_longterm.db` is encrypted; `jarvis_memory.db` is retired into it. **58/58 rows
  decrypt** through DPAPI → DEK → AES-256-GCM (re-verified 2026-08-01, counts only).
- **Recovery code: re-issued, saved off-disk, and PROVEN.** A fresh code was issued
  2026-08-01 (`manage_keys.py export-key`), which **voided every earlier code** — including
  the misplaced one and any that had touched a terminal transcript. Kaustav holds the new one
  in his password manager, off this disk. He ran the round-trip himself: **recovery wrap
  opens · MATCHES the DPAPI key · canary decrypts — all True.** So both wraps are proven to
  open the *same* DEK, which is the property that matters.
- Unattended boot is unaffected: `status` reports `boot wrap: unwraps OK (no prompt needed)`.
- The three encryption CLIs are cp1252-hardened, guarded by two tests in `test_governance.py`.

## Current state

| | |
|---|---|
| Branch | `feat/cloud-gateway`, level with origin, **not merged to `main`** |
| Suite | **876 checks / 39 harnesses, 0 failed** — `venv\Scripts\python.exe run_harnesses.py` (venv python; system python fakes failures) |
| Working tree | clean apart from the pre-existing untracked `jarvis-frontend/public/favicon.zip` |

⚠️ **The merge to `main` is not a fast-forward.** `main` carries one commit this branch does
not: **`8d0ea4f`** — *"Add GNU General Public License v3"*, 2026-07-27, one file, `LICENSE`,
+674 lines. A clean add with no overlap against this branch's 77 commits, so expect no
conflict — but it has to be reconciled rather than ignored.

The commits that got here:

| Hash | What |
|---|---|
| `312bf5c` | encryption subsystem (DPAPI + scrypt recovery, AES-256-GCM fields, blind-index dedup) |
| `e93cc34` | D#13 harness conversions, `tests/` retired, the cp1252 crash fixed |
| `c2d1a8c` + `dc84a88` | docs — C#11a folded into the roadmap, stale rows fixed, resume state |
| `9c8c5eb` | cp1252: the three encryption CLIs hardened + two guards in `test_governance.py` |
| `5093c37` | docs — six stale roadmap/TEST_PLAN rows reconciled against the tree at 876/39 |

## NEXT SESSION STARTS HERE — two decisions, both Kaustav's

**No code on either until he rules.** These are the real resume point; everything else on the
board is either done or waiting on a desk session. Priority order:

1. **Step 3 — move `.env` secrets into the key store.**
   Context so it need not be re-derived: `.env` currently holds every API key in plaintext,
   and the key store that would protect them already exists and is proven (DEK + DPAPI wrap +
   verified recovery code). It was **deliberately sequenced last** in the C#11a plan because
   it is separable and touches every subsystem that reads a key at boot — the cloud gateway,
   the LLM cascade, the Telegram legs. The decision he owes is *whether* to do it, not how.

2. **Cloud→desk sealed fact backlog.**
   The gap: turns the cloud brain answers while the desk is OFF are **never persisted** —
   `cloud_gateway.py` stores nothing and Render's filesystem is ephemeral. The level-3 bridge
   already forwards correctly when the desk is UP (`b125b9a`), so this is only about the
   PC-off window. **Design is complete in roadmap §11a** and needs no new thinking: desk owns
   an X25519 keypair (**already generated in the Step 1 ceremony** — only the public half goes
   to Render), the cloud seals each turn and queues it *before* replying, one file per record
   in a private GitHub repo (durable, zero new infra, the PAT already exists), and the desk
   drains on boot/reconnect and feeds each turn through the **existing**
   `extract_and_store_memory` so attribution is unchanged by construction. Idempotent by UUID;
   filenames carry no metadata.

After those, unchanged from before: **the single live-gate desk session (roadmap §7)** — needs
his hands, a phone, and a second person for the stranger-debounce row. Then Electron launch
scripts, then the merge. That session is what blocks Electron packaging, which blocks mobile.

## Off-machine (only Kaustav can do these)

- [x] **Recovery code stored OFF this disk — DONE 2026-08-01.** Fresh code in his password
      manager; all earlier codes void; round-trip verified working (see the top section).
- [x] **The 5 cleartext `.env` copies in `JARVIS-BACKUPS` were shredded 2026-08-01** — one per
      `pre-encryption-*` folder, 9,731 bytes each. A recursive sweep now finds no `.env`
      anywhere under that tree. The live `jarvis-backend\.env` is untouched, so nothing was
      lost. Folder structure and every `.db`/`.npz`/Chroma file left intact (verified: each
      folder exactly −1 file / −9,731 bytes, `plaintext-originals` byte-identical).
- [ ] **Biometric + Chroma backup copies still owed a shred** — his task, off-tooling.
      5× `models\owner_embeddings.npz` (6,686 B each) and 5 Chroma sets per backup folder
      (`jarvis_chroma_db` / `action_chroma_db` / `personal_chroma_db` / `memory\vector_db`,
      20 `chroma.sqlite3` files in total). Chroma keeps document text **plaintext** and its
      `.bin` vectors leak approximate content via embedding inversion — the same reasoning
      that kept partner data out of Chroma entirely.
- [ ] **The plaintext memory net is KEPT BY CHOICE, not by necessity.** 2 plaintext
      `jarvis_longterm.db` copies + 5 plaintext `jarvis_memory.db` copies (that store was
      never encrypted at any point in its life), plus the tracked
      `plaintext-originals\jarvis_longterm.db.plaintext-20260730-002550`. **The recovery path
      is now proven, so this CAN be pruned at any time** — it is retained only until he calls
      the encrypted store production-ready. Three `jarvis_longterm.db` copies in the later
      `pre-encryption-*` folders are almost certainly already *encrypted* (they post-date the
      migration; sizes 40,960 / 49,152 / 53,248 grow with ciphertext overhead) — confirm with
      a hex viewer before treating them as spillage.
- Keep `JARVIS-BACKUPS` on this machine — do not sync, zip to a shared drive, or upload it.
  It sits outside the repo (`F:\work\JARVIS-BACKUPS` vs `F:\work\JARVIS-Project`) and is not
  a git repo, so no `git add` can ever reach it; nothing sensitive is tracked and every key
  path is gitignored.

## Three things worth not re-learning

- **`UNIQUE(user, content)` cannot work on encrypted columns** — random nonces mean the same
  fact never produces the same ciphertext. Dedup lives in the keyed blind index
  `memories.content_hash`. Any future encrypted column needs the same treatment.
- **A crashed harness reports `0 failed`.** `run_harnesses.py` counts `broken` separately, so
  the line to trust is `N/N harnesses green`, never `0 failed` on its own.
- **A CLI that prompts cannot be answered from the PowerShell tool** — its stdin is the null
  device, so a piped `y` never arrives and the prompt declines. `manage_keys.py export-key`
  failed safe that way ("Cancelled. Nothing was changed.") before succeeding under a shell
  that can deliver stdin. Worth knowing before assuming a key command is broken.
