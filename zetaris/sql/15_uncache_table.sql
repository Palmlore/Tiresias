-- Release the session cache of a raw REST table (BACKLOG B-24). Never drops the table: the
-- registration stays, and the next CACHE TABLE (12) or query refetches the SEC response.
UNCACHE TABLE tiresias_sec.{{table}}
