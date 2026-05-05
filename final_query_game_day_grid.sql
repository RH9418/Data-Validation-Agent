WITH BaseData AS (
    SELECT fd.*,
           fo.user_incr_dollars_override,
           fo.user_base_dollars_override,
           fo.user_attendance_override,
           fo.planner_tag_override
    FROM "stage_da2_dataset1".fact_data fd
    LEFT JOIN "stage_da2_dataset1".fact_override fo
    ON fd.time_id = fo.time_id AND fd.product_id = fo.product_id AND fd.location_id = fo.location_id
),
JoinedData AS (
    SELECT bd.*,
           season_type_dim_desc.season_type_name,
           game_day_dim_desc.game_day_name,
           product4_dim_desc.product4_name,
           game_num_dim_desc.game_num_name,
           time_dim_desc.time_name,
           result_dim_desc.result_name
    FROM BaseData bd
    LEFT JOIN "stage_da2_dataset1".season_type_dim_xref ON bd.season_type_id = season_type_dim_xref.season_type_id
    LEFT JOIN "stage_da2_dataset1".season_type_dim_desc ON season_type_dim_xref.season_type_id = season_type_dim_desc.season_type_id
    LEFT JOIN "stage_da2_dataset1".game_day_dim_xref ON bd.game_day_id = game_day_dim_xref.game_day_id
    LEFT JOIN "stage_da2_dataset1".game_day_dim_desc ON game_day_dim_xref.game_day_id = game_day_dim_desc.game_day_id
    LEFT JOIN "stage_da2_dataset1".product_dim_xref ON bd.product_id = product_dim_xref.product_id
    LEFT JOIN "stage_da2_dataset1".product4_dim_desc ON product_dim_xref.product4_id = product4_dim_desc.product4_id
    LEFT JOIN "stage_da2_dataset1".game_num_dim_xref ON bd.game_num_id = game_num_dim_xref.game_num_id
    LEFT JOIN "stage_da2_dataset1".game_num_dim_desc ON game_num_dim_xref.game_num_id = game_num_dim_desc.game_num_id
    LEFT JOIN "stage_da2_dataset1".time_dim_xref ON bd.time_id = time_dim_xref.time_id
    LEFT JOIN "stage_da2_dataset1".time_dim_desc ON time_dim_xref.time_id = time_dim_desc.time_id
    LEFT JOIN "stage_da2_dataset1".result_dim_xref ON bd.result_id = result_dim_xref.result_id
    LEFT JOIN "stage_da2_dataset1".result_dim_desc ON result_dim_xref.result_id = result_dim_desc.result_id
),
AggregatedBase AS (
SELECT jd.season_type_name, jd.game_day_name, jd.product4_name, jd.game_num_name, jd.time_name, jd.result_name,
MIN(jd.series_num) AS min_series_num,
TO_CHAR(jd.time_name::DATE, 'Month') AS month_num,
TO_CHAR(jd.time_name::DATE, 'Day') AS day_name,
MIN(jd.opp_team) AS min_opp_team,
SUM((CASE WHEN jd.sys_incr_dollars_fcst IS NULL THEN 0 ELSE jd.sys_incr_dollars_fcst END) + (CASE WHEN jd.sys_base_dollars_fcst IS NULL THEN 0 ELSE jd.sys_base_dollars_fcst END)) AS stat_sales_dollars_fcst,
AVG(jd.sys_attendance_fcst) AS avg_sys_attendance_fcst,
SUM(CASE WHEN jd.user_base_dollars_override IS NULL THEN 0 ELSE jd.user_base_dollars_override END) + SUM(CASE WHEN jd.user_incr_dollars_override IS NULL THEN 0 ELSE jd.user_incr_dollars_override END) AS stat_sales_dollars_override,
SUM(jd.user_incr_dollars_override) AS sum_user_incr_dollars_override,
SUM(jd.user_base_dollars_override) AS sum_user_base_dollars_override,
SUM(COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0)) AS user_sales_dollars_fcst,
AVG(jd.user_attendance_override) AS avg_user_attendance_override,
AVG(COALESCE(jd.user_attendance_override, jd.sys_attendance_fcst)) AS user_attendance_fcst,
SUM(CASE WHEN jd.editable = 0 THEN jd.actual_sales_dollars ELSE COALESCE(jd.user_incr_dollars_override, jd.sys_incr_dollars_fcst, 0) + COALESCE(jd.user_base_dollars_override, jd.sys_base_dollars_fcst, 0) END) AS actual_sales_and_roy_fcst_dollars,
AVG(CASE WHEN jd.editable = 0 THEN jd.attendance ELSE COALESCE(jd.user_attendance_override, jd.sys_attendance_fcst) END) AS actual_attendance_and_roy_fcst,
SUM(jd.actual_sales_dollars) AS sum_actual_sales_dollars,
AVG(jd.attendance) AS avg_attendance,
MIN(jd.date_ly) AS min_date_ly,
TO_CHAR(MIN(jd.date_ly), 'Month') AS month_ly,
TO_CHAR(MIN(jd.date_ly), 'Day') AS dow_ly,
MIN(jd.opp_team_ly) AS min_opp_team_ly,
SUM(jd.actual_sales_dollars_ly) AS sum_actual_sales_dollars_ly,
AVG(jd.attendance_ly) AS avg_attendance_ly,
MIN(jd.date_lly) AS min_date_lly,
TO_CHAR(MIN(jd.date_lly), 'Month') AS month_lly,
TO_CHAR(MIN(jd.date_lly), 'Day') AS dow_lly,
MIN(jd.opp_team_lly) AS min_opp_team_lly,
SUM(jd.actual_sales_dollars_lly) AS sum_actual_sales_dollars_lly,
AVG(jd.attendance_lly) AS avg_attendance_lly,
COUNT(*) AS row_count,
1 AS pt_editable,
SUM(jd.sys_base_dollars_fcst) AS sum_sys_base_dollars_fcst,
SUM(jd.editable) AS sum_editable,
SUM(jd.planner_tag_override) AS sum_planner_tag_override,
SUM(jd.sys_incr_dollars_fcst) AS sum_sys_incr_dollars_fcst
FROM JoinedData jd
GROUP BY jd.season_type_name, jd.game_day_name, jd.product4_name, jd.game_num_name, jd.time_name, jd.result_name
),
FinalSelect AS (
SELECT ab.*,
  (ab.sum_sys_incr_dollars_fcst + ab.sum_sys_base_dollars_fcst) / NULLIF(ab.avg_sys_attendance_fcst, 0) AS initial_per_cap_stat_fcst,
  (COALESCE(ab.sum_user_incr_dollars_override, ab.sum_sys_incr_dollars_fcst) + COALESCE(ab.sum_user_base_dollars_override, ab.sum_sys_base_dollars_fcst)) / NULLIF(COALESCE(ab.avg_user_attendance_override, ab.avg_sys_attendance_fcst), 0) AS per_cap_fcst_dollars_adjusted,
  CASE WHEN ab.actual_attendance_and_roy_fcst > 0 THEN ab.actual_sales_and_roy_fcst_dollars / ab.actual_attendance_and_roy_fcst ELSE 0 END AS actual_and_roy_per_cap,
  ((ab.actual_sales_and_roy_fcst_dollars - ab.sum_actual_sales_dollars_ly) / NULLIF(ab.sum_actual_sales_dollars_ly, 0)) * 100 AS yoy_sales_pct_change,
  ab.sum_actual_sales_dollars_ly / NULLIF(ab.avg_attendance_ly, 0) AS per_cap_ly,
  ab.sum_actual_sales_dollars_lly / NULLIF(ab.avg_attendance_lly, 0) AS per_cap_lly,
  CAST((ab.sum_planner_tag_override / NULLIF(ab.row_count, 0)) AS DECIMAL(10,8)) AS planner_tag,
  CAST((ab.sum_editable / NULLIF(ab.row_count, 0)) AS DECIMAL(10,8))::int AS is_editable
FROM AggregatedBase ab
)
SELECT * FROM FinalSelect LIMIT 100;