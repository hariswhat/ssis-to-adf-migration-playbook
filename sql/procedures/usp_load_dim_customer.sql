-- Set-based replacement for the Slowly Changing Dimension data flow in
-- Load_DimCustomer. Called by pl_load_dimcustomer after stg.customer is loaded.
--
-- region and tier are type 2 (expire and insert); customer_name is type 1.
-- The comparison of customer_code uses the database collation
-- (SQL_Latin1_General_CP1_CI_AS): case-insensitive and blind to trailing
-- spaces, unlike the SSIS component (docs/adr/0004).

CREATE OR ALTER PROCEDURE etl.usp_load_dim_customer
    @load_ts DATETIME2(0)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;

    -- Type 1: overwrite the name on the current version.
    UPDATE d
    SET d.customer_name = s.customer_name
    FROM dw.dim_customer AS d
    INNER JOIN stg.customer AS s
        ON s.customer_code = d.customer_code
    WHERE d.is_current = 1
      AND d.customer_name <> s.customer_name;

    -- Type 2: expire the current version where region or tier changed.
    UPDATE d
    SET
          d.valid_to = @load_ts
        , d.is_current = 0
    FROM dw.dim_customer AS d
    INNER JOIN stg.customer AS s
        ON s.customer_code = d.customer_code
    WHERE d.is_current = 1
      AND (d.region <> s.region OR d.tier <> s.tier);

    -- New versions: changed customers (no current row any more) and new ones.
    DECLARE @max_key INT = (SELECT ISNULL(MAX(customer_key), 0) FROM dw.dim_customer);

    INSERT INTO dw.dim_customer
    (
          customer_key, customer_code, customer_name, region, tier
        , valid_from, valid_to, is_current
    )
    SELECT
          @max_key + ROW_NUMBER() OVER (ORDER BY s.customer_code)
        , s.customer_code
        , s.customer_name
        , s.region
        , s.tier
        , @load_ts
        , '9999-12-31'
        , 1
    FROM stg.customer AS s
    WHERE NOT EXISTS
    (
        SELECT 1 FROM dw.dim_customer AS d
        WHERE d.customer_code = s.customer_code
          AND d.is_current = 1
    );

    COMMIT TRANSACTION;
END;
GO
