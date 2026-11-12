-- DuckDB copy of etl.usp_load_fact_sales, for the reconciliation example.
-- Codes compared as UPPER(RTRIM(...)) to behave like SQL Server's
-- case-insensitive collation (see load_dim_customer.sql).

CREATE OR REPLACE TABLE fact_sales AS
SELECT
      ol.order_id
    , ol.line_number
    , ol.customer_code
    , COALESCE(c.customer_key, -1) AS customer_key
    , COALESCE(p.product_key, -1) AS product_key
    , ol.order_date
    , ol.quantity
    , ol.unit_price
    , ROUND(ol.quantity * ol.unit_price, 2) AS net_amount
FROM stg_order_line AS ol
LEFT JOIN dim_customer AS c
    ON UPPER(RTRIM(c.customer_code)) = UPPER(RTRIM(ol.customer_code))
   AND c.is_current = 1
LEFT JOIN dim_product AS p
    ON UPPER(RTRIM(p.product_code)) = UPPER(RTRIM(ol.product_code))
WHERE ol.quantity > 0;

CREATE OR REPLACE TABLE sales_rejected AS
SELECT
      ol.order_id
    , ol.line_number
    , ol.customer_code
    , COALESCE(c.customer_key, -1) AS customer_key
    , COALESCE(p.product_key, -1) AS product_key
    , ol.order_date
    , ol.quantity
    , ol.unit_price
    , ROUND(ol.quantity * ol.unit_price, 2) AS net_amount
FROM stg_order_line AS ol
LEFT JOIN dim_customer AS c
    ON UPPER(RTRIM(c.customer_code)) = UPPER(RTRIM(ol.customer_code))
   AND c.is_current = 1
LEFT JOIN dim_product AS p
    ON UPPER(RTRIM(p.product_code)) = UPPER(RTRIM(ol.product_code))
WHERE ol.quantity <= 0;
