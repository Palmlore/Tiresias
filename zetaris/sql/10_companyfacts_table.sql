-- One REST table per entity over SEC XBRL companyfacts (catalog id: sec_companyfacts).
-- companyfacts, not companyconcept: companyconcept returned empty units for GEHC (AGENTS.md).
-- {{sec_user_agent}} is SEC_USER_AGENT ("Name contact@example.com"), required by SEC fair access.
CREATE LIGHTNING REST TABLE {{ticker}}_facts_raw FROM TIRESIAS_SEC REQUEST(
    endpoint "https://data.sec.gov/api/xbrl/companyfacts/CIK{{cik10}}.json",
    method "get", response_type "json", http_encoding "URLENCODED"
) HEADER (user-agent "{{sec_user_agent}}") BODY ()
