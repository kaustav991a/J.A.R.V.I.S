# RESUME — pick up here

> Written 2026-07-30, rewritten 2026-08-02 at the head of `feat/cloud-gateway`.
> Read this first, then `JARVIS_MASTER_ROADMAP.md` (the single source of truth).
> Delete or rewrite this file once the list below is empty — it is a bookmark, not a plan.

## BOTH SIGN-OFF DECISIONS ARE CLOSED

The encryption arc (C#11a) closed 2026-08-01. The **cloud→desk sealed-fact
backlog closed 2026-08-02** — all three phases built, committed and pushed. What
remains on this branch needs Kaustav's hands, not more code.

## THE ENCRYPTION ARC (C#11a) IS FULLY CLOSED — BOTH STORES

Nothing about memory-at-rest encryption is outstanding. Do not reopen it looking for work.
**Both halves now encrypt content at rest: the memory store AND the vector store.**

- `jarvis_longterm.db` is encrypted; `jarvis_memory.db` is retired into it. **58/58 rows
  decrypt** through DPAPI → DEK → AES-256-GCM (re-verified 2026-08-01, counts only).
- **`personal_chroma_db` is encrypted (2026-08-02, `c173c2e`).** Document text and the
  sensitive metadata (`path`, `name`) are sealed with the *same* C#11a field encryption before
  they reach Chroma — same DEK, same DPAPI wrap, same recovery code, no new dependency, pins
  untouched. Chroma had been keeping the text in plaintext twice (`embedding_metadata` plus
  the FTS5 shadow tables) with the metadata beside it. Gated on `keys_ready()`, and a locked
  keystore **raises** rather than returning `[]`, because an empty result set is
  indistinguishable from "no relevant documents". 15 checks in `test_chroma_crypto.py`, which
  asserts on the bytes in `chroma.sqlite3` rather than on the API.
  - **Residual, accepted:** the **vectors stay plaintext**. They are computed from the
    plaintext (that is what makes semantic search work) and the encoder `all-MiniLM-L6-v2` is
    public, so they leak approximate content by inversion. Encrypting them would destroy the
    search the store exists for. Pinned by `test_the_vectors_are_deliberately_not_encrypted`.
  - **Applies to documents ingested from now on.** There was no migration because the store
    held no real documents — only **3 rows of stale test-fixture residue** (dated 2026-07-26,
    pointing at a since-deleted `%TEMP%\tmpjbkgwzac\decisions.md`). Those rows are still
    plaintext and are *still returned by search*, injecting a fake "PostgreSQL over MongoDB /
    Hetzner not AWS" decision into results. Deleting `jarvis-backend/personal_chroma_db/`
    clears them; it is gitignored and rebuilt on next ingest. **Kaustav has not yet ruled on
    this — do not delete it unasked.**
  - **Still plaintext, out of scope, real data:** `jarvis_chroma_db` (119 rows —
    `jarvis_memory` + `jarvis_episodes`, written by `memory.py:366` and
    `episodic_memory.py:110`) and `memory/vector_db` (1 row). These are the *vector mirror* of
    facts C#11a already sealed in SQLite — the same sentence is ciphertext in
    `jarvis_longterm.db` and readable in `jarvis_chroma_db/chroma.sqlite3`. `chroma_crypto` is
    collection-parametrised so the pattern extends directly, but unlike the RAG store these
    have real rows and would need a migration. `action_chroma_db` (42 rows) is a static
    command catalogue, not secret.
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
| Suite | **1035 checks / 45 harnesses green, 0 failed, 0 broken** at this commit — `venv\Scripts\python.exe run_harnesses.py` (venv python; system python fakes failures) |
| Working tree | carries **unrelated uncommitted work that is not part of this commit**: an additive `source` column on `memories` (`memory_manager.py`, `modules/fact_sink.py`, `test_fact_governance.py`, untracked `migrate_memory_source.py` + `test_memory_source.py`, a `.gitignore` hunk) plus the pre-existing untracked `jarvis-frontend/public/favicon.zip`. With that work in the tree the suite reads **1061 checks / 46 harnesses**. It was left staged-out deliberately — whoever owns it should commit it separately. `run_harnesses.py` carries one line from each arc, so each commit stages only its own line. ⚠️ **It is code-complete but migration-incomplete:** the `source` column exists in the live `jarvis_longterm.db` (added by the boot-time metadata-only `ALTER`) but all 58 rows are NULL — `migrate_memory_source.py` has never been run. Reads still behave (`_row_source()` maps NULL to `desk`), and no failed-migration sidecar is on disk. |

Note for anyone running the suite from a **bare checkout** (fresh clone, or a `git worktree`):
`test_memory_store_encryption.py`, `test_store_retirement.py` and `test_gmail_agent.py` fail
there — 12 checks — because the keystore and the local `.db` files are gitignored and so are
absent, which silently means *encryption is off* (`a locked key returned [...] instead of
raising`). Pre-existing and expected, not a regression. `test_chroma_crypto.py` self-skips
its encryption cases in that situation instead of failing.

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
| `1b37558` | **C#11a Step 4 phases 1+2** — sealed-fact seal/unseal core + queue/bridge transport (NOT wired to live memory) |
| `fee66a0` | **C#11a Step 4 phase 3** — the governed sink; the cloud→desk arc is CLOSED |
| `f84f644` | **`message_partner` actually works** — the approved send was refused by its own confirm prompt on 100% of attempts; + `test_partner_send_gate.py`, the first partner harness that asserts on transport call count instead of source text |
| `c173c2e` | **the Chroma RAG store is encrypted at rest** — text + sensitive metadata sealed with the existing C#11a field encryption, vector left plaintext for search; blind-index companions so `where` filters and the re-ingest delete still work against randomised ciphertext; + `test_chroma_crypto.py` (15) |
| `ba12cc1` | **the butler — `partner_contact_status`, the INBOUND half of partner-messaging** — "did she message" answered from a content-free encrypted contact-event store; admin-only via `tier_allows`; urgency is a write-time boolean; + `test_partner_contact.py` (25) |

## THE CLOUD→DESK SEALED-FACT BACKLOG IS COMPLETE

All three phases built, committed and pushed. Nothing here is outstanding; do not reopen it
looking for work. 92 checks across `test_fact_seal.py` (29), `test_fact_transport.py` (30) and
`test_fact_governance.py` (33).

Both design rulings held: transport is the **existing BRIDGE_SECRET WebSocket bridge** (no
GitHub queue, no new service, no new secret on Render), and the desk private unseal key is
**DPAPI-wrapped** through the C#11a chain (DPAPI → DEK → X25519 private, straight from the
Step 1 ceremony).

- PyNaCl `crypto_box_seal`, ephemeral keypair per record, so **Render cannot re-open its own
  queue** — asserted, not claimed.
- Cloud seals + queues each PC-off turn **before** replying; outbox flushes on the desk's
  `fact_key` handshake; records leave the outbox **on the ack, not the send**.
- Two dedup layers: record-UUID ledger (`jarvis_fact_ledger.db`) stops a replay before it is
  unsealed; the C#11a `content_hash` blind index stops the same fact arriving as a NEW record.
- `modules/fact_sink.py` is the **only** way a drained fact reaches memory, and it runs
  `governance_manager.check("remember_fact")` before the write, then hands off to
  `memory_manager.extract_and_persist` — the same call `brain.extract_and_store_memory`
  delegates to, minus that wrapper's catch-all (it would have silently eaten a backlog).
- Poison AND refused records dead-letter to `fact_quarantine/` **and are acked**, so neither
  can wedge the queue. A locked key store or a broken governance engine HOLDS instead: acks
  nothing, quarantines nothing, retries on the next connect.

### Four things about it worth not re-deriving

- **`tier_allows` is deliberately NOT on the drain path**, and was left untouched. It answers
  "may this CALLER INVOKE this action"; a memory extraction is not caller-invoked live or
  drained (`main.py` fires it for every recognised identity, partners included, outside the
  action pipeline). Applying it would have stored *less* than the live path, silently, for
  exactly one person. The gate that does apply is fail-closed identity: `who` must be in the
  roster derived from `partner_registry.SLOTS`, `tier` must be one this desk issues. A harness
  test pins the VIP allowlist so this cannot drift.
- **An unattended CONFIRM is refused AND its pending slot cancelled.** `check()` parks a
  CONFIRM in a single slot before returning; leaving it there would let the next spoken "yes",
  meant for something else, approve a write he never saw.
- **Refusal reasons carry no payload values** — they are written verbatim into the unencrypted
  dead-letter file, so lengths, types and field names only. The sealed record sits beside them
  for whoever holds the key. Same rule for the ledger: a refused record's claimed `who` is the
  field that just failed to check out, so it is not persisted.
- **Drained rows carry NO provenance.** A drained fact is stored as `(Fact, ciphertext, WHO)` —
  byte-for-byte indistinguishable from a live desk write. The `"cloud_fact_drain"` string
  exists only in the payload handed to the governance check and is **not** persisted. If that
  property is ever wanted, it needs an additive `source` column on `memories` threaded through
  `add_memory` — a separate, reviewable change, not a tweak.

### Next in the queue, in order

1. **THE LIVE-GATE DESK SESSION (roadmap §7) — this is what is next, and it is his.**
   No code is blocking it. It needs his hands, a phone, and a second person for the
   stranger-debounce row. It carries every owed gate: G4 + G5 + §6.1, §17.6–17.8 (backdoor
   governance), §23 (agentic core), §24 (partner messaging). **That session is what blocks
   Electron launch scripts, which block Electron packaging, which blocks mobile** — and it
   gates the merge to `main`.

2. **Step 3 — move `.env` secrets into the key store. ⏸ DEFERRED 2026-08-01 (Kaustav),
   triggered by item 1.** **Not dropped.** Resume it *after* the §7 session **and** after the
   merge to `main`, because it rewrites every boot-time key read (cloud gateway, LLM
   cascade, Telegram legs) and doing that underneath an un-gated tree adds variables to the one
   session that needs his hands. **Whoever reads this after the merge lands: this is due.**
   Context so it need not be re-derived: `.env` currently holds every API key in plaintext
   (37 keys — `GROQ_API_KEYS`, `TELEGRAM_BOT_TOKEN`, `BRIDGE_SECRET`, `TAVILY_API_KEY`, …),
   and the key store that would protect them already exists and is proven (DEK + DPAPI wrap +
   verified recovery code). It was **deliberately sequenced last** in the C#11a plan because
   it is separable. Exposure while deferred is local-disk only: `.env` is gitignored and has
   never been tracked, and the five cleartext backup copies were shredded 2026-08-01.

3. **Partner-outbound (`message_partner`) — DONE 2026-08-02, and it had never worked.**
   *(With item 4 below now shipped, **both halves of partner-messaging are complete**.)*
   The action shipped 2026-07-26 in `3185cd8` and was recorded here as "already done". It was
   not: **every approved send was refused as a duplicate of its own confirmation prompt**, on
   100% of attempts. Building the CONFIRM read-back calls `guard.note_staged(slot, body)` so one
   LLM reply cannot raise two prompts; the approval then re-enters the engine with the same
   `(slot, body)` and the duplicate arm refused it — `already_awaiting_approval`, nothing
   delivered. `STAGE_TTL_S` and governance's `_CONFIRM_TTL_SECS` are **both 90 s**, so there was
   never a window in which an approval was still valid and the mark had expired.

   Fixed by threading `governance_bypass` into the guard as `approved=`: the duplicate arm is
   skipped for the post-approval invocation and the mark retired, while **the denial arm runs in
   both modes and is checked first** — an approval sentinel can never overturn a refusal.

   **Why 34 passing tests missed it, and the lesson that generalises:** `SendGuard` is correct in
   isolation, and every wiring test matched *source text* (`assert "guard.refusal(" in body`)
   rather than running the sequence. A grep-level test cannot tell "refused" from "nothing was
   sent". `test_partner_send_gate.py` (24 checks) now drives the real governance manager, real
   registry, real engine and main.py's real read-back — compiled out of main.py's source with
   `ast` so a drift in main's body fails the harness instead of passing a substring check — and
   asserts on **transport call count**. Recipient allowlist re-proven by execution at the same
   time: 16 hostile shapes (raw ids as str/int/negative/unicode-digit/nested, unknown names,
   vague words) all reach the transport zero times.

   Still owed: the **§24 live gate**, which would have caught this on the first real send.

4. **Partner-inbound, the "did she talk to you" feature — ✅ DONE 2026-08-02 (`ba12cc1`).**
   **Both halves of partner-messaging are now complete: outbound `message_partner` (item 3)
   and inbound `partner_contact_status`.** Spec was roadmap §6.7; it is built to it.

   `partner_contact_status` (ADMIN-ONLY, AUTO in governance) answers *"Yes, Sir — Mousumi
   messaged around 3pm. Nothing urgent."* / *"…and twice more since; she flagged it as
   important. You may want to call her."* / *"No, Sir — nothing from Mousumi today. Last I
   heard from her was yesterday, around 12:30pm."* Times are deliberately coarse — "at
   15:12:44" is surveillance phrasing.

   **The design evolved during the build, and the change is the interesting part.** The first
   version scanned her logged messages and withheld the content when answering. It worked and
   was green, but discretion was a property of the *formatting code* — one careless refactor
   from leaking, and it coupled the gentle capability to the invasive one, since it needed
   verbatim transcript logging switched on to work at all. The shipped version instead reads
   `modules/contact_events.py`, a store whose schema is `(id, partner_key, partner_slot,
   timestamp, urgency)` — **no content column, and `record()` has no parameter through which
   content could arrive.** The urgency scan runs upstream in memory and only its boolean
   crosses the boundary. Content-free by construction beat scan-then-withhold.

   - **Encrypted at rest**, C#11a keystore, no new dependency: `partner_slot`, `timestamp` AND
     `urgency` are all sealed. More than `partner_log` seals, deliberately — there the secret
     is the message body and the timestamp is incidental; here the timestamp *is* the payload,
     since the table's whole content is a contact pattern. Ordering comes from the
     autoincrement `id` (insertion order is already chronological), which is what makes
     encrypting the timestamp affordable. `partner_key` is a keyed blind index, because
     randomised ciphertext can never satisfy `WHERE partner_slot = ?`.
   - **`JARVIS_LOG_CONTACT_EVENTS` (default ON)** is independent of `JARVIS_LOG_PARTNER_CHATS`
     on purpose — that is the entire reason for the separate store, so the discreet answer
     works on a machine where keeping her words is off. A harness pins that the write did not
     drift behind the transcript flag. ⚠️ Note the default differs from `JARVIS_LOG_PARTNER_CHATS`:
     that flag guards a third party's *words* and is default-OFF; this one records only that a
     message arrived. **If Kaustav wants it default-OFF instead, it is a one-line change.**
   - **Fails honestly.** Recording off, or a keystore that will not open, says so — never "no,
     she didn't message", which would be a confident answer manufactured by a failure.
   - **No migration** — new table, created on first write.
   - Harness `test_partner_contact.py` (25 checks). The leak checks push a rare marker word
     through the real write path and scan the raw db file for it, rather than asserting the
     code looks careful.

   **`summarize_partner_chat` survives as the deliberate explicit override** — "what did she
   say" is a different, more explicit request than "did she call". That settles the §6.6 open
   decision; the routing prompt in `brain.py` now states the two are not interchangeable.

   **Still his call, not technical:** whether Mousumi knows JARVIS exists and that Kaustav can
   ask whether she made contact. The butler model very likely clears the bar transcript-logging
   did not — fact-of-contact is roughly what a housemate would observe — but no document
   settles it for him.

   ⚠️ **The Benglish urgency terms are a guess** (`joruri`, `taratari`, `bipod`, `dorkar`,
   `ekhuni`, `phone koro`, `bari esho`, in `partner_contact.URGENT_TERMS`). Kaustav knows how
   she actually writes; that list wants his correction, not his tolerance.

   Unchanged and pinned against regression: `extract_and_store_memory` still runs for every
   recognised caller ahead of the partner gate, and `partner_log` still honours its own opt-in
   flag. "Off" still means *no transcript*, **not** *nothing retained*.

5. **One open call, his, not blocking:** the cloud cannot seal before it has the public half,
   so after a Render restart with the PC off, facts are **not queued** — counted and logged
   loudly every time (`dropped_no_key`, surfaced in `/health`), never stored in plaintext.
   Closing it means putting the desk **public** key in Render's env, which crosses his
   "no new config on Render" line. Left open deliberately.

⚠️ **The merge is still not a fast-forward** — see the `8d0ea4f` note above. Order is: §7 live
gate → Electron launch scripts → merge to `main` → Step 3.

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
- **Two sealed-queue invariants that look like bugs if you forget them.** (1) A record leaves
  the cloud outbox **on the ack, never on the send** — that is what makes a socket dying
  mid-batch cost a redelivery instead of a fact, and redelivery is normal, not exceptional.
  (2) An **empty sink means HELD**, not dropped: nothing acked, nothing ledgered. If facts seem
  to vanish, check whether a sink is installed before suspecting the transport.
- **A CLI that prompts cannot be answered from the PowerShell tool** — its stdin is the null
  device, so a piped `y` never arrives and the prompt declines. `manage_keys.py export-key`
  failed safe that way ("Cancelled. Nothing was changed.") before succeeding under a shell
  that can deliver stdin. Worth knowing before assuming a key command is broken.
