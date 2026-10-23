-- Discovery: registrants by exact ticker or CIK. {{tickers}} is a validated list of quoted
-- upper-case tickers, {{ciks}} a list of integers (either may be the placeholder value NULL).
SELECT cik, name, ticker, exchange
FROM tiresias.sec_tickers
WHERE upper(ticker) IN ({{tickers}}) OR cik IN ({{ciks}})
ORDER BY cik DESC
LIMIT {{limit}}
