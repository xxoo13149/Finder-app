from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.analysis import (
    paginate_time_partitioned,
    paginate_time_tail_recovery,
    partition_probe_max_offset,
)


def _record(record_id: str, timestamp: int) -> dict[str, Any]:
    return {"id": record_id, "timestamp": timestamp, "type": "TRADE"}


class TimePartitionProbeLimitTests(unittest.TestCase):
    def test_probe_limit_caps_non_single_partition_offsets(self) -> None:
        page_size = 2
        max_offset = 6
        probe_pages = 2
        probe_max_offset = partition_probe_max_offset(
            page_size=page_size,
            max_offset=max_offset,
            partition_probe_pages=probe_pages,
        )
        calls: list[dict[str, int]] = []

        def fetch_page(limit: int, offset: int, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
            calls.append(
                {"limit": limit, "offset": offset, "start": start_ts, "end": end_ts}
            )
            if start_ts < end_ts:
                return [
                    _record(f"wide-{start_ts}-{end_ts}-{offset}-{index}", end_ts)
                    for index in range(limit)
                ]
            if offset == 0:
                return [_record(f"point-{start_ts}", start_ts)]
            return []

        page = paginate_time_partitioned(
            page_size=page_size,
            max_offset=max_offset,
            fetch_page=fetch_page,
            start_ts=1,
            end_ts=4,
            partition_probe_pages=probe_pages,
        )

        non_single_calls = [call for call in calls if call["start"] < call["end"]]
        self.assertEqual(probe_max_offset, 2)
        self.assertTrue(non_single_calls)
        self.assertLess(probe_max_offset, max_offset)
        self.assertFalse(any(call["offset"] > probe_max_offset for call in non_single_calls))
        self.assertNotIn(max_offset, {call["offset"] for call in non_single_calls})
        self.assertEqual({call["offset"] for call in non_single_calls}, {0, probe_max_offset})
        self.assertTrue(page["complete"])
        self.assertEqual(page["stop_reason"], "partitioned_complete")
        self.assertEqual([record["id"] for record in page["records"]], ["point-4", "point-3", "point-2", "point-1"])
        self.assertEqual(page["record_count"], 4)
        self.assertEqual(page["partition_count"], 4)

    def test_single_point_partition_reads_full_offset_window_despite_probe_limit(self) -> None:
        calls: list[dict[str, int]] = []

        def fetch_page(limit: int, offset: int, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
            calls.append(
                {"limit": limit, "offset": offset, "start": start_ts, "end": end_ts}
            )
            if offset < 4:
                return [
                    _record(f"point-{start_ts}-{offset}-{index}", start_ts)
                    for index in range(limit)
                ]
            if offset == 4:
                return [_record(f"point-{start_ts}-tail", start_ts)]
            return []

        page = paginate_time_partitioned(
            page_size=2,
            max_offset=4,
            fetch_page=fetch_page,
            start_ts=42,
            end_ts=42,
            partition_probe_pages=1,
        )

        self.assertEqual([call["offset"] for call in calls], [0, 2, 4])
        self.assertTrue(page["complete"])
        self.assertEqual(page["stop_reason"], "last_page_partial")
        self.assertEqual(page["record_count"], 5)
        self.assertEqual(len(page["records"]), 5)
        self.assertEqual(page["partition_count"], 1)
        self.assertNotIn("probe_max_offset", page)

    def test_tail_recovery_reports_reasonable_summary_with_probe_limit(self) -> None:
        calls: list[dict[str, int]] = []

        def fetch_page(limit: int, offset: int, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
            calls.append(
                {"limit": limit, "offset": offset, "start": start_ts, "end": end_ts}
            )
            if (start_ts, end_ts) == (10, 12):
                return [_record("tail-12", 12), _record("tail-11-probe", 11)]
            if (start_ts, end_ts) == (11, 11):
                if offset < 4:
                    return [
                        _record(f"boundary-11-{offset}-{index}", 11)
                        for index in range(limit)
                    ]
                if offset == 4:
                    return [_record("boundary-11-tail", 11)]
                return []
            if (start_ts, end_ts) == (10, 10) and offset == 0:
                return [_record("tail-10", 10)]
            return []

        page = paginate_time_tail_recovery(
            page_size=2,
            max_offset=4,
            fetch_page=fetch_page,
            start_ts=10,
            end_ts=12,
            partition_probe_pages=1,
        )

        wide_offsets = [
            call["offset"] for call in calls if (call["start"], call["end"]) == (10, 12)
        ]
        boundary_offsets = [
            call["offset"] for call in calls if (call["start"], call["end"]) == (11, 11)
        ]
        self.assertEqual(wide_offsets, [0])
        self.assertEqual(boundary_offsets, [0, 2, 4])
        self.assertTrue(page["complete"])
        self.assertEqual(page["record_count"], len(page["records"]))
        self.assertEqual(page["record_count"], 7)
        self.assertEqual(page["partition_count"], 3)
        self.assertEqual(page["range_start"], 10)
        self.assertEqual(page["range_end"], 12)


if __name__ == "__main__":
    unittest.main()
