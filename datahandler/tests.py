from django.test import SimpleTestCase

from .consumers import _limit_table_payload


class DatasetStreamPayloadTests(SimpleTestCase):
    def test_limits_snapshot_rows_to_tail(self):
        payload = {"columns": ["x"], "data": [[i] for i in range(5)]}

        limited = _limit_table_payload(payload, "data", 3)

        self.assertEqual(limited["data"], [[2], [3], [4]])
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["row_count"], 5)
        self.assertEqual(limited["sent_rows"], 3)

    def test_keeps_small_append_payloads_unchanged(self):
        payload = {"columns": ["x"], "rows": [[1], [2]]}

        limited = _limit_table_payload(payload, "rows", 3)

        self.assertEqual(limited["rows"], [[1], [2]])
        self.assertFalse(limited["truncated"])
        self.assertEqual(limited["row_count"], 2)
        self.assertEqual(limited["sent_rows"], 2)
