CREATE TABLE IF NOT EXISTS company_research (
    corp_code TEXT PRIMARY KEY REFERENCES companies(corp_code),
    payload TEXT NOT NULL CHECK (json_valid(payload))
);
