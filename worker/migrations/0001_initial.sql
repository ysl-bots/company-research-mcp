CREATE TABLE IF NOT EXISTS companies (
    corp_code TEXT PRIMARY KEY,
    corp_name TEXT NOT NULL,
    corp_eng_name TEXT NOT NULL,
    stock_code TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS companies_stock_code ON companies (stock_code);

CREATE TABLE IF NOT EXISTS profiles (
    corp_code TEXT NOT NULL REFERENCES companies(corp_code),
    fiscal_year INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (corp_code, fiscal_year)
);
