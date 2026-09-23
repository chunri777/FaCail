from __future__ import annotations

import copy
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_update


SHANGHAI = ZoneInfo("Asia/Shanghai")


def local_time(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=SHANGHAI)


class ScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.closed = {local_time(25, 0, 0).date()}

    def test_premarket_and_missed_premarket(self) -> None:
        self.assertIsNone(run_update.scheduled_slot(local_time(23, 8, 29), self.closed, set()))
        self.assertEqual(run_update.scheduled_slot(local_time(23, 8, 45), self.closed, set()),
                         ("premarket", "2026-09-23 08:30 premarket"))
        self.assertIsNone(run_update.scheduled_slot(local_time(23, 9, 30), self.closed, set()))

    def test_intraday_windows_and_latest_slot_only(self) -> None:
        self.assertEqual(run_update.scheduled_slot(local_time(23, 9, 35), self.closed, set())[1],
                         "2026-09-23 09:35 intraday")
        self.assertEqual(run_update.scheduled_slot(local_time(23, 10, 27), self.closed, set())[1],
                         "2026-09-23 10:25 intraday")
        self.assertEqual(run_update.scheduled_slot(local_time(23, 11, 29), self.closed, set())[1],
                         "2026-09-23 11:25 intraday")
        self.assertIsNone(run_update.scheduled_slot(local_time(23, 11, 31), self.closed, set()))
        self.assertIsNone(run_update.scheduled_slot(local_time(23, 13, 4), self.closed, set()))
        self.assertEqual(run_update.scheduled_slot(local_time(23, 13, 17), self.closed, set())[1],
                         "2026-09-23 13:15 intraday")
        self.assertIsNone(run_update.scheduled_slot(local_time(23, 15, 5), self.closed, set()))

    def test_postmarket_catch_up_and_deduplication(self) -> None:
        due = run_update.scheduled_slot(local_time(23, 17, 30), self.closed, set())
        self.assertEqual(due, ("postmarket", "2026-09-23 15:10 postmarket"))
        self.assertIsNone(run_update.scheduled_slot(local_time(23, 18, 0), self.closed, {due[1]}))

    def test_weekend_and_holiday_have_no_slot(self) -> None:
        self.assertIsNone(run_update.scheduled_slot(local_time(26, 10, 0), self.closed, set()))
        self.assertIsNone(run_update.scheduled_slot(local_time(25, 10, 0), self.closed, set()))


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codes = {f"{code:06d}" for code in range(8)}
        self.closed = set()
        self.now = local_time(23, 8, 30)
        self.payloads = {
            name: {"data_source": {"id": "astock"}}
            for name in run_update.DATA_NAMES
        }
        self.payloads["market"] = {
            "trade_date": "2026-09-22",
            "indices": [{"value": 1, "source_timestamp": "2026-09-22T15:00:00",
                         "freshness": "last_trading_day"} for _ in range(4)],
            "data_source": {"id": "astock", "stage": "premarket",
                            "last_trading_day": "2026-09-22", "freshness": "last_trading_day"},
        }
        self.payloads["holdings"]["holdings"] = [
            {"code": code, "source": "tencent", "close": 10,
             "source_timestamp": "2026-09-22T15:00:00", "freshness": "last_trading_day"}
            for code in sorted(self.codes)
        ]

    def test_seven_holdings_and_three_indices_pass(self) -> None:
        payloads = copy.deepcopy(self.payloads)
        payloads["holdings"]["holdings"].pop()
        payloads["market"]["indices"][-1]["value"] = None
        quality = run_update.publication_quality(payloads, "premarket", self.now, self.codes, self.closed)
        self.assertEqual((quality["successful_holdings"], quality["successful_indices"]), (7, 3))

    def test_six_holdings_or_two_indices_fail_without_publication(self) -> None:
        payloads = copy.deepcopy(self.payloads)
        payloads["holdings"]["holdings"] = payloads["holdings"]["holdings"][:6]
        with self.assertRaisesRegex(ValueError, "Minimum publication quality"):
            run_update.publication_quality(payloads, "premarket", self.now, self.codes, self.closed)
        payloads = copy.deepcopy(self.payloads)
        payloads["market"]["indices"][2]["value"] = None
        payloads["market"]["indices"][3]["value"] = None
        with self.assertRaisesRegex(ValueError, "Minimum publication quality"):
            run_update.publication_quality(payloads, "premarket", self.now, self.codes, self.closed)

    def test_stale_data_and_mock_source_fail(self) -> None:
        payloads = copy.deepcopy(self.payloads)
        payloads["market"]["data_source"]["freshness"] = "stale"
        with self.assertRaisesRegex(ValueError, "freshness"):
            run_update.publication_quality(payloads, "premarket", self.now, self.codes, self.closed)
        payloads = copy.deepcopy(self.payloads)
        payloads["review"]["data_source"]["id"] = "mock"
        with self.assertRaisesRegex(ValueError, "non-astock"):
            run_update.publication_quality(payloads, "premarket", self.now, self.codes, self.closed)

    def test_intraday_and_postmarket_need_current_trading_day(self) -> None:
        for stage, minute in (("intraday", 35), ("postmarket", 10)):
            with self.subTest(stage=stage):
                payloads = copy.deepcopy(self.payloads)
                payloads["market"]["trade_date"] = "2026-09-23"
                payloads["market"]["data_source"].update(
                    {"stage": stage, "last_trading_day": "2026-09-23", "freshness": "current"})
                for item in payloads["market"]["indices"] + payloads["holdings"]["holdings"]:
                    item.update({"source_timestamp": "2026-09-23T15:00:00", "freshness": "current"})
                now = local_time(23, 9 if stage == "intraday" else 15, minute)
                quality = run_update.publication_quality(payloads, stage, now, self.codes, self.closed)
                self.assertEqual(quality["successful_holdings"], 8)

    def test_unchanged_and_changed_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            staging = root / "staging"
            staging.mkdir()
            for name in run_update.DATA_NAMES:
                (root / "data" / f"{name}.json").write_text("{}")
                (staging / f"{name}.json").write_text("{}")
            with patch.object(run_update, "ROOT", root):
                self.assertFalse(run_update.publish_files(staging))
                (staging / "market.json").write_text('{"new": true}')
                self.assertTrue(run_update.publish_files(staging))
                self.assertEqual(json.loads((root / "data" / "market.json").read_text()), {"new": True})

    def test_failed_push_retains_pending_commit_then_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = {"pending_commit": "abc", "completed_slots": []}
            record = {}
            with patch.object(run_update, "STATE_PATH", Path(directory) / "state.json"), patch.object(
                run_update, "LOG_DIR", Path(directory)
            ), patch.object(run_update, "pending_commit_count", return_value=1), patch.object(
                run_update, "checked_git", return_value="abc"
            ), patch.object(run_update, "git", return_value=subprocess.CompletedProcess([], 1, "", "network failed")):
                with self.assertRaisesRegex(RuntimeError, "Pending push failed"):
                    run_update.retry_pending_push(state, record)
                self.assertEqual(state["pending_commit"], "abc")
                self.assertTrue(record["retry_needed"])
            with patch.object(run_update, "STATE_PATH", Path(directory) / "state.json"), patch.object(
                run_update, "LOG_DIR", Path(directory)
            ), patch.object(run_update, "pending_commit_count", return_value=1), patch.object(
                run_update, "checked_git", return_value="abc"
            ), patch.object(run_update, "git", return_value=subprocess.CompletedProcess([], 0, "", "")):
                run_update.retry_pending_push(state, record)
                self.assertIsNone(state["pending_commit"])
                self.assertTrue(record["push_success"])

    def test_missing_holdings_config_fails_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(run_update, "ROOT", root), patch.object(run_update, "LOG_DIR", root / "logs"), patch.object(
                run_update, "STATE_PATH", root / "logs" / "state.json"
            ), patch.object(run_update, "retry_pending_push"), patch.object(
                run_update, "checked_git", return_value=""
            ), patch.object(run_update, "git", return_value=subprocess.CompletedProcess([], 0, "", "")):
                result = run_update.run_update(local_time(23, 8, 35), wait_pages=False)
                self.assertEqual(result["result"], "failed")
                self.assertIn("holdings configuration is missing", result["error"])

    def test_launchd_slots_and_private_paths(self) -> None:
        with (ROOT / "launchd" / "com.facail.update.plist").open("rb") as stream:
            agent = plistlib.load(stream)
        slots = {(item["Hour"], item["Minute"]) for item in agent["StartCalendarInterval"]}
        self.assertEqual(len(slots), 26)
        self.assertTrue({(8, 30), (9, 35), (11, 25), (13, 5), (14, 55), (15, 10)} <= slots)
        self.assertTrue(agent["RunAtLoad"])
        self.assertEqual(agent["ProgramArguments"][0], "/usr/bin/python3")
        self.assertEqual(agent["ProgramArguments"][1], str(ROOT / "scripts" / "run_update.py"))
        self.assertEqual(agent["WorkingDirectory"], str(ROOT))
        self.assertEqual(agent["StandardOutPath"], str(ROOT / "logs" / "launchd.out.log"))
        ignores = (ROOT / ".gitignore").read_text()
        for path in ("config/holdings.local.json", ".env.local", "logs/", "cache/"):
            self.assertIn(path, ignores)


if __name__ == "__main__":
    unittest.main()
