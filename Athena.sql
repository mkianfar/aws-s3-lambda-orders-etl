CREATE DATABASE IF NOT EXISTS orders_etl;

CREATE EXTERNAL TABLE IF NOT EXISTS orders_etl.orders_clean (
  order_id string,
  order_date string,
  city string,
  amount_cents bigint
)
ROW FORMAT SERDE 'org.apache.hive.hcatalog.data.JsonSerDe'
STORED AS TEXTFILE
LOCATION 's3://au-orders-demo/output/clean/';


SELECT order_id, order_date, city, amount_cents
FROM orders_etl.orders_clean
LIMIT 10;