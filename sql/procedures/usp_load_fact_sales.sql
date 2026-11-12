-- Set-based replacement for the Fact sales data flow in Load_FactSales:
-- two Lookups, a Derived Column for unknown members and net amount, and a
-- Conditional Split that sends non-positive quantities to etl.sales_rejected.
--
-- The joins compare codes under the database collation, case-insensitive and
-- blind to trailing spaces. The SSIS Lookups (full cache) did neither, so
-- order lines with 'c00042' or 'C00042 ' that used to land on -1 now find
-- their customer. Reconciliation reports these as an accepted difference
-- (docs/adr/0004, reconciliation/accepted.yml).

CREATE OR ALTER PROCEDURE etl.usp_load_fact_sales
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;

    INSERT INTO etl.sales_rejected
    (
          order_id, line_number, customer_code, customer_key, product_key
        , order_date, quantity, unit_price, net_amount, rejected_at
    )
    SELECT
          ol.order_id
        , ol.line_number
        , ol.customer_code
        , ISNULL(c.customer_key, -1)
        , ISNULL(p.product_key, -1)
        , ol.order_date
        , ol.quantity
        , ol.unit_price
        , CAST(ol.quantity * ol.unit_price AS DECIMAL(18, 2))
        , SYSUTCDATETIME()
    FROM stg.order_line AS ol
    LEFT JOIN dw.dim_customer AS c
        ON c.customer_code = ol.customer_code
       AND c.is_current = 1
    LEFT JOIN dw.dim_product AS p
        ON p.product_code = ol.product_code
    WHERE ol.quantity <= 0;

    INSERT INTO dw.fact_sales
    (
          order_id, line_number, customer_code, customer_key, product_key
        , order_date, quantity, unit_price, net_amount
    )
    SELECT
          ol.order_id
        , ol.line_number
        , ol.customer_code
        , ISNULL(c.customer_key, -1)
        , ISNULL(p.product_key, -1)
        , ol.order_date
        , ol.quantity
        , ol.unit_price
        , CAST(ol.quantity * ol.unit_price AS DECIMAL(18, 2))
    FROM stg.order_line AS ol
    LEFT JOIN dw.dim_customer AS c
        ON c.customer_code = ol.customer_code
       AND c.is_current = 1
    LEFT JOIN dw.dim_product AS p
        ON p.product_code = ol.product_code
    WHERE ol.quantity > 0;

    COMMIT TRANSACTION;
END;
GO
