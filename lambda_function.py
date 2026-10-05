"""Validate small order CSV files uploaded to S3 and publish three outputs."""

import csv
import io
import json
import re
from collections import defaultdict
from datetime import date
from urllib.parse import unquote_plus


REQUIRED_COLUMNS = {"order_id", "order_date", "city", "amount_cents"}
MAX_INPUT_BYTES = 1_000_000


def transform_csv(csv_text):
    """Return clean rows, rejected rows, and sales grouped by date and city."""
    reader = csv.DictReader(io.StringIO(csv_text))
    columns = set(reader.fieldnames or [])
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        return [], [{"row_number": 1, "reason": f"missing columns: {', '.join(missing)}"}], []

    clean = []
    rejected = []
    seen_ids = set()
    totals = defaultdict(lambda: {"order_count": 0, "total_amount_cents": 0})

    for row_number, row in enumerate(reader, start=2):
        try:
            order_id = (row.get("order_id") or "").strip()
            raw_date = (row.get("order_date") or "").strip()
            city = (row.get("city") or "").strip().title()
            raw_amount = (row.get("amount_cents") or "").strip()

            if not order_id:
                raise ValueError("missing order_id")
            if order_id in seen_ids:
                raise ValueError("duplicate order_id")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
                raise ValueError("invalid order_date")
            try:
                order_date = date.fromisoformat(raw_date).isoformat()
            except ValueError as exc:
                raise ValueError("invalid order_date") from exc
            if not city:
                raise ValueError("missing city")
            if not re.fullmatch(r"\d+", raw_amount) or int(raw_amount) == 0:
                raise ValueError("amount_cents must be a positive integer")

            amount_cents = int(raw_amount)
            item = {
                "order_id": order_id,
                "order_date": order_date,
                "city": city,
                "amount_cents": amount_cents,
            }
            clean.append(item)
            seen_ids.add(order_id)
            total = totals[(order_date, city)]
            total["order_count"] += 1
            total["total_amount_cents"] += amount_cents
        except ValueError as exc:
            rejected.append({"row_number": row_number, "reason": str(exc), "record": row})

    summary = [
        {"order_date": day, "city": city, **totals[(day, city)]}
        for day, city in sorted(totals)
    ]
    return clean, rejected, summary


def to_jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def lambda_handler(event, context):
    # Boto3 is included in the AWS Lambda Python runtime. Importing it here
    # keeps the transformation function testable without AWS credentials.
    import boto3

    s3 = boto3.client("s3")
    processed = 0

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        if not key.startswith("input/") or not key.endswith(".csv"):
            continue

        obj = s3.get_object(Bucket=bucket, Key=key)
        if obj["ContentLength"] > MAX_INPUT_BYTES:
            print(f"Skipped {key}: file exceeds {MAX_INPUT_BYTES} bytes")
            continue

        csv_text = obj["Body"].read().decode("utf-8-sig")
        clean, rejected, summary = transform_csv(csv_text)
        name = key[len("input/") : -len(".csv")]
        outputs = {
            f"output/clean/{name}.jsonl": (to_jsonl(clean), "application/x-ndjson"),
            f"output/rejected/{name}.jsonl": (to_jsonl(rejected), "application/x-ndjson"),
            f"output/summary/{name}.json": (
                json.dumps(
                    {
                        "source": key,
                        "valid_count": len(clean),
                        "rejected_count": len(rejected),
                        "by_date_and_city": summary,
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                "application/json",
            ),
        }
        for output_key, (content, content_type) in outputs.items():
            s3.put_object(
                Bucket=bucket,
                Key=output_key,
                Body=content.encode("utf-8"),
                ContentType=content_type,
            )

        print(f"source={key} valid={len(clean)} rejected={len(rejected)}")
        processed += 1

    return {"processed_files": processed}
