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
The database includes customers from various countries (USA, Canada, India, etc.). Notable customers include Luis, Fernando, Pedro, Julia, and others.

### Inventory
- ~3500 tracks
- ~350 albums
- ~275 artists

### How to Query
- To find a customer's purchase history: JOIN `Customer` → `Invoice` → `InvoiceLine` → `Track`
- To find what genres a customer likes: GROUP BY genre, COUNT tracks from their invoices
- To search artists: `SELECT Name FROM Artist WHERE Name LIKE '%keyword%'`
- To get genre popularity: `SELECT g.Name, COUNT(*) FROM Track t JOIN Genre g ON t.GenreId = g.GenreId GROUP BY g.GenreId ORDER BY COUNT(*) DESC`
