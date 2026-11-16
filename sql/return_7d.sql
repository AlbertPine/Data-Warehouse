INSERT INTO {db}.dws_merchant_return_7d
SELECT merchant_key, anchor AS day_seq, count() AS eligible_users,
       countIf(next_day > anchor AND next_day <= anchor+7) AS returned_users
FROM (
    SELECT merchant_key, days[i] AS anchor, days[i+1] AS next_day
    FROM (
        SELECT merchant_key, user_key, arraySort(groupArray(day_seq)) AS days
        FROM {db}.dws_merchant_user_day
        WHERE cityHash64(merchant_key,user_key)%{buckets}={bucket}
        GROUP BY merchant_key,user_key
    ) ARRAY JOIN arrayEnumerate(days) AS i
)
WHERE anchor <= {last_day}-7
GROUP BY merchant_key, anchor
