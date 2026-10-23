-- Lightning database that holds the SEC EDGAR REST tables.
-- Lightning SQL has no IF NOT EXISTS here; bootstrap reports "already exists" as status "exists".
CREATE LIGHTNING DATABASE TIRESIAS_SEC DESCRIBE BY "Tiresias SEC EDGAR REST"
