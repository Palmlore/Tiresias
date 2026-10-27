-- SEC ticker map (catalog id sec_tickers): listed companies by exchange.
SELECT exchange, COUNT(*) AS tickers
FROM tiresias.sec_tickers
GROUP BY exchange
ORDER BY tickers DESC
