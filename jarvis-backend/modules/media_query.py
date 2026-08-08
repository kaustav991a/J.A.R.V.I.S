"""media_query.py — turn a spoken music request into (service, search query).

Extracted from `action_engine._play_music` for §6.8.2 wave 2, for two reasons.

**It was wrong.** The handler stripped the service words with plain substring
replacement:

    target_lower.replace("youtube", "").replace("on", "")

`"on"` is two letters that occur inside ordinary song titles, so *"play
Moonlight"* searched for `"Molight"`, *"play Only Girl"* searched for
`"ly Girl"`, and *"Con te partirò"* lost its first word. The failure is quiet —
YouTube returns *something* for the mangled string, so it looks like the wrong
song was requested rather than like a bug. Stripping now happens on WORD
boundaries, which is what the original intent was.

**It was untestable.** Importing `action_engine` pulls in ADB, zeroconf, Chroma
and the rest of the stack, so nothing about this logic could be pinned by a
harness. This module imports nothing.

The parse is deliberately unchanged in every other respect: mentioning Spotify
anywhere selects Spotify, an empty query means "just open the service", and the
query is lower-cased because it is only ever used as a search string.
"""

from __future__ import annotations

import re

YOUTUBE, SPOTIFY = "youtube", "spotify"

#: The words that name a service rather than a song. Whole words only.
_SERVICE_WORDS = re.compile(r"\b(?:youtube|spotify)\b", re.IGNORECASE)
_SPOTIFY = re.compile(r"\bspotify\b", re.IGNORECASE)
#: The carrier word in "play X ON youtube". Whole word only — this is the one
#: that used to eat two letters out of the middle of titles.
_CARRIER = re.compile(r"\bon\b", re.IGNORECASE)


def clean_music_query(target: str) -> tuple[str, str]:
    """Split a request like "moonlight on spotify" into ("spotify", "moonlight").

    Returns `(service, query)`. `service` is `"spotify"` when Spotify is named
    anywhere in the request, otherwise `"youtube"` — the same precedence the
    handler has always had. `query` may be empty, which means the caller should
    open the service's home page rather than search for nothing.
    """
    text = str(target or "").lower()
    service = SPOTIFY if _SPOTIFY.search(text) else YOUTUBE
    query = _CARRIER.sub(" ", _SERVICE_WORDS.sub(" ", text))
    return service, " ".join(query.split())
