import os
import sqlite3
from pathlib import Path
from typing import Optional

import requests

CHINOOK_SQL_URL = "https://raw.githubusercontent.com/lerocha/chinook-database/master/ChinookDatabase/DataSources/Chinook_Sqlite.sql"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "chinook.db"


def ensure_chinook_database(db_path: Optional[Path] = None) -> Path:
    db_path = Path(db_path or DEFAULT_DB_PATH)
    if db_path.exists():
        return db_path

    try:
        response = requests.get(CHINOOK_SQL_URL, timeout=30)
        response.raise_for_status()
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(response.text)
            conn.commit()
        finally:
            conn.close()
    except (requests.RequestException, sqlite3.Error):
        db_path.unlink(missing_ok=True)
        raise

    return db_path


def get_customer_purchase_history(customer_name: str, db_path: Optional[Path] = None) -> str:
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT c.FirstName || ' ' || c.LastName AS customer_name,
               i.InvoiceId,
               i.InvoiceDate,
               i.Total
        FROM Customer c
        JOIN Invoice i ON i.CustomerId = c.CustomerId
        WHERE lower(c.FirstName || ' ' || c.LastName) LIKE ?
        ORDER BY i.InvoiceDate DESC
        LIMIT 5
    """
    rows = conn.execute(query, (f"%{customer_name.lower()}%",)).fetchall()
    conn.close()

    if not rows:
        return "No purchase history found for that customer."

    formatted = [
        f"- Invoice {row['InvoiceId']} on {row['InvoiceDate']}: ${row['Total']:.2f}"
        for row in rows
    ]
    return f"{rows[0]['customer_name']} has recent purchases in the store.\n" + "\n".join(formatted)


def recommend_music_for_customer(customer_name: str, db_path: Optional[Path] = None) -> str:
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT c.FirstName || ' ' || c.LastName AS customer_name,
               g.Name AS genre_name,
               COUNT(t.TrackId) AS track_count
        FROM Customer c
        JOIN Invoice i ON i.CustomerId = c.CustomerId
        JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
        JOIN Track t ON t.TrackId = il.TrackId
        JOIN Genre g ON g.GenreId = t.GenreId
        WHERE lower(c.FirstName || ' ' || c.LastName) LIKE ?
        GROUP BY c.CustomerId, g.GenreId
        ORDER BY track_count DESC
        LIMIT 5
    """
    rows = conn.execute(query, (f"%{customer_name.lower()}%",)).fetchall()
    conn.close()

    if not rows:
        return "No listening history found to recommend from."

    genres = ", ".join(f"{row['genre_name']} ({row['track_count']} tracks)" for row in rows)
    return f"{rows[0]['customer_name']} appears to enjoy these genres: {genres}. A strong recommendation theme is to spotlight those genres first."


def get_inventory_snapshot(db_path: Optional[Path] = None) -> str:
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT
            (SELECT COUNT(*) FROM Track) AS track_count,
            (SELECT COUNT(*) FROM Album) AS album_count,
            (SELECT COUNT(*) FROM Artist) AS artist_count
    """
    row = conn.execute(query).fetchone()
    conn.close()

    return (
        f"The store currently has {row['track_count']} tracks across {row['album_count']} albums "
        f"from {row['artist_count']} artists."
    )


def find_artists_by_keyword(keyword: str, db_path: Optional[Path] = None) -> str:
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    search_term = f"%{keyword.lower()}%"
    rows = conn.execute(
        "SELECT Name FROM Artist WHERE lower(Name) LIKE ? ORDER BY Name LIMIT 10",
        (search_term,),
    ).fetchall()
    conn.close()

    if rows:
        artists = ", ".join(row["Name"] for row in rows)
        return f"I found these artists matching '{keyword}': {artists}."

    if keyword.lower() == "italian":
        return (
            "I don't see any artists explicitly labeled as Italian in this catalog, "
            "but I can help look for similar artists by name or genre."
        )

    return f"I couldn't find any artists matching '{keyword}' in the catalog."


def get_most_common_genres(limit: int = 5, db_path: Optional[Path] = None) -> str:
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT g.Name AS genre_name, COUNT(*) AS track_count
        FROM Track t
        JOIN Genre g ON g.GenreId = t.GenreId
        GROUP BY g.GenreId
        ORDER BY track_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        return "No genre data is available in the catalog."

    formatted = ", ".join(f"{row['genre_name']} ({row['track_count']} tracks)" for row in rows)
    return f"The most common genres in the catalog are: {formatted}."


def build_support_response(user_message: str, db_path: Optional[Path] = None) -> str:
    lowered = user_message.lower()
    if "recommend" in lowered or "music" in lowered or "song" in lowered:
        customer_name = user_message.split()[-1].strip().rstrip(".")
        return recommend_music_for_customer(customer_name, db_path=db_path)

    if "purchase" in lowered or "invoice" in lowered or "receipt" in lowered or "order" in lowered:
        customer_name = user_message.split()[-1].strip().rstrip(".")
        return get_customer_purchase_history(customer_name, db_path=db_path)

    return (
        "I can help with music recommendations and purchase history. "
        "Try asking: 'Recommend music for Luis' or 'Show my invoice history for Luis'."
    )
