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
        Fuzzy searches for files across common directories.
        Returns top 5 matches with full paths.
        """
        print(f"[FILE AGENT] Searching for: {query}")
        query_lower = query.lower().strip()
        matches = []
        
        for search_dir in self.search_dirs:
            if not search_dir.exists():
                continue
            
            try:
                # Walk max 3 levels deep to avoid extremely slow searches
                for root, dirs, files in os.walk(search_dir):
                    # Limit depth
                    depth = str(root).count(os.sep) - str(search_dir).count(os.sep)
                    if depth > 3:
                        dirs.clear()
                        continue
                    
                    # Skip hidden/system directories
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'venv', '__pycache__', '.git')]
                    
                    for filename in files:
                        if query_lower in filename.lower():
                            full_path = os.path.join(root, filename)
                            try:
                                size = os.path.getsize(full_path)
                                modified = datetime.datetime.fromtimestamp(os.path.getmtime(full_path))
                                matches.append({
                                    "name": filename,
                                    "path": full_path,
                                    "size": self._format_size(size),
                                    "modified": modified.strftime("%Y-%m-%d %H:%M")
                                })
                            except Exception:
                                matches.append({"name": filename, "path": full_path, "size": "?", "modified": "?"})
                            
                            if len(matches) >= 10:
                                break
                    
                    if len(matches) >= 10:
                        break
            except PermissionError:
                continue
        
        if not matches:
            return f"I couldn't find any files matching '{query}' in your common directories, sir."
        
        # Sort by most recently modified
        matches.sort(key=lambda x: x.get("modified", ""), reverse=True)
        top = matches[:5]
        
        result_lines = [f"I found {len(matches)} file{'s' if len(matches) != 1 else ''} matching '{query}':"]
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
        
        # Sanitize filename
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
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
                    try:
                        shutil.move(str(item), str(dest_folder / item.name))
                        moved += 1
                    except Exception:
                        continue
        
        if moved == 0:
            return "Your Downloads folder is already tidy, sir. Nothing to organize."
        return f"Downloads organized. {moved} file{'s' if moved != 1 else ''} sorted into categorized folders."
    
    def _format_size(self, size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"
