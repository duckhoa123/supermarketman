"""Normalize database URLs from hosting providers for SQLAlchemy's asyncpg driver. Pure function."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def to_async_url(url: str) -> str:
    """Railway, Heroku-style and Supabase URLs look like postgres(ql)://...; the app needs postgresql+asyncpg://.

    asyncpg doesn't understand libpq's `sslmode` query parameter, so it becomes asyncpg's `ssl`.
    """
    if not url:
        return url
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
            break
    if not url.startswith("postgresql+asyncpg://"):
        return url
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "sslmode":
            if value in ("disable", "allow", "prefer"):
                continue  # asyncpg's default behaviour is fine
            key = "ssl"
        query.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(query)))
