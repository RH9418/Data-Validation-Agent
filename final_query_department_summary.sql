WITH BaseData AS (
    SELECT fd.*,
           fo.user_incr_dollars_override,
           fo.user_base_dollars_override
    FROM "stage_da2_dataset1".fact_data fd
    LEFT JOIN "stage_da2_dataset1".fact_override fo
    ON fd.time_id = fo.time_id AND fd.product_id = fo.product_id AND fd.location_id = fo.location_id
),
JoinedData AS (
    SELECT bd.*,
           pd.product3_id,
           md.month_id
    FROM BaseData bd
    LEFT JOIN "stage_da2_dataset1".product_dim_xref pdx
    ON bd.product_id = pdx.product_id
    LEFT JOIN "stage_da2_dataset1".product3_dim_desc pd
    ON pdx.product3_id = pd.product3_id
    LEFT JOIN "stage_da2_dataset1".month_dim_desc md
    ON bd.time_id = md.month_id
),
AggregatedBase AS (
    SELECT
        jd.product3_id,
        jd.month_id,
        SUM(CASE WHEN jd.editable = 0 THEN jd.actual_sales_dollars ELSE COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0) END) AS actual_sales_and_roy_fcst_dollars,
        SUM(COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0)) AS user_sales_dollars_fcst,
        SUM(COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0)) AS gd_user_sales_dollars_forecast,
        SUM(COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0)) AS ngd_user_sales_dollars_forecast,
        SUM(jd.user_incr_dollars_override) AS sum_user_incr_dollars_override,
        SUM(jd.user_base_dollars_override) AS sum_user_base_dollars_override,
        CASE WHEN SUM(CASE WHEN jd.editable = 0 THEN jd.actual_sales_dollars ELSE COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0) END) != 0 THEN
            SUM(CASE WHEN jd.editable = 0 THEN jd.actual_sales_dollars ELSE COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0) END) / (SUM(SUM(CASE WHEN jd.editable = 0 THEN jd.actual_sales_dollars ELSE COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0) END)) OVER (PARTITION BY jd.month_id)) * 100
        ELSE 0.0 END AS dept_penetration_ty,
        CASE WHEN SUM(jd.actual_sales_dollars_ly) != 0 THEN
            SUM(jd.actual_sales_dollars_ly) / (SUM(SUM(jd.actual_sales_dollars_ly)) OVER (PARTITION BY jd.month_id)) * 100
        ELSE 0.0 END AS dept_penetration_ly,
        CASE WHEN SUM(jd.actual_sales_dollars_lly) != 0 THEN
            SUM(jd.actual_sales_dollars_lly) / (SUM(SUM(jd.actual_sales_dollars_lly)) OVER (PARTITION BY jd.month_id)) * 100
        ELSE 0.0 END AS dept_penetration_lly,
        CAST((SUM(jd.editable) / NULLIF(COUNT(*), 0)) AS DECIMAL(10, 8))::int AS is_editable,
        COUNT(*) AS row_count,
        ((DENSE_RANK() OVER (ORDER BY jd.product3_id ASC)) + (DENSE_RANK() OVER (ORDER BY jd.product3_id DESC)) - 1) AS product3_pagination_count
    FROM JoinedData jd
    GROUP BY jd.product3_id, jd.month_id
),
FinalSelect AS (
    SELECT
        ab.product3_id,
        ab.month_id,
        ab.actual_sales_and_roy_fcst_dollars,
        ab.user_sales_dollars_fcst,
        ab.gd_user_sales_dollars_forecast,
        ab.ngd_user_sales_dollars_forecast,
        ab.sum_user_incr_dollars_override,
        ab.sum_user_base_dollars_override,
        ab.dept_penetration_ty,
        ab.dept_penetration_ly,
        ab.dept_penetration_lly,
        ab.is_editable,
        ab.row_count,
        ab.product3_pagination_count
    FROM AggregatedBase ab
    LIMIT 100
)
SELECT * FROM FinalSelect;
