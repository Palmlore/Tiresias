-- Discovery (source scout): registrants whose name starts with one of the brand words,
-- newest CIK first. {{words}} is a validated list of quoted lower-case words.
SELECT cik, name, ticker, exchange
FROM tiresias.sec_tickers
WHERE lower(split(name, ' ')[0]) IN ({{words}})
ORDER BY cik DESC
LIMIT {{limit}}
