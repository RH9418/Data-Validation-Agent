WITH BaseData AS (
    SELECT fd.*
    FROM "stage_da2_dataset1".fact_data fd
    LEFT JOIN "stage_da2_dataset1".fact_override fo
        ON fd.time_id = fo.time_id AND fd.product_id = fo.product_id AND fd.location_id = fo.location_id
),
JoinedData AS (
    SELECT bd.*,
           md.month_name
    FROM BaseData bd
    LEFT JOIN "stage_da2_dataset1".time_dim_xref tdx
        ON bd.time_id = tdx.time_id
    LEFT JOIN "stage_da2_dataset1".month_dim_desc md
        ON tdx.month_id = md.month_id
    WHERE tdx.latest52_next52_id IN (1)
),
AggregatedBase AS (
    SELECT
        jd.month_name,
        SUM(CASE WHEN jd.editable = 0 THEN jd.actual_sales_dollars ELSE COALESCE(fo.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(fo.user_base_dollars_override, jd.sys_base_dollars_fcst, 0) END) AS actual_sales_and_roy_fcst_dollars,
        SUM(COALESCE(fo.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(fo.user_base_dollars_override, jd.sys_base_dollars_fcst, 0)) AS user_sales_dollars_fcst,
        SUM(COALESCE(fo.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0)) AS gd_user_sales_dollars_forecast,
        SUM(COALESCE(fo.user_base_dollars_override, jd.sys_base_dollars_fcst, 0)) AS ngd_user_sales_dollars_forecast,
        SUM(fo.user_incr_dollars_override) AS sum_user_incr_dollars_override,
        SUM(fo.user_base_dollars_override) AS sum_user_base_dollars_override,
        SUM(jd.actual_sales_dollars) AS sum_actual_sales_dollars,
        SUM(CASE WHEN jd.sys_incr_dollars_fcst IS NULL THEN 0 ELSE jd.sys_incr_dollars_fcst END + CASE WHEN jd.sys_base_dollars_fcst IS NULL THEN 0 ELSE jd.sys_base_dollars_fcst END) AS stat_sales_dollars_fcst,
        SUM(jd.sys_incr_dollars_fcst) AS sum_sys_incr_dollars_fcst,
        SUM(jd.sys_base_dollars_fcst) AS sum_sys_base_dollars_fcst,
        SUM(jd.actual_sales_dollars_ly) AS sum_actual_sales_dollars_ly,
        SUM(jd.actual_sales_dollars_lly) AS sum_actual_sales_dollars_lly,
        COUNT(*) AS row_count,
        SUM(jd.editable) AS sum_editable
    FROM JoinedData jd
    LEFT JOIN "stage_da2_dataset1".fact_override fo
        ON jd.time_id = fo.time_id AND jd.product_id = fo.product_id AND jd.location_id = fo.location_id
    GROUP BY jd.month_name
),
FinalSelect AS (
    SELECT
        ab.month_name,
        ab.actual_sales_and_roy_fcst_dollars,
        ab.user_sales_dollars_fcst,
        ab.gd_user_sales_dollars_forecast,
        ab.ngd_user_sales_dollars_forecast,
        ab.sum_user_incr_dollars_override,
        ab.sum_user_base_dollars_override,
        ab.sum_actual_sales_dollars,
        ab.stat_sales_dollars_fcst,
        ab.sum_sys_incr_dollars_fcst,
        ab.sum_sys_base_dollars_fcst,
        ab.sum_actual_sales_dollars_ly,
        ab.sum_actual_sales_dollars_lly,
        ((ab.actual_sales_and_roy_fcst_dollars - ab.sum_actual_sales_dollars_ly) / NULLIF(ab.sum_actual_sales_dollars_ly, 0)) * 100 AS yoy_sales_pct_change,
        ((ab.actual_sales_and_roy_fcst_dollars - ab.sum_actual_sales_dollars_lly) / NULLIF(ab.sum_actual_sales_dollars_lly, 0)) * 100 AS yoy_sales_pct_change_lly,
        ab.row_count,
        CAST((ab.sum_editable / NULLIF(ab.row_count, 0)) AS DECIMAL(10, 8))::int AS is_editable
    FROM AggregatedBase ab
)
SELECT *
FROM FinalSelect
LIMIT 100;