"""
protected_paths.py — the files that must outlive a mistake
==========================================================

Pre-Electron review, 2026-08-15. `ActionEngine.restricted_folders` guarded
`C:/Windows` and the two Program Files directories — the operating system,
which can be reinstalled — and nothing else. The files it did NOT guard are the
only ones on the machine that cannot be:

    jarvis_key.dpapi        DPAPI wrap of the data-encryption key
    jarvis_key.recovery     the scrypt wrap; the last resort
    jarvis_x25519.enc       the cloud->desk unseal private key
    jarvis_key.canary
    jarvis_longterm.db      the encrypted memories themselves
    jarvis_fact_ledger.db
    .env                    every API key and token

Delete the two wraps and every row of `jarvis_longterm.db` is unreadable
forever — including with the recovery CODE, because the recovery WRAP is one of
the files that just went.

WHY THIS IS ITS OWN MODULE, AND NOT A METHOD ON ActionEngine
------------------------------------------------------------
It started as one. Guarding `_delete_file` and `_workspace_write` there covered
two call sites and looked complete — and it was not. `_workspace_patch` reaches
the same files by a different road (`WorkspaceAgent.patch_file`), and
`jarvis_key.dpapi` is JSON, so a find-and-replace corrupts it just as
thoroughly as an overwrite. `workspace_agent`'s own sandbox does not help: its
default roots are the repo and `F:\work`, so the key files are INSIDE it, and
its `_BLOCKED_EXTENSIONS` list covers `.exe` and `.zip` but not `.dpapi`,
`.recovery`, `.enc`, `.db` or `.env`.

That is the same lesson this review learned twice already, in a third costume:
**an injection or destruction class fixed one call site at a time stays open.**
So the rule lives in one place and is applied at each subsystem's own choke
point — `_resolve_safe` for the workspace agent, which covers read, write and
patch together.

READING is refused too, not only writing. `jarvis_key.dpapi` is the wrapped key
and `.env` is every credential; handing either back as file content is the same
disclosure as copying it out.
"""

from pathlib import Path

#: `modules/protected_paths.py` → parents[1] is `jarvis-backend/`.
BACKEND_DIR = Path(__file__).resolve().parent.parent

#: Exact files. Deliberately NOT the whole backend directory: JARVIS writes
#: code and notes beside these all day, and a blanket sandbox would cost that
#: for no extra safety. This is a short list of things that must survive a
#: mistake, not a jail.
PROTECTED_FILES = frozenset(
    p.resolve() if p.exists() else p
    for p in (
        BACKEND_DIR / "jarvis_key.dpapi",       # DPAPI wrap of the DEK
        BACKEND_DIR / "jarvis_key.recovery",    # scrypt wrap — the last resort
        BACKEND_DIR / "jarvis_x25519.enc",      # cloud->desk unseal private key
        BACKEND_DIR / "jarvis_key.canary",      # proves a key opens the store
        BACKEND_DIR / "jarvis_longterm.db",     # the encrypted memories
        BACKEND_DIR / "jarvis_fact_ledger.db",  # replay ledger for sealed facts
        BACKEND_DIR / ".env",                   # every API key and token
    )
)

#: The off-machine copies, which exist precisely to survive what happens in here.
PROTECTED_FOLDERS = (BACKEND_DIR.parent.parent / "JARVIS-BACKUPS",)


def protected_path_problem(target_path,
                           files=PROTECTED_FILES,
                           folders=PROTECTED_FOLDERS):
    """Refuse anything that would destroy or disclose state that cannot be rebuilt.

    Resolved FIRST, then compared, so `..`, a symlink, or a Windows short name
    (`PROGRA~1`) cannot walk around the check. `PurePath` comparison on Windows
    is case-insensitive, so `JARVIS_KEY.DPAPI` is caught too.

    Returns a refusal string, or None when the path is fine.
    """
    if not isinstance(target_path, str) or not target_path.strip():
        return "I need a path, Sir."
    # A NUL byte is never part of a real filename. `Path.resolve()` accepts one
    # without complaint and the failure surfaces much later, at the syscall —
    # which means the guard would have already said yes.
    if "\x00" in target_path:
        return "That path is malformed, Sir — I won't act on it."
    try:
        path = Path(target_path).resolve()
    except (OSError, ValueError):
        return "That path could not be resolved, Sir."
    if path in files:
        return (f"I won't touch {path.name}, Sir — it is part of the key store or "
                "the encrypted memory, and losing it cannot be undone.")
    for folder in folders:
        if folder == path or folder in path.parents:
            return ("I won't touch the backups, Sir — they exist to survive "
                    "exactly this kind of mistake.")
    return None
