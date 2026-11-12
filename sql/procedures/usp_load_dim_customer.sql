-- Set-based replacement for the Slowly Changing Dimension data flow in
-- Load_DimCustomer. Called by pl_load_dimcustomer after stg.customer is loaded.
--
-- region and tier are type 2 (expire and insert); customer_name is type 1,
-- applied to every version as the SSIS wizard was set to do.
--
-- Matching: the SCD component looked each business key up in the database,
-- so customer_code is matched under the database collation here too
-- (case-insensitive; SQL Server's = also ignores trailing spaces). Attribute
-- changes were detected by the component in .NET, which is case-sensitive,
-- so attributes are compared with a binary collation, NULL-safe through
-- EXCEPT. A change of trailing spaces alone is still not detected: = ignores
-- them under every collation (docs/adr/0004).
--
-- @load_ts stamps valid_from and valid_to. The pipeline passes the trigger
-- time in Eastern time, the clock SSIS's GETDATE() used on the on-premises
-- server; a manual run gets the current Eastern time.

CREATE OR ALTER PROCEDURE etl.usp_load_dim_customer
    @load_ts DATETIME2(0) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET @load_ts = ISNULL(
        @load_ts,
        CONVERT(DATETIME2(0), SYSDATETIMEOFFSET() AT TIME ZONE 'Eastern Standard Time')
    );

    BEGIN TRANSACTION;

    -- One load at a time: new keys are MAX + ROW_NUMBER. Wait up to ten
    -- minutes for another run, then fail rather than block until the
    -- activity times out.
    DECLARE @lock INT;
    EXEC @lock = sp_getapplock
        @Resource = 'etl.usp_load_dim_customer'
        , @LockMode = 'Exclusive'
        , @LockOwner = 'Transaction'
        , @LockTimeout = 600000;
    IF @lock < 0
        THROW 50001, 'etl.usp_load_dim_customer is already running', 1;

    -- Type 1: overwrite the name on every version of the customer.
    UPDATE d
    SET d.customer_name = s.customer_name
    FROM dw.dim_customer AS d
    INNER JOIN stg.customer AS s
        ON s.customer_code = d.customer_code
    WHERE EXISTS (
        SELECT s.customer_name COLLATE Latin1_General_BIN2  -- noqa: CP02
        EXCEPT
        SELECT d.customer_name COLLATE Latin1_General_BIN2  -- noqa: CP02
    );

    -- Type 2: expire the current version where region or tier changed.
    UPDATE d
    SET
        d.valid_to = @load_ts
        , d.is_current = 0
    FROM dw.dim_customer AS d
    INNER JOIN stg.customer AS s
        ON s.customer_code = d.customer_code
    WHERE d.is_current = 1
      AND EXISTS (
          SELECT s.region COLLATE Latin1_General_BIN2, s.tier COLLATE Latin1_General_BIN2  -- noqa: CP02
          EXCEPT
          SELECT d.region COLLATE Latin1_General_BIN2, d.tier COLLATE Latin1_General_BIN2  -- noqa: CP02
      );

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
