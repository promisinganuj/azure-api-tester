"""Request/response tracking — JSONL files + SQLite database.

Logs are stored locally in the output folder (payload_dir).
A global index at ~/.azure-api-tester/index.db maps run_id -> folder for history lookups.
"""

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

from .config import ensure_data_dir


@dataclass
class ApiCallRecord:
    """A single API call record."""
    run_id: str
    variant_name: str
    timestamp: str
    method: str
    url: str
    request_headers: dict
    request_body: Optional[dict]
    response_status: int
    response_headers: dict
    response_body: str
    duration_ms: float
    is_cleanup: bool = False


INDEX_DB_PATH = ensure_data_dir() / "index.db"


def _init_index_db() -> sqlite3.Connection:
    """Initialize the global index database."""
    conn = sqlite3.connect(str(INDEX_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_index (
            id TEXT PRIMARY KEY,
            payload_dir TEXT NOT NULL,
            doc_url TEXT,
            api_title TEXT,
            http_method TEXT,
            url_template TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            total_calls INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _init_local_db(db_path: Path) -> sqlite3.Connection:
    """Initialize a local SQLite database in the payload folder."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            variant_name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            method TEXT NOT NULL,
            url TEXT NOT NULL,
            request_headers TEXT,
            request_body TEXT,
            response_status INTEGER,
            response_headers TEXT,
            response_body TEXT,
            duration_ms REAL,
            is_cleanup INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


class Tracker:
    """Tracks API calls in local JSONL files + local SQLite, with a global index."""

    def __init__(self, run_id: str, doc_url: str, api_title: str = "",
                 http_method: str = "", url_template: str = "",
                 log_dir: str = ""):
        self.run_id = run_id
        self.log_dir = Path(log_dir) if log_dir else ensure_data_dir()

        # Create logs subdirectory in the output folder
        logs_subdir = self.log_dir / "logs"
        logs_subdir.mkdir(parents=True, exist_ok=True)

        # JSONL log file in local folder
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in api_title)[:50]
        self.jsonl_path = logs_subdir / f"{timestamp}_{safe_title}.jsonl"

        # Local SQLite in the output folder
        self.local_db_path = self.log_dir / "tracker.db"
        self.local_conn = _init_local_db(self.local_db_path)

        # Global index entry
        self.index_conn = _init_index_db()
        self.index_conn.execute(
            """INSERT OR REPLACE INTO run_index
               (id, payload_dir, doc_url, api_title, http_method, url_template, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, str(self.log_dir), doc_url, api_title, http_method,
             url_template, datetime.now(timezone.utc).isoformat()),
        )
        self.index_conn.commit()

        self._call_count = 0
        self._success_count = 0
        self._failure_count = 0

    def log_call(self, record: ApiCallRecord) -> None:
        """Log an API call to both JSONL and local SQLite."""
        self._call_count += 1
        if 200 <= record.response_status < 300:
            self._success_count += 1
        else:
            self._failure_count += 1

        # Write to JSONL
        with open(self.jsonl_path, "a") as f:
            f.write(json.dumps(asdict(record), default=str) + "\n")

        # Write to local SQLite
        self.local_conn.execute(
            """INSERT INTO api_calls
               (run_id, variant_name, timestamp, method, url,
                request_headers, request_body,
                response_status, response_headers, response_body,
                duration_ms, is_cleanup)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.run_id,
                record.variant_name,
                record.timestamp,
                record.method,
                record.url,
                json.dumps(record.request_headers),
                json.dumps(record.request_body) if record.request_body else None,
                record.response_status,
                json.dumps(record.response_headers),
                record.response_body,
                record.duration_ms,
                1 if record.is_cleanup else 0,
            ),
        )
        self.local_conn.commit()

    def finish(self) -> None:
        """Finalize the test run in both local and global DBs."""
        now = datetime.now(timezone.utc).isoformat()

        # Update global index
        self.index_conn.execute(
            """UPDATE run_index
               SET finished_at = ?, total_calls = ?, success_count = ?, failure_count = ?
               WHERE id = ?""",
            (now, self._call_count, self._success_count, self._failure_count, self.run_id),
        )
        self.index_conn.commit()

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def success_count(self) -> int:
        return self._success_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def close(self) -> None:
        """Close all database connections."""
        self.local_conn.close()
        self.index_conn.close()


def get_run_history(limit: int = 20) -> list[dict]:
    """Get recent test run history from the global index."""
    conn = _init_index_db()
    cursor = conn.execute(
        """SELECT id, payload_dir, doc_url, api_title, http_method, started_at,
                  finished_at, total_calls, success_count, failure_count
           FROM run_index ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    )
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_run_details(run_id: str) -> tuple[Optional[dict], list[dict]]:
    """Get details of a specific test run.

    Looks up the payload folder from the global index, then reads
    the local tracker.db for full call details.
    """
    # Look up folder from global index
    index_conn = _init_index_db()
    cursor = index_conn.execute(
        "SELECT * FROM run_index WHERE id = ?", (run_id,))
    columns = [desc[0] for desc in cursor.description]
    row = cursor.fetchone()
    index_conn.close()

    if not row:
        return None, []

    run_info = dict(zip(columns, row))
    payload_dir = Path(run_info["payload_dir"])
    local_db = payload_dir / "tracker.db"

    if not local_db.exists():
        run_info["_note"] = "(output folder removed — call details unavailable)"
        return run_info, []

    # Read call details from local DB
    local_conn = _init_local_db(local_db)
    cursor = local_conn.execute(
        """SELECT variant_name, timestamp, method, url, response_status,
                  duration_ms, is_cleanup, request_body, response_body
           FROM api_calls WHERE run_id = ? ORDER BY id""",
        (run_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    calls = [dict(zip(columns, row)) for row in cursor.fetchall()]
    local_conn.close()

    return run_info, calls
