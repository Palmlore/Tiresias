-- Discovery view over sec_tickers_raw: one row per (cik, ticker). SEC returns
-- {"fields": ["cik","name","ticker","exchange"], "data": [[...], ...]}, inferred as
-- array<array<string>>, so explode the rows and index the columns.
CREATE OR REPLACE SCHEMASTORE VIEW sec_tickers WITH CONTAINER tiresias AS
SELECT CAST(r[0] AS BIGINT) AS cik, r[1] AS name, r[2] AS ticker, r[3] AS exchange
FROM tiresias_sec.sec_tickers_raw
LATERAL VIEW explode(data) AS r
