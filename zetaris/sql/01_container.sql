-- SchemaStore container for the flattening views (queried as tiresias.<view>).
-- No IF NOT EXISTS either; "already exists" is reported as status "exists".
CREATE SCHEMASTORE CONTAINER tiresias
