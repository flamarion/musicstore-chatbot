import os
import sqlite3
from pathlib import Path
from typing import Optional

import requests
from langsmith import traceable

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


@traceable(run_type="retriever", name="chinook_query")
def _retrieve(sql: str, params: tuple = (), db_path: Optional[Path] = None) -> list[dict]:
    """Run a read query against Chinook and return the rows as dicts.

    Every tool's data access goes through here, so in LangSmith each lookup shows
    up as a distinct **retriever** run nested under its tool call — the store DB
    is the bot's retrieval source.  Centralizing the connection also means one
    place opens and (always) closes it.
    """
    db_path = ensure_chinook_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _lookup_customer_by_exact_email(email: str, db_path: Optional[Path] = None) -> list[dict]:
    """Look up a customer by an exact (case-insensitive) email match.

    Email is guaranteed unique in the Customer table, so this returns at most
    one row.  This is the identifier used by the PII gate — unlike a ``LIKE``
    search it will not match on partial/substring input, so a caller cannot
    fish for another customer's account with a fragment.
    """
    return _retrieve(
        "SELECT CustomerId, FirstName, LastName, Email, City, Country "
        "FROM Customer WHERE lower(Email) = ?",
        (email.strip().lower(),),
        db_path,
    )


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

    customers = _lookup_customer_by_exact_email(email, db_path)
    if not customers:
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

    rows = _retrieve(
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
        db_path,
    )

    if not rows:
        return "That account has no purchase history in our store yet."

    return _format_purchase_history(rows)


@traceable(run_type="parser", name="format_purchase_history")
def _format_purchase_history(rows: list[dict]) -> str:
    """Turn the flat invoice-line rows into the itemized, newest-first reply.

    Traced as a **parser** run: it's the step that shapes raw retrieved rows into
    the customer-facing text (grouping line items under their invoice).
    """
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

    rows = _retrieve(
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
        db_path,
    )

    if not rows:
        return "That account has no listening history yet to recommend from."

    genres = ", ".join(f"{row['genre_name']} ({row['track_count']} tracks)" for row in rows)
    return (
        f"{rows[0]['customer_name']} appears to enjoy these genres: {genres}. "
        "A strong recommendation theme is to spotlight those genres first."
    )


def get_inventory_snapshot(db_path: Optional[Path] = None) -> str:
    row = _retrieve(
        """
        SELECT
            (SELECT COUNT(*) FROM Track) AS track_count,
            (SELECT COUNT(*) FROM Album) AS album_count,
            (SELECT COUNT(*) FROM Artist) AS artist_count
        """,
        (),
        db_path,
    )[0]

    return (
        f"The store currently has {row['track_count']} tracks across {row['album_count']} albums "
        f"from {row['artist_count']} artists."
    )


def find_artists_by_keyword(keyword: str, db_path: Optional[Path] = None) -> str:
    rows = _retrieve(
        "SELECT Name FROM Artist WHERE lower(Name) LIKE ? ORDER BY Name LIMIT 10",
        (f"%{keyword.lower()}%",),
        db_path,
    )

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
    rows = _retrieve(
        """
        SELECT g.Name AS genre_name, COUNT(*) AS track_count
        FROM Track t
        JOIN Genre g ON g.GenreId = t.GenreId
        GROUP BY g.GenreId
        ORDER BY track_count DESC
        LIMIT ?
        """,
        (limit,),
        db_path,
    )

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

    rows = _retrieve(
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
        db_path,
    )

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


def get_albums_by_artist(artist: str, db_path: Optional[Path] = None) -> str:
    """List the albums a given artist has in the catalog.

    The artist name is fuzzy-matched (``LIKE``), so "nirvana" resolves to
    "Nirvana" and a partial like "beatles" still lands.  Albums are annotated
    with units sold (0 shown as "in catalog") and grouped under each matching
    artist, so a keyword that hits several artists stays readable.  Answers
    "which <artist> albums do you have?" — the gap the genre/keyword tools left.
    """
    artist = (artist or "").strip()
    if not artist:
        return "Which artist would you like albums for? For example: Nirvana, Metallica, or U2."

    rows = _retrieve(
        """
        SELECT ar.Name AS artist,
               al.Title AS album,
               COALESCE(SUM(il.Quantity), 0) AS units_sold
        FROM Artist ar
        JOIN Album al ON al.ArtistId = ar.ArtistId
        LEFT JOIN Track t ON t.AlbumId = al.AlbumId
        LEFT JOIN InvoiceLine il ON il.TrackId = t.TrackId
        WHERE lower(ar.Name) LIKE ?
        GROUP BY al.AlbumId
        ORDER BY ar.Name, units_sold DESC, al.Title
        LIMIT 25
        """,
        (f"%{artist.lower()}%",),
        db_path,
    )

    if not rows:
        return (
            f"I couldn't find any albums by an artist matching '{artist}' in the catalog. "
            "Want me to search for similar artist names, or browse a genre instead?"
        )

    by_artist: dict = {}
    for row in rows:
        by_artist.setdefault(row["artist"], []).append(row)

    blocks = []
    for name, albums in by_artist.items():
        listed = "\n".join(
            f"- {a['album']}"
            + (f" ({a['units_sold']} sold)" if a["units_sold"] else " (in catalog)")
            for a in albums
        )
        blocks.append(f"{name}:\n{listed}")

    return "Albums we carry:\n\n" + "\n\n".join(blocks)


def top_selling_albums(genre: str = "", db_path: Optional[Path] = None) -> str:
    """Return the best-selling albums overall, or within a genre if one is given.

    Ranked by total units sold (``InvoiceLine.Quantity``).  Genre is
    fuzzy-matched, so "punk" resolves to "Alternative & Punk".
    """
    genre = (genre or "").strip()

    where = ""
    params: tuple = ()
    if genre:
        where = "WHERE lower(g.Name) LIKE ?"
        params = (f"%{genre.lower()}%",)

    rows = _retrieve(
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
        db_path,
    )

    scope = f" in '{genre}'" if genre else ""
    if not rows:
        return f"I couldn't find sales data for albums{scope}."

    listed = "\n".join(
        f"- {row['album']} — {row['artist']} ({row['units_sold']} sold)" for row in rows
    )
    return f"Best-selling albums{scope}:\n{listed}"
