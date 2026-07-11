import os
import shutil
import sqlite3
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from ..models import AppInfo, Conversation


class BaseAdapter(ABC):
    name: str = ""
    display_name: str = ""

    def __init__(self):
        self.user_home = Path.home()
        self.appdata_roaming = os.environ.get("APPDATA", str(self.user_home / "AppData" / "Roaming"))
        self.appdata_local = os.environ.get("LOCALAPPDATA", str(self.user_home / "AppData" / "Local"))
        self.program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")

    @abstractmethod
    def detect(self) -> bool:
        pass

    @abstractmethod
    def get_app_info(self) -> AppInfo:
        pass

    @abstractmethod
    def list_conversations(self) -> List[Conversation]:
        pass

    @abstractmethod
    def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        pass

    @staticmethod
    def _readonly_uri(db_path: str) -> str:
        """Build a cross-platform read-only SQLite URI."""
        return f"{Path(db_path).resolve().as_uri()}?mode=ro"

    def _safe_copy_db(self, db_path: str) -> str:
        """Create a transaction-consistent SQLite snapshot.

        A plain file copy can miss committed rows that still live in a WAL file.
        Prefer SQLite's online backup API so the snapshot contains one coherent
        database view. If the source cannot be opened through SQLite, fall back
        to copying the database plus its WAL/SHM sidecars.
        """
        tmp_dir = tempfile.mkdtemp(prefix="chat_export_")
        tmp_path = os.path.join(tmp_dir, os.path.basename(db_path))

        source = None
        target = None
        try:
            source = sqlite3.connect(self._readonly_uri(db_path), uri=True, timeout=5.0)
            target = sqlite3.connect(tmp_path)
            source.backup(target)
            target.commit()
            return tmp_path
        except sqlite3.Error:
            if target:
                target.close()
                target = None
            if source:
                source.close()
                source = None

            shutil.copy2(db_path, tmp_path)
            for suffix in ("-wal", "-shm"):
                sidecar = f"{db_path}{suffix}"
                if os.path.exists(sidecar):
                    shutil.copy2(sidecar, f"{tmp_path}{suffix}")
            return tmp_path
        finally:
            if target:
                target.close()
            if source:
                source.close()

    def _connect_db(self, db_path: str, copy: bool = False) -> sqlite3.Connection:
        if copy:
            actual_path = self._safe_copy_db(db_path)
            conn = sqlite3.connect(actual_path)
        else:
            conn = sqlite3.connect(self._readonly_uri(db_path), uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
        except sqlite3.Error:
            pass
        return conn

    @staticmethod
    def _ts_to_dt(ts, ms: bool = True):
        from datetime import datetime
        if ts is None:
            return None
        if isinstance(ts, (int, float)):
            try:
                return datetime.fromtimestamp(ts / 1000) if ms else datetime.fromtimestamp(ts)
            except (ValueError, OSError, OverflowError):
                return None
        if isinstance(ts, str):
            s = ts.strip()
            if not s:
                return None
            for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                pass
            try:
                num = float(s)
                return datetime.fromtimestamp(num / 1000) if ms else datetime.fromtimestamp(num)
            except (ValueError, OSError, OverflowError):
                return None
        return None
