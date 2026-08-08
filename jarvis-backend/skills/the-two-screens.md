---
name: the-two-screens
description: Television versus desk display — which tool puts what where, and the toggle that can turn the TV off.
---

# The two screens

There are two screens in this house and "put X on" is ambiguous between them.
Getting it wrong is immediately visible to the owner, so decide first.

| He means | Use |
|---|---|
| The **television** across the room | `tv_play_media`, `tv_launch_app`, `tv_volume`, `tv_control`, `tv_power` |
| The **desk display** in front of him | `play_music`, `hud_open_widget`, `render_chart`, `web_search_image` |

If the request does not say and the answer matters — a film almost certainly
means the television, a song at the desk almost certainly does not — ask, or say
which one you chose.

## Putting something on the television

`tv_play_media` with **both** the title and the app: `netflix`, `youtube`,
`prime video`, `hotstar` (this is where Disney+ content lives) or `spotify`.
Without an app it comes back asking which one, and that costs a step.

If the title itself contains a colon, you MUST pass the app — the colon is how
the target is split, so *"Mission: Impossible"* without an app is read as an app
called "mission".

`tv_launch_app` only opens an app. `tv_play_media` opens it and searches inside
it, which is usually what was wanted.

## The remote

`tv_control` is arrow keys, select, back, home and play/pause — moving around a
screen that is already open. Power is `tv_power`, volume and mute are
`tv_volume`. Do not reach for `tv_control` for those.

## The one that bites

**`tv_power` is a TOGGLE and there is no way to read the current state.** It
wakes a sleeping TV and puts an awake one to sleep. So:

- "Turn the TV on" → call it.
- "Make sure the TV is on" → **do not call it.** If it is already on you have
  just turned it off in front of him. Say you cannot tell whether it is on.

## Music and pictures at the desk

`play_music` opens the track in the HUD's player on the desktop. You will not
hear it and cannot confirm it is audible — say it is playing on the desk
display, not that it is playing.

Same for `web_search_image` and `render_chart`: he sees them, you do not.
Describe what the data says, never what the picture looks like.
