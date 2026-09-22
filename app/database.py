"""SQLite 持久化:启动时建表,配方版本存储。"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("NUTRIENT_DB", "/data/nutrient.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recipe_versions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                version       INTEGER NOT NULL,
                request_json  TEXT NOT NULL,
                result_json   TEXT NOT NULL,
                feasible      INTEGER NOT NULL,
                note          TEXT,
                created_at    TEXT NOT NULL,
                UNIQUE (recipe_id, version)
            );
            """
        )


def get_conn():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_version(
    conn: sqlite3.Connection,
    name: str,
    request_payload: Dict[str, Any],
    result: Dict[str, Any],
    note: Optional[str] = None,
) -> Dict[str, Any]:
    now = _now()
    row = conn.execute(
        "SELECT id FROM recipes WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO recipes (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, now, now),
        )
        recipe_id = cur.lastrowid
        version = 1
    else:
        recipe_id = row["id"]
        version = (
            conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v "
                "FROM recipe_versions WHERE recipe_id = ?",
                (recipe_id,),
            ).fetchone()["v"]
        )
        conn.execute(
            "UPDATE recipes SET updated_at = ? WHERE id = ?", (now, recipe_id)
        )

    conn.execute(
        """
        INSERT INTO recipe_versions
            (recipe_id, version, request_json, result_json, feasible, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recipe_id,
            version,
            json.dumps(request_payload, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            1 if result.get("feasible") else 0,
            note,
            now,
        ),
    )
    conn.commit()
    return {
        "recipe_id": recipe_id,
        "name": name,
        "version": version,
        "feasible": bool(result.get("feasible")),
        "created_at": now,
    }


def list_recipes(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.id, r.name, r.created_at, r.updated_at,
               COUNT(v.id) AS version_count,
               MAX(v.version) AS latest_version
        FROM recipes r
        LEFT JOIN recipe_versions v ON v.recipe_id = r.id
        GROUP BY r.id
        ORDER BY r.updated_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_version(
    conn: sqlite3.Connection, name: str, version: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    recipe = conn.execute(
        "SELECT id FROM recipes WHERE name = ?", (name,)
    ).fetchone()
    if recipe is None:
        return None
    if version is None:
        row = conn.execute(
            """
            SELECT * FROM recipe_versions
            WHERE recipe_id = ? ORDER BY version DESC LIMIT 1
            """,
            (recipe["id"],),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM recipe_versions WHERE recipe_id = ? AND version = ?",
            (recipe["id"], version),
        ).fetchone()
    if row is None:
        return None
    return {
        "recipe_id": row["recipe_id"],
        "version": row["version"],
        "request": json.loads(row["request_json"]),
        "result": json.loads(row["result_json"]),
        "feasible": bool(row["feasible"]),
        "note": row["note"],
        "created_at": row["created_at"],
    }
