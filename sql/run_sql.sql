/*
    dbo.run_sql: run SQL written in another dialect against SQL Server 2025.

        EXEC dbo.run_sql @dialect = N'postgres',
                         @sql     = N'SELECT name FROM users WHERE name ILIKE ''a%'' LIMIT 10';

    The SQL is sent to the sql2tsql translation service with
    sp_invoke_external_rest_endpoint, and each translated T-SQL statement is run
    with sp_executesql in the caller's security context, so it can do nothing the
    caller couldn't do by running the T-SQL directly.

    Pass @print_only = 1 to see the translation without running it.

    One-time setup (as a sysadmin / db_owner). Replace the URL and key:

        EXEC sp_configure 'external rest endpoint enabled', 1;
        RECONFIGURE WITH OVERRIDE;

        -- Needed once per database before creating a database scoped credential.
        -- CREATE MASTER KEY ENCRYPTION BY PASSWORD = '<strong password>';

        CREATE DATABASE SCOPED CREDENTIAL [https://sql2tsql.example.com]
            WITH IDENTITY = 'HTTPEndpointHeaders',
                 SECRET   = '{"X-Api-Key":"<same value as SQL2TSQL_API_KEY>"}';

        GRANT EXECUTE ANY EXTERNAL ENDPOINT TO [<user or role>];
        GRANT REFERENCES ON DATABASE SCOPED CREDENTIAL::[https://sql2tsql.example.com] TO [<user or role>];
        GRANT EXECUTE ON dbo.run_sql TO [<user or role>];
*/
CREATE OR ALTER PROCEDURE dbo.run_sql
    @dialect    sysname,
    @sql        nvarchar(max),
    @strict     bit = 1,
    @print_only bit = 0
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @url nvarchar(4000) = N'https://sql2tsql.example.com/translate';

    DECLARE @payload nvarchar(max) = (
        SELECT @sql AS [sql], @dialect AS [dialect], CAST(@strict AS bit) AS [strict]
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
    );

    DECLARE @response nvarchar(max), @rc int;

    EXEC @rc = sys.sp_invoke_external_rest_endpoint
        @url        = @url,
        @method     = 'POST',
        @payload    = @payload,
        @credential = [https://sql2tsql.example.com],
        @timeout    = 30,
        @response   = @response OUTPUT;

    IF @rc <> 0
    BEGIN
        DECLARE @error nvarchar(2048) = COALESCE(
            JSON_VALUE(@response, '$.result.error'),
            JSON_VALUE(@response, '$.result.detail'),
            CONCAT(N'translation service returned HTTP ', @rc));
        THROW 50001, @error, 1;
    END;

    DECLARE @statements TABLE (ordinal int PRIMARY KEY, stmt nvarchar(max) NOT NULL);

    INSERT @statements (ordinal, stmt)
    SELECT CAST([key] AS int), [value]
    FROM OPENJSON(@response, '$.result.statements');

    IF @print_only = 1
    BEGIN
        SELECT s.ordinal, s.stmt AS translated_tsql,
               (SELECT STRING_AGG(w.[value], N'; ') FROM OPENJSON(@response, '$.result.warnings') AS w) AS warnings
        FROM @statements AS s
        ORDER BY s.ordinal;
        RETURN;
    END;

    -- Run statements one at a time so batch-leading DDL (CREATE VIEW, ...) works.
    DECLARE @i int = 0, @n int = (SELECT COUNT(*) FROM @statements), @stmt nvarchar(max);
    WHILE @i < @n
    BEGIN
        SELECT @stmt = stmt FROM @statements WHERE ordinal = @i;
        EXEC sys.sp_executesql @stmt;
        SET @i += 1;
    END;
END;
GO
