---
name: work-with-git
description: Looking at a repository, and the two git actions that need a human and cannot be taken back.
---

# Git

## Looking

1. **`github_status`** — what has changed, and on which branch. Start here.
2. **`github_diff`** — a `--stat` summary: which files moved and by how much.
   Use it when the names alone do not answer the question. It is not the full
   patch; to read the actual change, read the file.
3. **`github_log`** — recent commits, newest first. This is how you answer
   "what did I do last" or find where something landed.

All three default to the **active workspace repo**. Pass `repo_path` only when
he names a different one — an absolute path, never a guess.

Do not confuse `github_status` (a repository) with `system_status` (the
machine's CPU and memory). They share a word and nothing else.

## Committing

`github_commit` needs the owner's confirmation and **stages EVERY change in the
repository**. It cannot commit a subset.

So the procedure is:

1. **`github_status` first, always.** Committing without reading what is
   uncommitted is how unrelated work ends up in one commit under a message that
   describes half of it.
2. If the working tree holds changes that do not belong together, **say so** and
   let him decide. Do not commit anyway.
3. Write a message that says what changed and why. If he gave you the message,
   use his words.
4. **A pipe character in a commit message is refused.** The message would be
   split at it and read as a repository path. Rewrite without the pipe.

## Pushing

`github_push` publishes the current branch. It needs confirmation, and it is the
step that is hardest to take back.

- **Never push work you were not asked to push.** "Commit this" is not "push
  this".
- Committing and pushing are two decisions. Do not chain them because they often
  go together.

## When nobody is at the desk

The two writing tools are not findable in an unattended run. Report what you
found with the reading tools and say the commit needs him.
