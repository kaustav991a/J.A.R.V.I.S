---
name: handle-email
description: Reading, searching and answering mail — which of the four reading tools, and the rules for sending.
---

# Mail

## Reading

- **"Anything new?" / "check my inbox"** → `gmail_read_unread`. Unread only.
- **From a person, about a subject, older than today** → `gmail_read` with Gmail
  search syntax: `from:name@x.com`, `subject:invoice`, `is:starred`,
  `newer_than:2d`. It also returns the **thread id**, which a reply needs.
- **A phrase you are looking for, no syntax** → `search_email`.
- **The body of one message** → `read_email` by its position in the last
  listing, or `latest`.

Read the summary before opening a body. Most questions are answered by who sent
it and what the subject is, and a body is a lot of transcript.

## Answering "did anything important come in?"

Do not read every message. List the unread ones, and judge from senders and
subjects. Open a body only when the answer depends on what is actually in it.
Say how many there were, then what matters — not a list of all of them.

## Sending

`gmail_send` for a new message, `gmail_reply` to answer a thread. Both need the
owner's confirmation, and he sees the text before it goes.

The rules that are not negotiable:

- **Never invent a recipient.** Only an address he gave you, or one that came
  out of a mail you actually read. An address you inferred from a name is a
  guess with someone else's inbox on the other end.
- **Read the thread before replying to it.** A reply written without seeing what
  it answers is a guess, and `gmail_reply` needs a thread id from `gmail_read`
  anyway — one you did not get from a tool is invented.
- **Do not soften or improve what he asked you to say.** Send his message.
- If you are missing the recipient, the subject or the body, ask for the missing
  one rather than filling it in.

## When nobody is there to approve

If there is nobody at the desk, the sending tools are not merely refused —
you will not find them at all. That is correct. Say the mail needs him, and what
it would have said.

## The calendar next door

`check_calendar` reads; `create_event` adds and needs confirmation; describe an
event in one natural phrase with its time ("dentist Thursday 4pm"). Check the
calendar before adding something to a slot that might already be taken.

`clear_schedule` removes **every event today** at once. It is for an explicit
"clear my day" and nothing else.
