-- DuckDB copy of etl.usp_load_dim_customer, for the reconciliation example.
-- Same statements in the same order; the only change is how the comparison of
-- customer_code is written. SQL Server compares it case-insensitively (the
-- database collation) and its = ignores trailing spaces; DuckDB does neither,
-- so the code is compared as UPPER(RTRIM(...)) here.
-- tests/test_reconcile.py checks that both files keep the same statements.

UPDATE dim_customer AS d
SET customer_name = s.customer_name
FROM stg_customer AS s
WHERE UPPER(RTRIM(s.customer_code)) = UPPER(RTRIM(d.customer_code))
  AND d.customer_name <> s.customer_name;

UPDATE dim_customer AS d
SET valid_to = $load_ts, is_current = 0
FROM stg_customer AS s
WHERE UPPER(RTRIM(s.customer_code)) = UPPER(RTRIM(d.customer_code))
  AND d.is_current = 1
  AND (d.region <> s.region OR d.tier <> s.tier);

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
