-- Set-based replacement for the Fact sales data flow in Load_FactSales:
-- two Lookups, a Derived Column for unknown members and net amount, and a
-- Conditional Split that sends non-positive quantities to etl.sales_rejected.
--
-- The joins compare codes under the database's case-insensitive collation,
-- and SQL Server's = ignores trailing spaces. The SSIS Lookups (full cache)
-- did neither, so order lines with 'c00042' or 'C00042 ' that used to land on
-- -1 now find their customer. Reconciliation reports these as an accepted
-- difference (docs/adr/0004, reconciliation/accepted.yml).
--
-- Rerunnable (docs/adr/0003): lines already loaded are replaced by order and
-- line number, staging is reduced to the latest row per line, and the order
-- watermark moves forward in the same transaction, so staged lines are never
-- skipped when this procedure fails. The SSIS package appended, and
-- Stage_Orders moved the watermark before this load ran.

CREATE OR ALTER PROCEDURE etl.usp_load_fact_sales
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SELECT
        ol.order_id
        , ol.line_number
        , ol.customer_code
        , ISNULL(c.customer_key, -1) AS customer_key
        , ISNULL(p.product_key, -1) AS product_key
        , ol.order_date
        , ol.quantity
        , ol.unit_price
        , CAST(ol.quantity * ol.unit_price AS DECIMAL(18, 2)) AS net_amount
    INTO #line
    FROM (
        SELECT
            s.*
            , ROW_NUMBER() OVER (
                PARTITION BY s.order_id, s.line_number ORDER BY s.modified_at DESC
            ) AS rn
        FROM stg.order_line AS s
    ) AS ol
    LEFT JOIN dw.dim_customer AS c
        ON c.customer_code = ol.customer_code
       AND c.is_current = 1
    LEFT JOIN dw.dim_product AS p
        ON p.product_code = ol.product_code
    WHERE ol.rn = 1;

    BEGIN TRANSACTION;

    DELETE f
    FROM dw.fact_sales AS f
    INNER JOIN #line AS l
        ON l.order_id = f.order_id
       AND l.line_number = f.line_number;

    DELETE r
    FROM etl.sales_rejected AS r
    INNER JOIN #line AS l
        ON l.order_id = r.order_id
       AND l.line_number = r.line_number;

    INSERT INTO etl.sales_rejected
    (
        order_id, line_number, customer_code, customer_key, product_key
        , order_date, quantity, unit_price, net_amount, rejected_at
    )
    SELECT
        order_id, line_number, customer_code, customer_key, product_key
        , order_date, quantity, unit_price, net_amount, SYSUTCDATETIME()
    FROM #line
    WHERE quantity <= 0;

    INSERT INTO dw.fact_sales
    (
        order_id, line_number, customer_code, customer_key, product_key
        , order_date, quantity, unit_price, net_amount
    )
    SELECT
        order_id, line_number, customer_code, customer_key, product_key
        , order_date, quantity, unit_price, net_amount
    FROM #line
    WHERE quantity > 0;

    -- Was the "Set watermark" task in Stage_Orders.
    UPDATE etl.watermark
    SET watermark_at = (SELECT MAX(modified_at) FROM stg.order_line)
    WHERE table_name = 'order_line'
      AND EXISTS (SELECT 1 FROM stg.order_line);

    COMMIT TRANSACTION;
END;
GO
