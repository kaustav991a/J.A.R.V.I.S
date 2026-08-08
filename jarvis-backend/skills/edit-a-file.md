---
name: edit-a-file
description: Changing a file without losing the parts you did not re-type, and why an edit gets refused.
---

# Changing a file

Two tools, and picking the wrong one is how work disappears.

- **`edit_file`** replaces one exact string and leaves everything else alone.
  **This is the default.**
- **`workspace_write`** replaces the WHOLE file with what you send. Anything you
  do not re-emit is gone. Use it to create a new file, or when the file is
  genuinely being replaced end to end.

Both require the owner's confirmation, and both need an **absolute path**.

## The procedure

1. **Read the file first.** `workspace_write` refuses to overwrite a file you
   have not read in this run, and `edit_file` cannot match a string you have not
   seen. This is enforced in code, not advice.
2. **Strip the line-number prefix** before you use text from a read as
   `old_string`. The numbers are added for citation; they are not in the file.
3. **Make `old_string` unique.** If it matches more than once, the edit is
   REFUSED and tells you how many places matched. That refusal is the feature —
   see below.
4. **Check the result.** The tool says what it changed.

## When an edit is refused for matching several places

Do not switch to `replace_all` to make the refusal go away. It exists because
*"change `timeout = 30` to 60"* on a file with three of those lines used to
change all three silently.

Two honest ways forward:

- **Extend `old_string` with the surrounding lines** until it identifies exactly
  the one you mean. This is nearly always the right answer.
- **Pass `replace_all: true`** only when you genuinely mean every occurrence —
  a rename, for instance — and say so in your answer.

## Writing a new file

`workspace_write` with the full content. Say the path back in your answer, so
the owner knows where it went. A "note" with a title rather than a path is
`create_note` instead.

## What NOT to do

- Do not rewrite a whole file to change one line. That is how a model drops the
  half of the file it did not think to re-emit.
- Do not guess at indentation. Copy it from what you read.
- Do not report an edit as done before the tool has said it was.
