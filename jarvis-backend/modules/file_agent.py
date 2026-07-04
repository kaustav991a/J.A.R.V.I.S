"""
Phase 6: File System Agent
Provides file search, note creation, and download organization.
No external dependencies — pure pathlib + os.
"""
import os
import shutil
import datetime
from pathlib import Path


class FileAgent:
    def __init__(self):
        # Common directories to search
        home = Path.home()
        self.search_dirs = [
            home / "Desktop",
            home / "Documents",
            home / "Downloads",
            home / "Pictures",
            Path("G:/work"),  # Projects directory
        ]
        
        # Notes directory
        self.notes_dir = home / "Documents" / "JarvisNotes"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        
        # Download organization categories
        self.file_categories = {
            "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico"},
            "Documents": {".pdf", ".doc", ".docx", ".txt", ".xlsx", ".xls", ".pptx", ".csv", ".odt"},
            "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"},
            "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
            "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"},
            "Installers": {".exe", ".msi", ".dmg", ".deb"},
            "Code": {".py", ".js", ".jsx", ".ts", ".html", ".css", ".scss", ".json", ".java", ".cpp", ".c"},
        }
    
    def find_file(self, query: str) -> str:
        """
        Phase 8.8: Fuzzy searches for files across common directories.

        Safety limits:
          - MAX_DEPTH = 4 (was 3 off-by-one; now explicit constant)
          - TIMEOUT   = 10 seconds (wall-clock, enforced via threading.Event)

        The os.walk loop checks timeout_event.is_set() at every directory
        yield so it exits promptly when the timer fires. If nothing is found
        within the time limit a graceful failure is returned to the LLM.
        Returns top 5 matches sorted by most-recently-modified.
        """
        import threading

        MAX_DEPTH = 4
        TIMEOUT   = 10  # seconds

        print(f"[FILE AGENT] Searching for: {query!r}  (max_depth={MAX_DEPTH}, timeout={TIMEOUT}s)")
        query_lower = query.lower().strip()
        matches = []

        # Threading.Event: the Timer thread sets this flag; the walk loop checks it.
        timeout_event = threading.Event()
        timer = threading.Timer(TIMEOUT, timeout_event.set)
        timer.daemon = True
        timer.start()

        try:
            for search_dir in self.search_dirs:
                if timeout_event.is_set():
                    break
                if not search_dir.exists():
                    continue

                try:
                    for root, dirs, files in os.walk(search_dir):
                        # ── Timeout check — exit the walk immediately when timer fires ──
                        if timeout_event.is_set():
                            dirs.clear()   # prevent os.walk from descending further
                            break

                        # Depth limit
                        depth = (
                            str(root).count(os.sep)
                            - str(search_dir).count(os.sep)
                        )
                        if depth >= MAX_DEPTH:
                            dirs.clear()
                            continue

                        # Skip hidden/system directories
                        dirs[:] = [
                            d for d in dirs
                            if not d.startswith(".")
                            and d not in ("node_modules", "venv", "__pycache__", ".git")
                        ]

                        for filename in files:
                            # ── Inner timeout check ───────────────────────────────
                            if timeout_event.is_set():
                                break

                            if query_lower in filename.lower():
                                full_path = os.path.join(root, filename)
                                try:
                                    size = os.path.getsize(full_path)
                                    modified = datetime.datetime.fromtimestamp(
                                        os.path.getmtime(full_path)
                                    )
                                    matches.append({
                                        "name":     filename,
                                        "path":     full_path,
                                        "size":     self._format_size(size),
                                        "modified": modified.strftime("%Y-%m-%d %H:%M"),
                                    })
                                except Exception:
                                    matches.append({
                                        "name": filename, "path": full_path,
                                        "size": "?", "modified": "?",
                                    })

                                if len(matches) >= 10:
                                    break

                        if len(matches) >= 10:
                            dirs.clear()
                            break

                except PermissionError:
                    continue

        finally:
            timer.cancel()  # cancel the timer if the walk finished early

        if timeout_event.is_set():
            print(f"[FILE AGENT] find_file timed out after {TIMEOUT}s; {len(matches)} partial matches found.")

        if not matches:
            if timeout_event.is_set():
                return (
                    f"I couldn't locate '{query}' within the time limit, Sir. "
                    f"The search exceeded {TIMEOUT} seconds across your common directories."
                )
            return f"No files matching '{query}' were located in your common directories, sir."

        # Sort by most recently modified
        matches.sort(key=lambda x: x.get("modified", ""), reverse=True)
        top = matches[:5]

        timed_out_note = " (search timed out — results may be partial)" if timeout_event.is_set() else ""
        result_lines = [
            f"I found {len(matches)} file{'s' if len(matches) != 1 else ''} "
            f"matching '{query}'{timed_out_note}:"
        ]
        for i, m in enumerate(top, 1):
            result_lines.append(f"  {i}. {m['name']} ({m['size']}) — Modified: {m['modified']}")
            result_lines.append(f"     Path: {m['path']}")

        if len(matches) > 5:
            result_lines.append(f"  ... and {len(matches) - 5} more.")

        return "\n".join(result_lines)
    
    def create_note(self, target: str) -> str:
        """
        Creates a text note in Documents/JarvisNotes/.
        Target format: "title: content" or just "title" (empty note)
        """
        if ":" in target:
            title, content = target.split(":", 1)
            title = title.strip()
            content = content.strip()
        else:
            title = target.strip()
            content = ""
        
        # Sanitize filename — collapse spaces so "Sprint Plan" and "SprintPlan"
        # both produce the same file on disk.
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        safe_title = safe_title.replace(" ", "")  # remove spaces: "Sprint Plan" → "SprintPlan"
        if not safe_title:
            safe_title = "untitled_note"
        
        filename = f"{safe_title}.txt"
        filepath = self.notes_dir / filename
        
        # Add timestamp header
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full_content = f"--- Note created by J.A.R.V.I.S. on {timestamp} ---\n\n{content}\n"
        
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(full_content)
            return f"Note '{title}' has been created at {filepath}, sir."
        except Exception as e:
            return f"I couldn't create that note: {e}"
    
    def get_recent_files(self, hours: int = 24) -> str:
        """Returns files modified within the last N hours."""
        cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
        recent = []
        
        for search_dir in self.search_dirs:
            if not search_dir.exists():
                continue
            try:
                for root, dirs, files in os.walk(search_dir):
                    depth = str(root).count(os.sep) - str(search_dir).count(os.sep)
                    if depth > 2:
                        dirs.clear()
                        continue
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'venv', '__pycache__', '.git')]
                    
                    for filename in files:
                        full_path = os.path.join(root, filename)
                        try:
                            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(full_path))
                            if mtime > cutoff:
                                recent.append({"name": filename, "path": full_path, "modified": mtime.strftime("%H:%M")})
                        except Exception:
                            continue
            except PermissionError:
                continue
        
        if not recent:
            return f"No files were modified in the last {hours} hours, sir."
        
        recent.sort(key=lambda x: x["modified"], reverse=True)
        top = recent[:8]
        lines = [f"Files modified in the last {hours} hours:"]
        for f in top:
            lines.append(f"  • {f['name']} (at {f['modified']})")
        if len(recent) > 8:
            lines.append(f"  ... and {len(recent) - 8} more.")
        return "\n".join(lines)
    
    def organize_downloads(self) -> str:
        """Sorts the Downloads folder by file type into subfolders."""
        downloads = Path.home() / "Downloads"
        if not downloads.exists():
            return "Downloads folder not found, sir."
        
        moved = 0
        skipped: list[str] = []
        for item in downloads.iterdir():
            if item.is_file():
                ext = item.suffix.lower()
                dest_folder = None

                for category, extensions in self.file_categories.items():
                    if ext in extensions:
                        dest_folder = downloads / category
                        break

                if dest_folder:
                    dest_folder.mkdir(exist_ok=True)
                    dest = dest_folder / item.name
                    # Don't clobber an existing file — pick a unique name.
                    if dest.exists():
                        n = 1
                        while dest.exists():
                            dest = dest_folder / f"{item.stem} ({n}){item.suffix}"
                            n += 1
                    try:
                        shutil.move(str(item), str(dest))
                        moved += 1
                    except Exception:
                        # In use / locked / permission — record it, don't hide it.
                        skipped.append(item.name)

        if moved == 0 and not skipped:
            return "Your Downloads folder is already tidy, sir. Nothing to organize."
        msg = f"Downloads organized. {moved} file{'s' if moved != 1 else ''} sorted into categorized folders."
        if skipped:
            k = len(skipped)
            shown = ", ".join(skipped[:5]) + (f", and {k - 5} more" if k > 5 else "")
            msg += f" {k} file{'s' if k != 1 else ''} couldn't be moved (in use or locked): {shown}."
        return msg
    
    def _format_size(self, size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"
