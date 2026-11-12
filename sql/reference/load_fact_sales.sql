-- DuckDB copy of etl.usp_load_fact_sales, for the reconciliation example.
-- Same statements in the same order (tests/test_reconcile.py checks it).
-- Codes are compared as UPPER(RTRIM(...)) to behave like SQL Server (see
-- load_dim_customer.sql); amounts are DECIMAL(18, 2) as in the procedure.

CREATE OR REPLACE TEMP TABLE line AS
SELECT
      ol.order_id
    , ol.line_number
    , ol.customer_code
    , COALESCE(c.customer_key, -1) AS customer_key
    , COALESCE(p.product_key, -1) AS product_key
    , ol.order_date
    , ol.quantity
    , ol.unit_price
    , CAST(ol.quantity * ol.unit_price AS DECIMAL(18, 2)) AS net_amount
FROM (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.order_id, s.line_number ORDER BY s.modified_at DESC) AS rn
    FROM stg_order_line AS s
) AS ol
LEFT JOIN dim_customer AS c
    ON UPPER(RTRIM(c.customer_code)) = UPPER(RTRIM(ol.customer_code))
   AND c.is_current = 1
LEFT JOIN dim_product AS p
    ON UPPER(RTRIM(p.product_code)) = UPPER(RTRIM(ol.product_code))
WHERE ol.rn = 1;

DELETE FROM fact_sales AS f
USING line AS l
WHERE l.order_id = f.order_id AND l.line_number = f.line_number;

DELETE FROM sales_rejected AS r
USING line AS l
WHERE l.order_id = r.order_id AND l.line_number = r.line_number;

INSERT INTO sales_rejected
SELECT order_id, line_number, customer_code, customer_key, product_key,
       order_date, quantity, unit_price, net_amount
FROM line
WHERE quantity <= 0;

INSERT INTO fact_sales
SELECT order_id, line_number, customer_code, customer_key, product_key,
       order_date, quantity, unit_price, net_amount
FROM line
WHERE quantity > 0;

UPDATE watermark
SET watermark_at = (SELECT MAX(modified_at) FROM stg_order_line)
WHERE table_name = 'order_line'
  AND EXISTS (SELECT 1 FROM stg_order_line);
