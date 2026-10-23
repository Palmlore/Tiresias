-- Discovery (catalog id: sec_tickers): one REST table over SEC's ticker/CIK/name/exchange list.
-- The row-array file (company_tickers_exchange.json), not company_tickers.json: the latter is
-- one object keyed "0".."N", which Zetaris infers as a struct with ~10k fields.
-- Registered once by bootstrap (or on first use); never counted in MAX_NEW_SOURCES_PER_RUN.
CREATE LIGHTNING REST TABLE sec_tickers_raw FROM TIRESIAS_SEC REQUEST(
    endpoint "https://www.sec.gov/files/company_tickers_exchange.json",
    method "get", response_type "json", http_encoding "URLENCODED"
) HEADER (user-agent "{{sec_user_agent}}") BODY ()
