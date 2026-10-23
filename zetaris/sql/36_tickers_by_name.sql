-- Discovery (asked entity given by name): case-insensitive substring match on the name.
-- {{pattern}} is a validated, quoted LIKE pattern such as '%coca cola%'.
SELECT cik, name, ticker, exchange
FROM tiresias.sec_tickers
WHERE lower(name) LIKE {{pattern}}
ORDER BY cik DESC
LIMIT {{limit}}
