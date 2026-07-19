r"""
test_working_memory_lock.py — G5.7 working_memory thread-safety (no network)

Run: venv\Scripts\python.exe test_working_memory_lock.py

working_memory is mutated from several threads. A bare list whose head is
slice-assigned by the compressor while another thread appends or iterates can
raise ("list changed size during iteration") or corrupt. This hammers
add/get/clear concurrently and asserts no thread raised and the buffer stays
well-formed, plus that getters hand back COPIES (a caller mutating the result
can't corrupt the shared buffer). The LLM compressor is stubbed with a
lock-using trim so the race is exercised without any network call.
"""

import threading

import memory

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _stub_compress():
    # mirrors the real compressor's slice-assign, under the real lock, no network
    with memory._wm_lock:
        memory.working_memory[:15] = [{"role": "system", "content": "[CONTEXT SUMMARY] x"}]


def test_concurrent_access_is_race_free():
    saved = memory._compress_oldest_memories
    memory._compress_oldest_memories = _stub_compress
    memory.clear_working_memory()
    errors: list[Exception] = []

    def add_worker():
        try:
            for i in range(1500):
                memory.add_to_working_memory("user", f"m{i}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def read_worker():
        try:
            for _ in range(1500):
                for m in memory.get_context_window():
                    _ = m.get("role")
                for m in memory.get_working_memory():
                    _ = m.get("content")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def clear_worker():
        try:
            for i in range(300):
                if i % 50 == 0:
                    memory.clear_working_memory()
                memory.get_working_memory()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = ([threading.Thread(target=add_worker) for _ in range(3)]
               + [threading.Thread(target=read_worker) for _ in range(3)]
               + [threading.Thread(target=clear_worker)])
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        check(not errors, f"no thread raised under concurrent access (got {errors[:2]})")
        with memory._wm_lock:
            snap = list(memory.working_memory)
        check(all(isinstance(m, dict) and "role" in m and "content" in m for m in snap),
              "buffer stays a list of well-formed {role, content} dicts")
    finally:
        memory._compress_oldest_memories = saved
        memory.clear_working_memory()


def test_getters_return_copies():
    memory.clear_working_memory()
    memory.add_to_working_memory("user", "a")
    memory.add_to_working_memory("assistant", "b")
    n = len(memory.get_context_window())

    w = memory.get_context_window()
    w.append({"role": "x", "content": "y"})
    check(len(memory.get_context_window()) == n, "mutating get_context_window() result is isolated")

    g = memory.get_working_memory()
    g.clear()
    check(len(memory.get_working_memory()) == n, "mutating get_working_memory() result is isolated")
    memory.clear_working_memory()


TESTS = [test_concurrent_access_is_race_free, test_getters_return_copies]


def main():
    print("=" * 60)
    print("working_memory lock harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
