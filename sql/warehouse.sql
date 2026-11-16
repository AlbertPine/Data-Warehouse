CREATE TABLE {db}.ods_behavior (
    source_row UInt64, user_id String, item_id String, cat_id String,
    seller_id String, brand_id String, time_stamp String, action_type String
) ENGINE=MergeTree ORDER BY source_row;

CREATE TABLE {db}.ods_user (
    source_row UInt64, user_id String, age_range String, gender String
) ENGINE=MergeTree ORDER BY source_row;

CREATE TABLE {db}.ods_label (
    source_row UInt64, user_id String, merchant_id String, label String
) ENGINE=MergeTree ORDER BY source_row;

CREATE VIEW {db}.dim_user AS
SELECT toUInt32(user_id) AS user_key,
       if(age_range IN ('0',''), '未知', age_range) AS age_group,
       multiIf(gender='0','女性',gender='1','男性','未知') AS gender_group
FROM {db}.ods_user;

CREATE VIEW {db}.dim_merchant AS
SELECT DISTINCT toUInt32(seller_id) AS merchant_key FROM {db}.ods_behavior
UNION DISTINCT SELECT toUInt32(merchant_id) AS merchant_key FROM {db}.ods_label;

CREATE VIEW {db}.dim_day AS
SELECT toUInt16(number+1) AS day_seq,
       formatDateTime(addDays(toDate('2000-05-11'), number), '%m%d') AS mmdd
FROM numbers(185);

CREATE VIEW {db}.dim_action AS
SELECT toUInt8(tupleElement(action,1)) AS action_key, tupleElement(action,2) AS action_name
FROM (SELECT arrayJoin([(0,'点击'),(1,'加购'),(2,'购买'),(3,'收藏')]) AS action);

CREATE TABLE {db}.dwd_behavior (
    source_row UInt64, merchant_key UInt32, user_key UInt32, item_key UInt32,
    category_key UInt32, brand_key UInt32, mmdd FixedString(4), day_seq UInt16,
    action_key UInt8
) ENGINE=MergeTree PARTITION BY substring(mmdd,1,2)
ORDER BY (merchant_key,day_seq,user_key,source_row);

CREATE VIEW {db}.dwd_behavior_quarantine AS
SELECT o.*, '日期超出分析范围或动作码无效' AS reason
FROM {db}.ods_behavior o
LEFT JOIN {db}.dim_day d ON o.time_stamp=d.mmdd
WHERE d.day_seq=0 OR o.action_type NOT IN ('0','1','2','3');

INSERT INTO {db}.dwd_behavior
SELECT o.source_row,toUInt32(o.seller_id),toUInt32(o.user_id),toUInt32(o.item_id),
       toUInt32(o.cat_id),toUInt32OrZero(o.brand_id),o.time_stamp,d.day_seq,toUInt8(o.action_type)
FROM {db}.ods_behavior o
INNER JOIN {db}.dim_day d ON o.time_stamp=d.mmdd
WHERE o.action_type IN ('0','1','2','3');

CREATE VIEW {db}.dim_item AS SELECT DISTINCT item_key FROM {db}.dwd_behavior;
CREATE VIEW {db}.dim_category AS SELECT DISTINCT category_key FROM {db}.dwd_behavior;
CREATE VIEW {db}.dim_brand AS SELECT DISTINCT brand_key FROM {db}.dwd_behavior;

CREATE VIEW {db}.dwd_repeat_label AS
SELECT source_row,toUInt32(user_id) AS user_key,toUInt32(merchant_id) AS merchant_key,
       toUInt8(label) AS repeated
FROM {db}.ods_label WHERE label IN ('0','1');

CREATE TABLE {db}.dws_merchant_user_day ENGINE=MergeTree
ORDER BY (merchant_key,user_key,day_seq) AS
SELECT merchant_key,user_key,day_seq,max(action_key=2) AS purchased,count() AS event_count
FROM {db}.dwd_behavior GROUP BY merchant_key,user_key,day_seq;

CREATE TABLE {db}.dws_category_brand_day ENGINE=MergeTree
ORDER BY (day_seq,category_key,brand_key) AS
SELECT day_seq,category_key,brand_key,count() AS event_count,countIf(action_key=2) AS purchase_event_count
FROM {db}.dwd_behavior GROUP BY day_seq,category_key,brand_key;

CREATE TABLE {db}.dws_merchant_repeat ENGINE=MergeTree ORDER BY merchant_key AS
SELECT merchant_key,count() AS labeled_pairs,sum(repeated) AS positive_pairs
FROM {db}.dwd_repeat_label GROUP BY merchant_key;

CREATE VIEW {db}.ads_merchant_day AS
SELECT a.merchant_key, d.mmdd, a.day_seq, count() AS active_users,
       sum(purchased) AS purchase_users, purchase_users/active_users AS purchase_user_share,
       sum(event_count) AS event_count
FROM {db}.dws_merchant_user_day a INNER JOIN {db}.dim_day d ON a.day_seq=d.day_seq
GROUP BY a.merchant_key,d.mmdd,a.day_seq;

CREATE VIEW {db}.ads_merchant_period AS
SELECT merchant_key,uniqExact(user_key) AS active_users,uniqExactIf(user_key,purchased=1) AS purchase_users,
       purchase_users/active_users AS purchase_user_share, sum(event_count) AS event_count
FROM {db}.dws_merchant_user_day GROUP BY merchant_key;

CREATE VIEW {db}.ads_repeat_sample AS
SELECT merchant_key,labeled_pairs,positive_pairs,positive_pairs/labeled_pairs AS repeat_share
FROM {db}.dws_merchant_repeat;

CREATE VIEW {db}.ads_category_brand_day AS
SELECT d.mmdd,k.* FROM {db}.dws_category_brand_day k INNER JOIN {db}.dim_day d ON k.day_seq=d.day_seq;

CREATE VIEW {db}.ads_user_age_day AS
SELECT d.mmdd,u.age_group,u.gender_group,count() AS merchant_user_days,
       uniqExact(a.user_key) AS active_users,uniqExactIf(a.user_key,a.purchased=1) AS purchase_users
FROM {db}.dws_merchant_user_day a
INNER JOIN {db}.dim_user u ON a.user_key=u.user_key
INNER JOIN {db}.dim_day d ON a.day_seq=d.day_seq
GROUP BY d.mmdd,u.age_group,u.gender_group;

CREATE TABLE {db}.dws_merchant_return_7d (
    merchant_key UInt32, day_seq UInt16, eligible_users UInt64, returned_users UInt64
) ENGINE=MergeTree ORDER BY (merchant_key,day_seq);

CREATE VIEW {db}.ads_merchant_return_7d AS
SELECT r.merchant_key,d.mmdd,sum(r.eligible_users) AS eligible_users,sum(r.returned_users) AS returned_users,
       returned_users/eligible_users AS return_share
FROM {db}.dws_merchant_return_7d r INNER JOIN {db}.dim_day d ON r.day_seq=d.day_seq GROUP BY r.merchant_key,d.mmdd

