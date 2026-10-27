-- Type-ahead by name: the entity directory's live search.
SELECT cik, name, ticker, exchange
FROM tiresias.sec_tickers
WHERE lower(name) LIKE '%solventum%' OR lower(name) LIKE '3m %'
ORDER BY cik
