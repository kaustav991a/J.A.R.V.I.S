---
name: find-a-file
description: Locating a file on this machine, and the two sandboxes that are not the same one.
---

# Finding a file

The single most common failure here is naming a file you cannot then open. It
happens because **the tools that LIST and the tools that READ do not share a
sandbox**, and nothing in a directory listing tells you that.

## The order that works

1. **`find_file`** if you know part of the name and not the folder.
2. **`list_directory`** if you know the folder. It returns entries **newest
   first**, with a modified time, a type and a **full path** on every row.
3. **`workspace_read`** on the FULL PATH from that row — never on the bare name.

## The three rules

- **Copy the path verbatim from the listing.** A bare filename resolves against
  a different root in the reading tool than it did in the listing tool. This is
  not hypothetical: `\.claude.json` from a listing became
  `File not found: F:\work\.claude.json` when read.
- **`list_directory` only works inside the home directory. `workspace_read`
  works on the workspace roots.** Those two sets overlap but are not equal, so a
  folder you can read may be one you cannot list, and the reverse. Your
  instructions name both.
- **"Access denied" is a refusal, not an empty folder.** Do not try a
  neighbouring root and then another. If a place is refused, it stays refused —
  say which folder you could not reach and ask, or work with what you can list.

## Reading a long file

`workspace_read` returns **numbered lines** and one window at a time. The footer
tells you the offset to pass to continue. Use those line numbers when you refer
to anything — `config.py:120` is checkable and "near the top" is not.

If you need one thing out of a large file, read the window you expect it in
rather than paging through the whole file: every window you read stays in the
transcript for the rest of the task.

## What NOT to do

- Do not answer from a filename. A file called `budget-final.xlsx` tells you
  nothing about what is in it.
- Do not invent a path because a listing was refused.
- Do not read a directory. Read a file; list a folder.
