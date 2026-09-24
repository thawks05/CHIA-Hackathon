"""Append-only experiment records. Parent process is the sole SQLite writer."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class ResultsDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS experiments (
                run_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evaluations (
                run_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                arm TEXT NOT NULL, candidate_id TEXT NOT NULL,
                status TEXT NOT NULL, candidate_json TEXT NOT NULL,
                result_json TEXT NOT NULL, PRIMARY KEY(run_id, ordinal));
        """)

    def start(self, run_id: str, metadata: dict):
        self.connection.execute("INSERT INTO experiments VALUES (?,?)", (run_id, json.dumps(metadata, sort_keys=True)))
        self.connection.commit()

    def record(self, run_id: str, ordinal: int, candidate: dict, result: dict):
        self.connection.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)", (
            run_id, ordinal, candidate.get("arm", "manual"), result["candidate_id"], result["status"],
            json.dumps(candidate, sort_keys=True), json.dumps(result, sort_keys=True)))
        self.connection.commit()

    def rows(self, run_id: str):
        return [{"ordinal": row[0], "arm": row[1], "candidate": json.loads(row[2]), "result": json.loads(row[3])}
                for row in self.connection.execute("SELECT ordinal,arm,candidate_json,result_json FROM evaluations WHERE run_id=? ORDER BY ordinal", (run_id,))]

    def close(self):
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.connection.close()
