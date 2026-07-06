# Chinook Music Store Database Context

## Overview
This is the Chinook sample database — a relational database representing a digital music store. It contains information about artists, albums, tracks, customers, invoices, and genres.

## Schema

### Tables

| Table | Description | Key Columns |
|-------|-------------|-------------|
| `Artist` | Music artists/bands | `ArtistId`, `Name` |
| `Album` | Albums belonging to artists | `AlbumId`, `Title`, `ArtistId` (FK) |
| `Track` | Individual tracks within albums | `TrackId`, `Name`, `AlbumId` (FK), `GenreId` (FK), `MediaTypeId` (FK), `Milliseconds` |
| `Genre` | Music genre categories | `GenreId`, `Name` |
| `MediaTypeId` | Media format (MPEG, AAC, etc.) | `MediaTypeId`, `Name` |
| `Customer` | Store customers | `CustomerId`, `FirstName`, `LastName`, `Company`, `City`, `Country`, `Phone`, `Email` |
| `Invoice` | Customer invoices | `InvoiceId`, `CustomerId` (FK), `InvoiceDate`, `BillingAddress`, `Total` |
| `InvoiceLine` | Line items on invoices | `InvoiceLineId`, `InvoiceId` (FK), `TrackId` (FK), `Quantity`, `UnitPrice` |

### Relationships
- `Artist` 1→N `Album`
- `Album` 1→N `Track`
- `Track` N→1 `Genre`
- `Customer` 1→N `Invoice`
- `Invoice` 1→N `InvoiceLine`
- `InvoiceLine` N→1 `Track`

## Available Data Insights

### Genres
The catalog contains multiple genres including Rock, Jazz, Metal, Pop, R&B/Soul, Blues, Classical, etc. Rock is the most represented genre.

### Customers
The database includes 59 customers from various countries (USA, Canada, India, Brazil, Germany, etc.). Notable customers include Luis Gonçalves, Fernando, Pedro, Julia, and others.

### Customer Identification
For customer lookup, **email address is the preferred identifier** because it is guaranteed unique in the Customer table. Name-based lookups are supported as a fallback but may return multiple matches (e.g., "John Gordon" appears twice).

The Customer table fields are:
- `CustomerId` — Unique internal ID (not user-facing)
- `FirstName`, `LastName` — Full name (may have collisions)
- `Company` — Company name (nullable, not unique)
- `City`, `Country` — Location (not unique)
- `Phone` — Phone number (unique but hard to remember)
- `Email` — **Email address (unique, preferred identifier)**

### Inventory
- ~3500 tracks
- ~350 albums
- ~275 artists

### How to Query
- To find a customer's purchase history: JOIN `Customer` → `Invoice` → `InvoiceLine` → `Track`
- To find what genres a customer likes: GROUP BY genre, COUNT tracks from their invoices
- To search artists: `SELECT Name FROM Artist WHERE Name LIKE '%keyword%'`
- To get genre popularity: `SELECT g.Name, COUNT(*) FROM Track t JOIN Genre g ON t.GenreId = g.GenreId GROUP BY g.GenreId ORDER BY COUNT(*) DESC`
