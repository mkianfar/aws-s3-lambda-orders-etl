import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lambda_function import lambda_handler, transform_csv


SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "orders.csv"


class PipelineTests(unittest.TestCase):
    def test_data_quality_and_aggregation(self):
        clean, rejected, summary = transform_csv(SAMPLE.read_text(encoding="utf-8"))

        self.assertEqual([row["order_id"] for row in clean], ["1", "2", "4"])
        self.assertEqual(clean[1]["city"], "Melbourne")
        self.assertEqual(
            [row["reason"] for row in rejected],
            [
                "amount_cents must be a positive integer",
                "duplicate order_id",
                "invalid order_date",
            ],
        )
        self.assertEqual(
            summary,
            [
                {
                    "order_date": "2026-10-01",
                    "city": "Melbourne",
                    "order_count": 1,
                    "total_amount_cents": 850,
                },
                {
                    "order_date": "2026-10-01",
                    "city": "Sydney",
                    "order_count": 2,
                    "total_amount_cents": 1500,
                },
            ],
        )

    def test_missing_schema_is_reported(self):
        clean, rejected, summary = transform_csv("order_id,city\n1,Sydney\n")
        self.assertEqual(clean, [])
        self.assertEqual(summary, [])
        self.assertIn("amount_cents", rejected[0]["reason"])

    def test_s3_event_writes_three_outputs(self):
        uploaded = {}

        class FakeS3:
            def get_object(self, Bucket, Key):
                if (Bucket, Key) != ("au-orders-demo", "input/orders.csv"):
                    raise AssertionError(f"Unexpected S3 input: {Bucket}/{Key}")
                payload = SAMPLE.read_bytes()
                return {"ContentLength": len(payload), "Body": io.BytesIO(payload)}

            def put_object(self, Bucket, Key, Body, ContentType):
                uploaded[Key] = json.loads(Body) if Key.endswith(".json") else Body

        fake_s3 = FakeS3()
        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "au-orders-demo"},
                        "object": {"key": "input/orders.csv"},
                    }
                }
            ]
        }
        with patch.dict(sys.modules, {"boto3": SimpleNamespace(client=lambda name: fake_s3)}):
            result = lambda_handler(event, None)

        self.assertEqual(result, {"processed_files": 1})
        self.assertEqual(
            set(uploaded),
            {
                "output/clean/orders.jsonl",
                "output/rejected/orders.jsonl",
                "output/summary/orders.json",
            },
        )
        self.assertEqual(uploaded["output/summary/orders.json"]["rejected_count"], 3)


if __name__ == "__main__":
    unittest.main()
