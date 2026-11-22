CREATE TABLE IF NOT EXISTS company_research_materials (
    corp_code TEXT PRIMARY KEY REFERENCES companies(corp_code),
    checked_date TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
