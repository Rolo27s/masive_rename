from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ENCODINGS_TO_TRY = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
USERNAME_CHARS = r"A-Za-z0-9_"
SEAT_PATTERN = re.compile(r"^Seat\s+\d+:\s(.+?)\s\([^\r\n]*in chips\)", re.MULTILINE)
DEALT_PATTERN = re.compile(r"^Dealt to\s+(.+?)\s+\[[^\r\n]*\]", re.MULTILINE)


@dataclass(frozen=True)
class ParsedText:
    text: str
    encoding: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read all .txt files from bronze, persist username anonymization in SQLite, "
            "and write anonymized copies to gold."
        )
    )
    parser.add_argument(
        "--bronze",
        type=Path,
        default=Path("data") / "bronze",
        help="Bronze input folder (default: data/bronze)",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=Path("data") / "gold",
        help="Gold output folder (default: data/gold)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data") / "gold" / "anonymization.sqlite",
        help="SQLite path for persistent name mapping (default: data/gold/anonymization.sqlite)",
    )
    parser.add_argument(
        "--alias-prefix",
        default="randomname",
        help="Alias prefix to generate new names (default: randomname)",
    )
    parser.add_argument(
        "--alias-padding",
        type=int,
        default=6,
        help="Zero padding width for alias numeric suffix (default: 6)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess all files even if unchanged in metadata table.",
    )
    return parser.parse_args()


def read_with_fallback(path: Path) -> ParsedText:
    raw = path.read_bytes()
    for encoding in ENCODINGS_TO_TRY:
        try:
            return ParsedText(text=raw.decode(encoding), encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", raw, 0, 1, f"Unable to decode file: {path}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            original_name TEXT PRIMARY KEY,
            anon_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            source_path TEXT PRIMARY KEY,
            gold_path TEXT NOT NULL,
            source_mtime REAL NOT NULL,
            source_size INTEGER NOT NULL,
            processed_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def collect_usernames(text: str) -> set[str]:
    names = {m.group(1).strip() for m in SEAT_PATTERN.finditer(text)}
    names.update(m.group(1).strip() for m in DEALT_PATTERN.finditer(text))
    return {name for name in names if name}


def get_existing_mappings(conn: sqlite3.Connection, names: set[str]) -> dict[str, str]:
    if not names:
        return {}
    placeholders = ",".join("?" for _ in names)
    query = f"SELECT original_name, anon_name FROM players WHERE original_name IN ({placeholders})"
    rows = conn.execute(query, sorted(names)).fetchall()
    return {original: anon for original, anon in rows}


def get_next_alias_index(conn: sqlite3.Connection, alias_prefix: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(CAST(SUBSTR(anon_name, ?) AS INTEGER)), 0)
        FROM players
        WHERE anon_name LIKE ?
        """,
        (len(alias_prefix) + 1, f"{alias_prefix}%"),
    ).fetchone()
    return int(row[0]) + 1


def ensure_mappings(
    conn: sqlite3.Connection, names: set[str], alias_prefix: str, alias_padding: int
) -> dict[str, str]:
    mapping = get_existing_mappings(conn, names)
    missing = sorted(name for name in names if name not in mapping)
    next_index = get_next_alias_index(conn, alias_prefix)
    for original_name in missing:
        alias = f"{alias_prefix}{next_index:0{alias_padding}d}"
        conn.execute(
            "INSERT INTO players (original_name, anon_name) VALUES (?, ?)",
            (original_name, alias),
        )
        mapping[original_name] = alias
        next_index += 1
    return mapping


def anonymize_text(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    ordered_names = sorted(mapping.keys(), key=len, reverse=True)
    pattern = re.compile(
        rf"(?<![{USERNAME_CHARS}])(?:{'|'.join(re.escape(name) for name in ordered_names)})(?![{USERNAME_CHARS}])"
    )
    return pattern.sub(lambda match: mapping[match.group(0)], text)


def is_file_unchanged(
    conn: sqlite3.Connection, source_path: Path, source_mtime: float, source_size: int
) -> bool:
    row = conn.execute(
        "SELECT source_mtime, source_size FROM files WHERE source_path = ?",
        (str(source_path.resolve()),),
    ).fetchone()
    if row is None:
        return False
    saved_mtime, saved_size = row
    return float(saved_mtime) == source_mtime and int(saved_size) == source_size


def upsert_file_metadata(
    conn: sqlite3.Connection,
    source_path: Path,
    gold_path: Path,
    source_mtime: float,
    source_size: int,
) -> None:
    conn.execute(
        """
        INSERT INTO files (source_path, gold_path, source_mtime, source_size, processed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_path) DO UPDATE SET
            gold_path = excluded.gold_path,
            source_mtime = excluded.source_mtime,
            source_size = excluded.source_size,
            processed_at = excluded.processed_at
        """,
        (
            str(source_path.resolve()),
            str(gold_path.resolve()),
            source_mtime,
            source_size,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def process_files(
    bronze_dir: Path,
    gold_dir: Path,
    conn: sqlite3.Connection,
    alias_prefix: str,
    alias_padding: int,
    force: bool,
) -> tuple[int, int, int]:
    bronze_files = sorted(path for path in bronze_dir.rglob("*.txt") if path.is_file())
    processed = 0
    skipped = 0
    discovered_names = 0

    for source_path in bronze_files:
        stat = source_path.stat()
        mtime = float(stat.st_mtime)
        size = int(stat.st_size)
        if not force and is_file_unchanged(conn, source_path, mtime, size):
            skipped += 1
            continue

        parsed = read_with_fallback(source_path)
        names = collect_usernames(parsed.text)
        discovered_names += len(names)
        mapping = ensure_mappings(conn, names, alias_prefix, alias_padding)
        anonymized = anonymize_text(parsed.text, mapping)

        relative_path = source_path.relative_to(bronze_dir)
        gold_path = gold_dir / relative_path
        gold_path.parent.mkdir(parents=True, exist_ok=True)
        gold_path.write_text(anonymized, encoding=parsed.encoding, newline="")

        upsert_file_metadata(conn, source_path, gold_path, mtime, size)
        processed += 1

    return processed, skipped, discovered_names


def main() -> int:
    args = parse_args()
    bronze_dir: Path = args.bronze
    gold_dir: Path = args.gold
    db_path: Path = args.db

    if not bronze_dir.exists():
        raise FileNotFoundError(f"Bronze folder does not exist: {bronze_dir}")
    if not bronze_dir.is_dir():
        raise NotADirectoryError(f"Bronze path is not a directory: {bronze_dir}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        processed, skipped, discovered_names = process_files(
            bronze_dir=bronze_dir,
            gold_dir=gold_dir,
            conn=conn,
            alias_prefix=args.alias_prefix,
            alias_padding=args.alias_padding,
            force=args.force,
        )
        conn.commit()

        players_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        print(
            "Done. "
            f"Processed: {processed}, Skipped unchanged: {skipped}, "
            f"Names discovered this run: {discovered_names}, Total mapped players: {players_count}."
        )
        print(f"Mapping DB: {db_path.resolve()}")
        print(f"Gold folder: {gold_dir.resolve()}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
