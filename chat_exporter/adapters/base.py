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

    def _safe_copy_db(self, db_path: str) -> str:
        tmp_dir = tempfile.mkdtemp(prefix="chat_export_")
        tmp_path = os.path.join(tmp_dir, os.path.basename(db_path))
        shutil.copy2(db_path, tmp_path)
        return tmp_path

    def _connect_db(self, db_path: str, copy: bool = False) -> sqlite3.Connection:
        if copy:
            actual_path = self._safe_copy_db(db_path)
            conn = sqlite3.connect(actual_path)
        else:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
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
