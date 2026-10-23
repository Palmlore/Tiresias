-- One REST table per entity over SEC submissions (catalog id: sec_submissions):
-- company profile and filing history, with 8-K item codes.
CREATE LIGHTNING REST TABLE {{ticker}}_submissions_raw FROM TIRESIAS_SEC REQUEST(
    endpoint "https://data.sec.gov/submissions/CIK{{cik10}}.json",
    method "get", response_type "json", http_encoding "URLENCODED"
) HEADER (user-agent "{{sec_user_agent}}") BODY ()
