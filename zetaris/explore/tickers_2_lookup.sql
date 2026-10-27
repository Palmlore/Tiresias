-- Resolving the case's tickers to CIKs, as the entity resolver does.
SELECT cik, name, ticker, exchange
FROM tiresias.sec_tickers
WHERE upper(ticker) IN ('MMM', 'SOLV')
