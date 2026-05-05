WITH BaseData AS (
    SELECT fd.*,
           fo.user_incr_dollars_override,
           fo.user_base_dollars_override
    FROM "stage_da2_dataset1".fact_data fd
    LEFT JOIN "stage_da2_dataset1".fact_override fo
           ON fd.time_id = fo.time_id
          AND fd.product_id = fo.product_id
          AND fd.location_id = fo.location_id
),
JoinedData AS (
    SELECT bd.*
    FROM BaseData bd
),
AggregatedBase AS (
    SELECT
        SUM((CASE WHEN jd.sys_incr_dollars_fcst IS NULL THEN 0 ELSE jd.sys_incr_dollars_fcst END) +
            (CASE WHEN jd.sys_base_dollars_fcst IS NULL THEN 0 ELSE jd.sys_base_dollars_fcst END)) AS stat_sales_dollars_fcst,
        SUM(COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) +
            COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0)) AS user_sales_dollars_fcst,
        SUM(CASE WHEN jd.editable = 0 THEN jd.actual_sales_dollars ELSE
            COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) +
            COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0) END) AS actual_sales_and_roy_fcst_dollars,
        SUM(jd.actual_sales_dollars) AS sum_actual_sales_dollars,
        SUM(jd.actual_sales_dollars_ly) AS sum_actual_sales_dollars_ly,
        SUM(jd.actual_sales_dollars_lly) AS sum_actual_sales_dollars_lly
    FROM JoinedData jd
),
FinalSelect AS (
    SELECT
        stat_sales_dollars_fcst,
        user_sales_dollars_fcst,
        actual_sales_and_roy_fcst_dollars,
        sum_actual_sales_dollars,
        sum_actual_sales_dollars_ly,
        sum_actual_sales_dollars_lly,
        ((actual_sales_and_roy_fcst_dollars - sum_actual_sales_dollars_ly) / NULLIF(sum_actual_sales_dollars_ly, 0)) * 100 AS yoy_sales_pct_change
    FROM AggregatedBase
)
SELECT *
FROM FinalSelect
LIMIT 100;
