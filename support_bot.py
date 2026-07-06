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


def _lookup_customer_by_exact_email(email: str, db_path: Optional[Path] = None) -> tuple[int, list[dict]]:
    """Look up a customer by an exact (case-insensitive) email match.

    Email is guaranteed unique in the Customer table, so this returns at most
    one row.  This is the identifier used by the PII gate — unlike a ``LIKE``
    search it will not match on partial/substring input, so a caller cannot
    fish for another customer's account with a fragment.
    """
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT CustomerId, FirstName, LastName, Email, City, Country "
        "FROM Customer WHERE lower(Email) = ?",
        (email.strip().lower(),),
    ).fetchall()
    conn.close()

    customers = [dict(row) for row in rows]
    return len(customers), customers


def _lookup_customer_by_name(customer_name: str, db_path: Optional[Path] = None) -> tuple[int, list[dict]]:
    """Look up customers by name.  Returns (count, list_of_customer_dicts).

    May return multiple matches when customers share a name (e.g. two "Luis"
    accounts).  Used only to *detect* existence/ambiguity — never to release
    personal data, which requires a verified email (see
    :func:`resolve_customer_for_pii`).
    """
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT CustomerId, FirstName, LastName, Email, City, Country "
        "FROM Customer WHERE lower(FirstName || ' ' || LastName) LIKE ?",
        (f"%{customer_name.lower()}%",),
    ).fetchall()
    conn.close()

    customers = [dict(row) for row in rows]
    return len(customers), customers


# Shown when we decline to release personal data without a verified email.
_EMAIL_REQUIRED = (
    "To protect account privacy, I can only look up personal account details "
    "(like purchase history) with the email address on the account. "
    "What's the email address on your account?"
)


def resolve_customer_for_pii(
    customer_email: str = "",
    customer_name: str = "",
    db_path: Optional[Path] = None,
) -> tuple[Optional[int], Optional[str]]:
    """Resolve a customer for personal-data access.  Email-only by policy.

    Returns ``(customer_id, error_message)`` where exactly one is non-None.
    Personal data (purchase history, recommendations) is released ONLY on a
    unique, exact email match.  A name alone never unlocks PII, and we never
    enumerate the accounts that match a name — both would let a caller
    impersonate another customer (e.g. picking one of two "Luis" accounts),
    which is exactly the failure this gate exists to prevent.
    """
    email = (customer_email or "").strip()
    if not email:
        # A name may have been supplied, but a name is not proof of identity.
        return None, _EMAIL_REQUIRED

    count, customers = _lookup_customer_by_exact_email(email, db_path)
    if count == 0:
        return None, (
            f"I don't see an account under {email}. Could you double-check the "
            "email address on the account?"
        )
    return customers[0]["CustomerId"], None


def get_customer_purchase_history(
    customer_email: str = "",
    customer_name: str = "",
    db_path: Optional[Path] = None,
) -> str:
    """Return a customer's recent orders, itemized — email-verified only.

    Lists the last few invoices and, under each, the tracks purchased and the
    album each belongs to — so "what did I buy?" / "what albums?" is answerable,
    not just totals.  Personal data is released only on a unique, exact email
    match (see :func:`resolve_customer_for_pii`); a name alone returns a privacy
    prompt.
    """
    cid, error = resolve_customer_for_pii(customer_email, customer_name, db_path)
    if error:
        return error

    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT c.FirstName || ' ' || c.LastName AS customer_name,
               i.InvoiceId, i.InvoiceDate, i.Total,
               t.Name AS track, al.Title AS album
        FROM Customer c
        JOIN Invoice i ON i.CustomerId = c.CustomerId
        JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
        JOIN Track t ON t.TrackId = il.TrackId
        LEFT JOIN Album al ON al.AlbumId = t.AlbumId
        WHERE c.CustomerId = ?
          AND i.InvoiceId IN (
              SELECT InvoiceId FROM Invoice WHERE CustomerId = ?
              ORDER BY InvoiceDate DESC LIMIT 5
          )
        ORDER BY i.InvoiceDate DESC, il.InvoiceLineId
        """,
        (cid, cid),
    ).fetchall()
    conn.close()

    if not rows:
        return "That account has no purchase history in our store yet."

    # Group the line items under their invoice, preserving newest-first order.
    name = rows[0]["customer_name"]
    invoices: dict = {}
    for r in rows:
        inv = invoices.setdefault(
            r["InvoiceId"],
            {"date": r["InvoiceDate"], "total": r["Total"], "items": []},
        )
        inv["items"].append(
            f"{r['track']} — {r['album']}" if r["album"] else r["track"]
        )

    blocks = []
    for inv_id, inv in invoices.items():
        header = f"Invoice {inv_id} · {inv['date']} · ${inv['total']:.2f}"
        lines = "\n".join(f"    • {item}" for item in inv["items"])
        blocks.append(f"{header}\n{lines}")

    return f"{name} — your last {len(invoices)} orders:\n\n" + "\n\n".join(blocks)


def recommend_music_for_customer(
    customer_email: str = "",
    customer_name: str = "",
    db_path: Optional[Path] = None,
) -> str:
    """Recommend genres from a customer's purchase history — email-verified only.

    Personal listening history is released only on a unique, exact email match
    (see :func:`resolve_customer_for_pii`).  A name alone returns a privacy
    prompt.  For catalog-wide (non-personal) suggestions, use the top-sellers
    and genre-browse tools instead.
    """
    cid, error = resolve_customer_for_pii(customer_email, customer_name, db_path)
    if error:
        return error

    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT c.FirstName || ' ' || c.LastName AS customer_name,
               g.Name AS genre_name,
               COUNT(t.TrackId) AS track_count
        FROM Customer c
        JOIN Invoice i ON i.CustomerId = c.CustomerId
        JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
        JOIN Track t ON t.TrackId = il.TrackId
        JOIN Genre g ON g.GenreId = t.GenreId
        WHERE c.CustomerId = ?
        GROUP BY c.CustomerId, g.GenreId
        ORDER BY track_count DESC
        LIMIT 5
        """,
        (cid,),
    ).fetchall()
    conn.close()

    if not rows:
        return "That account has no listening history yet to recommend from."

    genres = ", ".join(f"{row['genre_name']} ({row['track_count']} tracks)" for row in rows)
    return (
        f"{rows[0]['customer_name']} appears to enjoy these genres: {genres}. "
        "A strong recommendation theme is to spotlight those genres first."
    )


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


def browse_albums_by_genre(genre: str, db_path: Optional[Path] = None) -> str:
    """List albums in a genre, most-popular first (ranked by units sold).

    The genre name is fuzzy-matched, so "punk" resolves to "Alternative & Punk".
    The catalog stores no release-date data, so there is no truthful way to sort
    by "newest" — results are ranked by sales instead, and the message says so.
    """
    genre = (genre or "").strip()
    if not genre:
        return "Which genre would you like to browse? For example: Rock, Jazz, or Alternative & Punk."

    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT al.Title AS album,
               ar.Name AS artist,
               COALESCE(SUM(il.Quantity), 0) AS units_sold
        FROM Genre g
        JOIN Track t ON t.GenreId = g.GenreId
        JOIN Album al ON al.AlbumId = t.AlbumId
        JOIN Artist ar ON ar.ArtistId = al.ArtistId
        LEFT JOIN InvoiceLine il ON il.TrackId = t.TrackId
        WHERE lower(g.Name) LIKE ?
        GROUP BY al.AlbumId
        ORDER BY units_sold DESC, al.Title
        LIMIT 10
        """,
        (f"%{genre.lower()}%",),
    ).fetchall()
    conn.close()

    if not rows:
        return (
            f"I couldn't find a genre matching '{genre}'. Want me to list the genres we carry?"
        )

    listed = "\n".join(
        f"- {row['album']} — {row['artist']}"
        + (f" ({row['units_sold']} sold)" if row["units_sold"] else "")
        for row in rows
    )
    return (
        f"Albums in '{genre}' (we don't track release dates, so these are ranked by "
        f"sales rather than recency):\n{listed}"
    )


def top_selling_albums(genre: str = "", db_path: Optional[Path] = None) -> str:
    """Return the best-selling albums overall, or within a genre if one is given.

    Ranked by total units sold (``InvoiceLine.Quantity``).  Genre is
    fuzzy-matched, so "punk" resolves to "Alternative & Punk".
    """
    genre = (genre or "").strip()
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    where = ""
    params: list = []
    if genre:
        where = "WHERE lower(g.Name) LIKE ?"
        params.append(f"%{genre.lower()}%")

    rows = conn.execute(
        f"""
        SELECT al.Title AS album,
               ar.Name AS artist,
               SUM(il.Quantity) AS units_sold
        FROM InvoiceLine il
        JOIN Track t ON t.TrackId = il.TrackId
        JOIN Album al ON al.AlbumId = t.AlbumId
        JOIN Artist ar ON ar.ArtistId = al.ArtistId
        JOIN Genre g ON g.GenreId = t.GenreId
        {where}
        GROUP BY al.AlbumId
        ORDER BY units_sold DESC, al.Title
        LIMIT 5
        """,
        params,
    ).fetchall()
    conn.close()

    scope = f" in '{genre}'" if genre else ""
    if not rows:
        return f"I couldn't find sales data for albums{scope}."

    listed = "\n".join(
        f"- {row['album']} — {row['artist']} ({row['units_sold']} sold)" for row in rows
    )
    return f"Best-selling albums{scope}:\n{listed}"


# Common trailing filler words that should not be part of a customer name
_FILLER_WORDS = frozenset({
    "please", "thanks", "thank", "thankyou", "thank-you", "pls",
    "help", "can", "could", "would", "should", "is", "are", "was",
    "were", "do", "did", "has", "have", "had", "show", "get", "look",
    "i", "my", "me", "for", "the", "a", "an",
})


def _extract_customer_name(user_message: str) -> str:
    """Extract a customer name from a user message.

    Strips trailing punctuation and filler words, then returns the last
    meaningful word as the candidate name.  This is a keyword-router
    fallback — the LLM-facing path in ``app.py`` is the source of truth.
    """
    # Strip trailing punctuation
    cleaned = user_message.strip().rstrip(".,!?;:")
    words = cleaned.split()
    # Pop trailing filler words
    while words and words[-1].lower() in _FILLER_WORDS:
        words.pop()
    # Return the last remaining word, stripped
    return words[-1].strip() if words else ""


def build_support_response(user_message: str, db_path: Optional[Path] = None) -> str:
    lowered = user_message.lower()
    if "recommend" in lowered or "music" in lowered or "song" in lowered:
        customer_name = _extract_customer_name(user_message)
        if not customer_name:
            return "I'd need a customer name to look up recommendations. Could you provide one?"
        return recommend_music_for_customer(customer_name=customer_name, db_path=db_path)

    if "purchase" in lowered or "invoice" in lowered or "receipt" in lowered or "order" in lowered:
        customer_name = _extract_customer_name(user_message)
        if not customer_name:
            return "I'd need a customer name to look up purchase history. Could you provide one?"
        return get_customer_purchase_history(customer_name=customer_name, db_path=db_path)

    return (
        "I can help with music recommendations and purchase history. "
        "Try asking: 'Recommend music for Luis' or 'Show my invoice history for Luis'."
    )
