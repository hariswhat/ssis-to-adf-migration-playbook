-- DuckDB copy of etl.usp_load_dim_customer, for the reconciliation example.
-- Same statements in the same order (tests/test_reconcile.py checks it); what
-- differs is how comparisons are written, because DuckDB compares strings
-- case-sensitively and does not ignore trailing spaces:
--   * customer_code, compared under SQL Server's case-insensitive collation,
--     is compared as UPPER(RTRIM(...)) here;
--   * attributes, compared with a binary collation (case-sensitive, trailing
--     spaces still ignored by =) and NULL-safe, are compared as
--     RTRIM(...) IS DISTINCT FROM RTRIM(...).
-- The lock and the transaction are left out.

UPDATE dim_customer AS d
SET customer_name = s.customer_name
FROM stg_customer AS s
WHERE UPPER(RTRIM(s.customer_code)) = UPPER(RTRIM(d.customer_code))
  AND RTRIM(s.customer_name) IS DISTINCT FROM RTRIM(d.customer_name);

UPDATE dim_customer AS d
SET valid_to = $load_ts, is_current = 0
FROM stg_customer AS s
WHERE UPPER(RTRIM(s.customer_code)) = UPPER(RTRIM(d.customer_code))
  AND d.is_current = 1
  AND (RTRIM(s.region) IS DISTINCT FROM RTRIM(d.region) OR RTRIM(s.tier) IS DISTINCT FROM RTRIM(d.tier));

INSERT INTO dim_customer
SELECT
      (SELECT MAX(customer_key) FROM dim_customer) + ROW_NUMBER() OVER (ORDER BY s.customer_code)
    , s.customer_code, s.customer_name, s.region, s.tier
    , $load_ts, TIMESTAMP '9999-12-31', 1
FROM stg_customer AS s
WHERE NOT EXISTS (
    SELECT 1 FROM dim_customer AS d
    WHERE UPPER(RTRIM(d.customer_code)) = UPPER(RTRIM(s.customer_code))
      AND d.is_current = 1
);
