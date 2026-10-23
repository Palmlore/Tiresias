-- Discovery (catalog id: sec_fulltext): one REST table per EDGAR full-text search, named after a
-- hash of its endpoint ({{endpoint}} is percent-encoded, built by adapters.catalog). Registered
-- on demand by the source scout; never counted in MAX_NEW_SOURCES_PER_RUN.
-- http_encoding "JSON": the search service answers 415 to the form-encoded request that the
-- other SEC tables use (measured 2026-10-23).
CREATE LIGHTNING REST TABLE {{table}} FROM TIRESIAS_SEC REQUEST(
    endpoint "{{endpoint}}",
    method "get", response_type "json", http_encoding "JSON"
) HEADER (user-agent "{{sec_user_agent}}") BODY ()
