r"""test_chroma_crypto.py — the RAG store is sealed, and search still works.

Every check here runs against a REAL Chroma collection in a temp directory and
the REAL `memory_crypto` keystore. The assertions that matter are made against
the bytes on disk, not against the API: "the caller got plaintext back" is not
the same claim as "the file holds ciphertext", and only the second one is
security. So the store's own sqlite file is opened and scanned for the words
that must not be in it.

Not covered on purpose: the vectors. They are plaintext by design (that is what
makes retrieval work) and `test_the_vectors_are_deliberately_not_encrypted`
pins that as an accepted, documented tradeoff rather than leaving it to be
rediscovered as a surprise.

Runs with no network. If the machine has no key set the encryption-path tests
skip themselves rather than failing — the plaintext-degradation path is still
asserted, matching how the SQLite store behaves before the ceremony.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from modules import chroma_crypto as cc          # noqa: E402
from modules import memory_crypto as mc          # noqa: E402
from modules import personal_rag                 # noqa: E402

COLL = "personal_documents"
KEYS = cc.encryption_on()

DOCS = {
    "phoenix.md": "# Project Phoenix\n"
                  "## Database choice\n"
                  "On 2026-03-10 I decided to use PostgreSQL for Project Phoenix "
                  "because the relational model fits the billing data.\n",
    "health.md":  "# Health\n"
                  "I smoked seven cigarettes today and slept badly.\n",
    "hosting.md": "# Hosting\n"
                  "We will host the gateway on Render's free tier.\n",
}

SECRETS = ("PostgreSQL", "cigarettes", "Render", "Phoenix", "phoenix.md", "health.md")


# ── scaffolding ──────────────────────────────────────────────────────────────

class Store:
    """A throwaway personal_rag pointed at temp dirs. Restores globals on exit."""

    def __init__(self, docs=None):
        self.docs = DOCS if docs is None else docs
        self.tmp = tempfile.mkdtemp(prefix="chroma_crypto_test_")

    def __enter__(self):
        self.roots = os.path.join(self.tmp, "docs")
        os.makedirs(self.roots)
        for name, body in self.docs.items():
            pathlib.Path(self.roots, name).write_text(body, encoding="utf-8")
        self._saved = (personal_rag._PERSIST_DIR, personal_rag._collection,
                       personal_rag._embed_fn)
        personal_rag._PERSIST_DIR = os.path.join(self.tmp, "chroma")
        personal_rag._collection = None
        personal_rag._embed_fn = None
        return self

    def ingest(self):
        return personal_rag.ingest_documents([self.roots])

    def query(self, q, n=3):
        return personal_rag.query_documents(q, n_results=n)

    def raw_sqlite_bytes(self) -> bytes:
        personal_rag._collection = None          # drop the handle so the file settles
        p = os.path.join(personal_rag._PERSIST_DIR, "chroma.sqlite3")
        return pathlib.Path(p).read_bytes()

    def stored_documents(self) -> list[str]:
        """Read the document strings straight out of Chroma's sqlite."""
        personal_rag._collection = None
        p = os.path.join(personal_rag._PERSIST_DIR, "chroma.sqlite3")
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return [r[0] for r in con.execute(
                "SELECT string_value FROM embedding_metadata "
                "WHERE key='chroma:document' AND string_value IS NOT NULL").fetchall()]
        finally:
            con.close()

    def stored_metadata(self, key: str) -> list[str]:
        personal_rag._collection = None
        p = os.path.join(personal_rag._PERSIST_DIR, "chroma.sqlite3")
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return [r[0] for r in con.execute(
                "SELECT string_value FROM embedding_metadata WHERE key=? "
                "AND string_value IS NOT NULL", (key,)).fetchall()]
        finally:
            con.close()

    def stored_ids(self) -> list[str]:
        personal_rag._collection = None
        p = os.path.join(personal_rag._PERSIST_DIR, "chroma.sqlite3")
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return [r[0] for r in con.execute(
                "SELECT embedding_id FROM embeddings").fetchall()]
        finally:
            con.close()

    def __exit__(self, *exc):
        (personal_rag._PERSIST_DIR, personal_rag._collection,
         personal_rag._embed_fn) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def _need_keys():
    if not KEYS:
        print("      (skipped — no key set on this machine)")
        return False
    return True


# ── the unit layer: AAD, blind index, metadata shaping ───────────────────────

def test_aad_is_namespaced_per_collection():
    """A sealed document must not open as one from another collection, or as a
    memories row — the AAD is what makes a moved blob fail instead of decrypting
    to something plausible."""
    if not _need_keys():
        return
    blob = cc.encrypt_document("secret text", "personal_documents")
    assert cc.decrypt_document(blob, "personal_documents") == "secret text"
    for wrong in ("jarvis_memory", "jarvis_episodes"):
        try:
            cc.decrypt_document(blob, wrong)
            raise AssertionError(f"blob opened under the wrong collection {wrong!r}")
        except mc.MemoryCryptoError:
            pass
    # and not as a memories row either
    try:
        mc.decrypt_field(blob, "memories", "content")
        raise AssertionError("a chroma blob opened as a memories row")
    except mc.MemoryCryptoError:
        pass


def test_ciphertext_is_randomised_but_the_blind_index_is_stable():
    if not _need_keys():
        return
    a = cc.encrypt_document("same words", COLL)
    b = cc.encrypt_document("same words", COLL)
    assert a != b, "randomised encryption must not repeat a ciphertext"
    assert cc.blind("/x/y.md", COLL, "path") == cc.blind("/x/y.md", COLL, "path")
    assert cc.blind("/x/y.md", COLL, "path") != cc.blind("/x/z.md", COLL, "path")
    # the fingerprint reveals nothing about the value
    assert "/x/y.md" not in cc.blind("/x/y.md", COLL, "path")


def test_metadata_seals_only_the_sensitive_keys():
    if not _need_keys():
        return
    meta = {"path": "/home/k/diary.md", "name": "diary.md", "chunk": 3}
    enc = cc.encrypt_metadata(meta, COLL, ("path", "name"))
    assert enc["chunk"] == 3, "an ordinal is not a secret and must stay filterable"
    assert mc.is_encrypted(enc["path"]) and mc.is_encrypted(enc["name"])
    assert "diary" not in enc["path"] and "diary" not in enc["name"]
    assert enc["path" + cc.BLIND_SUFFIX], "a filterable key needs a blind index"
    back = cc.decrypt_metadata(enc, COLL, ("path", "name"))
    assert back == meta, f"round-trip changed the metadata: {back}"
    assert not any(k.endswith(cc.BLIND_SUFFIX) for k in back), \
        "blind-index companions are plumbing and must not reach the caller"


def test_a_sensitive_non_string_is_refused_not_silently_left_plain():
    if not _need_keys():
        return
    try:
        cc.encrypt_metadata({"path": 12345}, COLL, ("path",))
        raise AssertionError("a sensitive int was accepted and left in the clear")
    except TypeError as e:
        assert "sensitive" in str(e)


# ── the store layer: what is actually on disk ────────────────────────────────

def test_ingest_writes_ciphertext_to_disk():
    if not _need_keys():
        return
    with Store() as s:
        n = s.ingest()
        assert n >= 3, f"expected chunks from {len(DOCS)} files, got {n}"

        stored = s.stored_documents()
        assert stored, "nothing was stored"
        for doc in stored:
            assert mc.is_encrypted(doc), f"document stored in plaintext: {doc[:60]!r}"

        blob = s.raw_sqlite_bytes()
        for secret in SECRETS:
            assert secret.encode() not in blob, \
                f"{secret!r} is readable in chroma.sqlite3 — including its FTS index"


def test_sensitive_metadata_and_ids_are_not_readable():
    if not _need_keys():
        return
    with Store() as s:
        s.ingest()
        for key in ("path", "name"):
            for v in s.stored_metadata(key):
                assert mc.is_encrypted(v), f"metadata {key} in plaintext: {v[:50]!r}"
        # the id is Chroma's primary key and is ALWAYS plaintext — it must
        # therefore not be derived from the filename
        for i in s.stored_ids():
            assert ".md" not in i and "phoenix" not in i.lower(), \
                f"the chunk id leaks the filename: {i!r}"


def test_the_vectors_are_deliberately_not_encrypted():
    """Accepted tradeoff, pinned so it is a decision and not a surprise: the
    vectors are plaintext floats and leak approximate content by inversion."""
    if not _need_keys():
        return
    with Store() as s:
        s.ingest()
        personal_rag._collection = None
        p = os.path.join(personal_rag._PERSIST_DIR, "chroma.sqlite3")
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        n, total = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(vector)),0) FROM embeddings_queue"
        ).fetchone()
        con.close()
        assert n >= 3 and total > 0, "vectors must still be present for search to work"


# ── retrieval: the caller sees exactly what it saw before ────────────────────

def test_search_returns_the_right_document_decrypted():
    if not _need_keys():
        return
    with Store() as s:
        s.ingest()
        hits = s.query("which database did I choose?", n=3)
        assert hits, "search returned nothing"
        top = hits[0]
        assert "PostgreSQL" in top, f"wrong or still-encrypted result: {top[:90]!r}"
        assert "[phoenix.md]" in top, f"the source name did not decrypt: {top[:60]!r}"
        assert not top.startswith("[document]"), "metadata failed to decrypt"


def test_ranking_survives_encryption_across_several_documents():
    """Encryption must not disturb which document wins — the vectors are still
    computed from plaintext, so relevance is unchanged."""
    if not _need_keys():
        return
    with Store() as s:
        s.ingest()
        for question, expect in (("which database did I choose?", "PostgreSQL"),
                                 ("how much did I smoke?", "cigarettes"),
                                 ("where are we hosting it?", "Render")):
            hits = s.query(question, n=3)
            assert hits, f"no hits for {question!r}"
            assert expect in hits[0], \
                f"{question!r} ranked wrong: got {hits[0][:80]!r}, wanted {expect}"


def test_reingest_of_a_shrunken_file_leaves_no_stale_chunks():
    """The real job of the where-delete, and the one case that can catch it.

    Re-ingesting IDENTICAL content proves nothing: the ids are the same, so
    upsert overwrites in place and the count holds even if the filter matched
    nothing. Only a file that LOSES a section exposes a broken delete — and a
    randomised ciphertext can never be found by `where={"path": ...}`, which is
    exactly why the blind index exists.
    """
    if not _need_keys():
        return
    big = {"notes.md": "# N\n## A\naaa\n## B\nbbb\n## C\nccc\n"}
    with Store(big) as s:
        n1 = s.ingest()
        assert n1 >= 3, f"the fixture must chunk into several parts, got {n1}"
        assert len(s.stored_documents()) == n1

        pathlib.Path(s.roots, "notes.md").write_text("# N\n## A\naaa\n", encoding="utf-8")
        personal_rag._collection = None
        n2 = s.ingest()
        assert n2 < n1, f"the shrunken file should chunk smaller: {n1} -> {n2}"
        left = len(s.stored_documents())
        assert left == n2, f"{left - n2} stale chunk(s) survived the re-ingest"


# ── failure modes ────────────────────────────────────────────────────────────

def test_a_locked_key_store_raises_instead_of_returning_nothing():
    """The C#11a rule applied to retrieval: [] is indistinguishable from "no
    relevant documents", so a locked store must be loud."""
    if not _need_keys():
        return
    with Store() as s:
        s.ingest()
        s.query("which database did I choose?")           # warm + prove it works

        real_load, real_cache = mc.load_dek, mc._cached_dek
        def locked(*a, **k):
            raise mc.MemoryLockedError("key store unavailable (test)")
        mc.load_dek, mc._cached_dek = locked, None
        try:
            s.query("which database did I choose?")
            raise AssertionError("a locked key store returned results instead of raising")
        except mc.MemoryLockedError:
            pass
        finally:
            mc.load_dek, mc._cached_dek = real_load, real_cache

        # and it recovers once the key is back
        assert "PostgreSQL" in s.query("which database did I choose?")[0]


def test_a_tampered_document_is_refused_not_returned_as_garbage():
    if not _need_keys():
        return
    blob = cc.encrypt_document("the real text", COLL)
    flipped = blob[:-6] + ("A" if blob[-6] != "A" else "B") + blob[-5:]
    try:
        cc.decrypt_document(flipped, COLL)
        raise AssertionError("a tampered ciphertext decrypted")
    except mc.MemoryCryptoError:
        pass


def test_without_keys_it_degrades_to_plaintext_like_the_sqlite_store():
    """No ceremony run ⇒ encryption is simply off, exactly as memory_manager
    behaves. It must not crash, and it must not half-encrypt."""
    real = cc.encryption_on
    cc.encryption_on = lambda: False
    try:
        with Store() as s:
            n = s.ingest()
            assert n >= 3
            for doc in s.stored_documents():
                assert not mc.is_encrypted(doc), "encrypted with the switch off"
            hits = s.query("which database did I choose?")
            assert hits and "PostgreSQL" in hits[0]
    finally:
        cc.encryption_on = real


def test_plaintext_rows_still_read_after_the_switch_flips_on():
    """A store written before the ceremony must keep reading afterwards —
    decrypt_field passes plaintext through, which is what makes the migration
    interruptible."""
    if not _need_keys():
        return
    # One `with`, so a failure inside the ingest still restores the globals and
    # removes the temp store instead of leaking them into the next test.
    with Store() as s:
        real = cc.encryption_on
        cc.encryption_on = lambda: False
        try:
            s.ingest()                                     # written BEFORE the ceremony
        finally:
            cc.encryption_on = real
        personal_rag._collection = None
        hits = s.query("which database did I choose?")     # read WITH encryption on
        assert hits and "PostgreSQL" in hits[0], \
            f"pre-ceremony rows stopped reading: {hits[:1]}"


# ── the constraint that keeps this honest ────────────────────────────────────

def test_no_new_dependency_and_the_pins_are_untouched():
    """Option B was approved on the promise of zero new deps. Assert it."""
    src = pathlib.Path(__file__).resolve().parent.joinpath(
        "modules", "chroma_crypto.py").read_text(encoding="utf-8")
    for banned in ("import sqlcipher", "pysqlite3", "import torch", "tenseal", "seal"):
        assert f"\n{banned}" not in src and f"import {banned}" not in src, \
            f"chroma_crypto pulled in {banned!r}"
    req = pathlib.Path(__file__).resolve().parent.joinpath(
        "requirements.txt").read_text(encoding="utf-8")
    assert "protobuf==6.33.6" in req, "the protobuf pin moved"
    assert "chromadb==1.5.9" in req, "the chromadb pin moved"
    assert "cryptography==" in req, "cryptography must stay pinned"


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    print(f"key set present: {KEYS}\n")
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
