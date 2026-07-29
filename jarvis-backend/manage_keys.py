"""The key ceremony for C#11a — generate, check, and recover the memory key.

    venv\\Scripts\\python.exe manage_keys.py init          # once, before any migration
    venv\\Scripts\\python.exe manage_keys.py status        # what exists, no secrets shown
    venv\\Scripts\\python.exe manage_keys.py verify        # decrypt the canary, prove it works
    venv\\Scripts\\python.exe manage_keys.py export-key    # issue a FRESH recovery code
    venv\\Scripts\\python.exe manage_keys.py restore-key   # rebuild the boot wrap from a code
    venv\\Scripts\\python.exe manage_keys.py show-public   # X25519 public half, for Render

No command here opens a database. `init` refuses to run twice, because a second
DEK would orphan every row written under the first.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from modules import memory_crypto as mc

RULE = "=" * 72


def cmd_init(args) -> int:
    if mc.key_files_exist():
        print("Keys already exist. Nothing was changed.\n")
        print("  status      what is on disk")
        print("  verify      prove the key still works")
        print("  export-key  issue a fresh recovery code")
        return 1

    print("Generating a new memory key. No database is opened by this command.\n")
    result = mc.initialise_keys()

    print(RULE)
    print("  YOUR RECOVERY CODE — SHOWN ONCE, NEVER STORED, NEVER RECOVERABLE")
    print(RULE)
    print()
    print("    " + result.recovery_code)
    print()
    print("  Write it down NOW. Password manager, or paper in a drawer.")
    print()
    print("  What it is for: if Windows is reinstalled, your profile is rebuilt,")
    print("  or you move the databases to another machine, this code is the only")
    print("  way to read your memory again. Normal boot never asks for it.")
    print()
    print("  If you lose BOTH this code and this Windows profile, the encrypted")
    print("  memory is gone permanently. That is why you are seeing this now,")
    print("  before anything has been encrypted.")
    print(RULE)
    print()
    print("Files written (all git-ignored):")
    for name in result.files:
        print(f"  {name}")
    print()
    print("X25519 public key (safe to paste into Render later):")
    print(f"  {result.x25519_public_b64}")
    print()

    if not args.no_confirm:
        typed = input("Type SAVED once the code is written down: ").strip()
        if typed.upper() != "SAVED":
            print("\nNot confirmed. The keys still exist and are valid — but do NOT")
            print("run the migration until that code is written down.")
            return 2

    ok = mc.verify_keys()
    print(f"\ncanary decrypts: {'OK' if ok else 'FAILED'}")
    print("Next: the migration will re-run backup_memory.py, then convert rows.")
    return 0 if ok else 3


def cmd_status(args) -> int:
    print("key files:")
    for path in (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE):
        mark = "present" if path.exists() else "MISSING"
        print(f"  {path.name:24s} {mark}")
    print(f"\nDPAPI available: {mc.dpapi_available()}")
    if not mc.key_files_exist():
        print("\nNo keys yet. Run: manage_keys.py init")
        return 1
    try:
        mc.load_dek(use_cache=False)
        print("boot wrap:       unwraps OK (no prompt needed)")
    except mc.MemoryLockedError as exc:
        print(f"boot wrap:       LOCKED — {exc}")
        return 1
    return 0


def cmd_verify(args) -> int:
    try:
        ok = mc.verify_keys()
    except mc.MemoryLockedError as exc:
        print(f"LOCKED — {exc}")
        print(f"\n{mc.MemoryLockedError.SPOKEN}")
        return 1
    print("canary decrypts OK — the key works, and your memory is readable.")
    return 0 if ok else 1


def cmd_export_key(args) -> int:
    """Issue a fresh code for the same DEK. The old one stops working."""
    print("This issues a NEW recovery code and voids any previous one.")
    print("Your data and your boot wrap are NOT changed.\n")
    if not args.no_confirm:
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled. Nothing was changed.")
            return 1
    try:
        code = mc.rotate_recovery_code()
    except mc.MemoryLockedError as exc:
        print(f"LOCKED — {exc}")
        print("A new code can only be issued while the key is healthy.")
        return 1
    print()
    print(RULE)
    print("  NEW RECOVERY CODE — the previous one is now void")
    print(RULE)
    print(f"\n    {code}\n")
    print(RULE)
    return 0


def cmd_restore_key(args) -> int:
    print("Rebuilding the unattended boot wrap from your recovery code.")
    print("Use this after a Windows reinstall, a rebuilt profile, or on a new machine.\n")
    code = args.code or getpass.getpass("Recovery code (hidden): ")
    try:
        mc.restore_dpapi_wrap(code)
    except mc.MemoryLockedError as exc:
        print(f"FAILED — {exc}")
        return 1
    ok = mc.verify_keys()
    print(f"\nboot wrap rebuilt. canary decrypts: {'OK' if ok else 'FAILED'}")
    if ok:
        print("Your memory is readable again on this machine.")
    return 0 if ok else 1


def cmd_show_public(args) -> int:
    try:
        print(mc.x25519_public_b64())
    except mc.MemoryLockedError as exc:
        print(f"LOCKED — {exc}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="generate the key set (once)")
    p.add_argument("--no-confirm", action="store_true", help="skip the SAVED prompt")
    p.set_defaults(func=cmd_init)

    sub.add_parser("status", help="what exists on disk").set_defaults(func=cmd_status)
    sub.add_parser("verify", help="decrypt the canary").set_defaults(func=cmd_verify)

    p = sub.add_parser("export-key", help="issue a fresh recovery code")
    p.add_argument("--no-confirm", action="store_true")
    p.set_defaults(func=cmd_export_key)

    p = sub.add_parser("restore-key", help="rebuild the boot wrap from a recovery code")
    p.add_argument("--code", help="the code (omit to be prompted without echo)")
    p.set_defaults(func=cmd_restore_key)

    sub.add_parser("show-public", help="X25519 public key").set_defaults(func=cmd_show_public)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
