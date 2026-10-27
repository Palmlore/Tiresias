-- The model: three tables over the SEC sources Tiresias already registers as REST tables
-- (zetaris/sql/10_companyfacts_table.sql, 11_submissions_table.sql). Sent as ONE statement:
-- a semicolon-splitting client breaks a multi-table COMPILE.
-- Each FOREIGN KEY makes Zetaris generate a data-quality rule on the referencing column.
COMPILE USL IF NOT EXISTS sec_evidence DEPLOY NAMESPACE lightning.metastore.tiresias_usl DDL
CREATE TABLE company (
    cik bigint NOT NULL PRIMARY KEY,
    entity_name varchar(200) NOT NULL,
    sic varchar(4),
    sic_description varchar(200),
    fiscal_year_end varchar(4)
);

CREATE TABLE filing (
    accession_number varchar(20) NOT NULL PRIMARY KEY,
    cik bigint NOT NULL FOREIGN KEY REFERENCES company(cik),
    form varchar(20),
    filing_date date,
    report_date date,
    items varchar(100)
);

CREATE TABLE fact (
    cik bigint NOT NULL FOREIGN KEY REFERENCES company(cik),
    concept varchar(200) NOT NULL,
    unit varchar(10),
    val double NOT NULL,
    period_start date,
    period_end date NOT NULL,
    fy int,
    fp varchar(4),
    form varchar(20),
    filed date,
    accn varchar(20) NOT NULL FOREIGN KEY REFERENCES filing(accession_number),
    frame varchar(20)
);
