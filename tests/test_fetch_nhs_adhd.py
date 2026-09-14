"""Tests for scripts/fetch_nhs_adhd.py.

The small synthetic tests run in every GitHub Action. If the two official NHS
fixture CSVs are available through ``NHS_ADHD_FIXTURE_DIR``, the regression
test also checks the May-to-August 2026 revision history.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from datetime import date
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "fetch_nhs_adhd.py"
if not SCRIPT.exists():
    SCRIPT = HERE / "fetch_nhs_adhd.py"

SPEC = importlib.util.spec_from_file_location("fetch_nhs_adhd", SCRIPT)
feed = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = feed
SPEC.loader.exec_module(feed)


def synthetic_csv(missing_level: str | None = None) -> bytes:
    headers = list(feed.CONFIG["columns"].values())
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    base_values = {
        "ADHD007": [5000, 7000, 4000, 12000, 0],
        "ADHD003": [20000, 150000, 80000, 400000, 10000],
    }
    for period_index, (start, end) in enumerate((
        ("01/05/2026", "31/05/2026"),
        ("2026-06-01", "2026-06-30"),
    )):
        for measure_id, values in base_values.items():
            for index, (level, description) in enumerate(feed.CONFIG["age_levels"].items()):
                if level == missing_level:
                    continue
                writer.writerow({
                    feed.CONFIG["columns"]["period_start"]: start,
                    feed.CONFIG["columns"]["period_end"]: end,
                    feed.CONFIG["columns"]["breakdown"]: "Age Group",
                    feed.CONFIG["columns"]["level"]: level,
                    feed.CONFIG["columns"]["level_description"]: description,
                    feed.CONFIG["columns"]["measure_id"]: measure_id,
                    feed.CONFIG["columns"]["value"]: values[index] + period_index * 100,
                })
    return output.getvalue().encode()


class DiscoveryTests(unittest.TestCase):
    def test_rss_fixture_shape(self):
        raw = b"""<?xml version='1.0'?><rss><channel><item>
        <title>ADHD Management Information - August 2026</title>
        <link>https://digital.nhs.uk/data-and-information/publications/statistical/mi-adhd/august-2026</link>
        <pubDate>Thu, 27 Aug 2026 08:31:48 GMT</pubDate>
        </item></channel></rss>"""
        release = feed.discover_via_rss(raw)
        self.assertEqual(release.release_period, "2026-08")
        self.assertEqual(release.publication_date, "2026-08-27")

    def test_predicted_urls_are_chronological(self):
        periods = [item.release_period for item in feed.predicted_release_candidates(date(2026, 9, 14))]
        self.assertEqual(periods[0], "2026-08")
        self.assertEqual(periods, sorted(periods, reverse=True))

    def test_national_csv_excludes_regional_files(self):
        html = b"""
        <a href='https://files.digital.nhs.uk/AA/one/ADHD_Aug26_byRegion.csv'>By Region</a>
        <a href='https://files.digital.nhs.uk/BB/two/ADHD_Aug26.csv'>ADHD Management Information</a>
        <a href='https://files.digital.nhs.uk/CC/three/ADHD_Aug26_byICB.csv'>By ICB</a>
        """
        result = feed.national_csv_url("https://digital.nhs.uk/example", html)
        self.assertTrue(result.endswith("ADHD_Aug26.csv"))

    def test_known_csv_is_verified_when_discovery_is_unavailable(self):
        raw = synthetic_csv()
        release_url = "https://digital.nhs.uk/data-and-information/publications/statistical/mi-adhd/may-2026"
        csv_url = "https://files.digital.nhs.uk/AA/BBBBBB/ADHD_May26.csv"
        published = date.today().isoformat()
        release = feed.Release(release_url, "2026-05", "test", published)
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            feed.process(state_path, release, raw, csv_url, published)
            arguments = feed.cli_arguments(["--output", str(state_path)])
            with (
                mock.patch.object(feed, "discover_release", side_effect=feed.Review("offline")),
                mock.patch.object(feed, "request_bytes", return_value=(raw, csv_url)),
            ):
                result = feed.run(arguments)
            state = json.loads(state_path.read_text())
        self.assertEqual(result, 0)
        self.assertEqual(state["feed"]["data_status"], "current")
        self.assertEqual(state["feed"]["discovery_status"], "current_csv_fallback")


class ParsingTests(unittest.TestCase):
    def test_verified_age_aggregation_and_mixed_dates(self):
        observations = feed.extract_observations(feed.parse_csv(synthetic_csv()))
        latest = feed.latest_observations(observations)
        values = {item["measure_id"]: item["value"] for item in latest}
        self.assertEqual(values, {"ADHD007": 28_500, "ADHD003": 660_500})

    def test_missing_age_level_requires_review(self):
        with self.assertRaises(feed.Review):
            feed.extract_observations(feed.parse_csv(synthetic_csv("Unknown")))

    def test_snapshot_identity_includes_source_url(self):
        digest = "a" * 64
        first = feed.snapshot_id_for("https://example.test/may-2026", digest)
        second = feed.snapshot_id_for("https://example.test/august-2026", digest)
        self.assertNotEqual(first, second)

    def test_in_place_csv_replacement_creates_a_new_snapshot(self):
        release = feed.Release(
            "https://digital.nhs.uk/data-and-information/publications/statistical/mi-adhd/may-2026",
            "2026-05", "test", "2026-05-28"
        )
        original = synthetic_csv()
        replacement = original.replace(b",12000\r\n", b",12005\r\n", 1)
        self.assertNotEqual(original, replacement)
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            feed.process(state_path, release, original, "same-url.csv", "2026-05-28")
            feed.process(state_path, release, replacement, "same-url.csv", "2026-05-28")
            state = json.loads(state_path.read_text())
        self.assertEqual(len(state["snapshots"]), 2)
        self.assertEqual(sum(item["kind"] == "revision" for item in state["changes"]), 1)

    def test_review_failure_preserves_last_validated_snapshot(self):
        release_url = "https://digital.nhs.uk/data-and-information/publications/statistical/mi-adhd/may-2026"
        release = feed.Release(release_url, "2026-05", "test", "2026-05-28")
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            state_path = directory / "state.json"
            valid_path = directory / "valid.csv"
            invalid_path = directory / "invalid.csv"
            valid_path.write_bytes(synthetic_csv())
            invalid_path.write_bytes(synthetic_csv("Unknown"))
            feed.process(state_path, release, valid_path.read_bytes(), "valid.csv", "2026-05-28")
            before = json.loads(state_path.read_text())
            with mock.patch.dict(os.environ, {"REVIEW_FILE": str(directory / "review.txt")}):
                result = feed.main([
                    "--csv-file", str(invalid_path),
                    "--release-url", release_url,
                    "--publication-date", "2026-05-28",
                    "--output", str(state_path),
                ])
            after = json.loads(state_path.read_text())

        self.assertEqual(result, 1)
        self.assertEqual(after["current"]["snapshot_id"], before["current"]["snapshot_id"])
        self.assertEqual(after["feed"]["last_successful_at"], before["feed"]["last_successful_at"])
        self.assertEqual(after["feed"]["data_status"], "review_required")


class OfficialFixtureTests(unittest.TestCase):
    def fixture(self, filename: str) -> Path:
        configured = os.environ.get("NHS_ADHD_FIXTURE_DIR")
        directory = Path(configured) if configured else HERE
        path = directory / filename
        if not path.exists():
            self.skipTest(f"optional official fixture not found: {filename}")
        return path

    def test_may_to_august_2026(self):
        may = self.fixture("ADHD_May26_V2.csv")
        august = self.fixture("ADHD_Aug26.csv")
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            may_release = feed.Release(
                "https://digital.nhs.uk/data-and-information/publications/statistical/mi-adhd/may-2026",
                "2026-05", "fixture", "2026-05-28"
            )
            august_release = feed.Release(
                "https://digital.nhs.uk/data-and-information/publications/statistical/mi-adhd/august-2026",
                "2026-08", "fixture", "2026-08-27"
            )
            feed.process(state_path, may_release, may.read_bytes(), may.name, "2026-05-28")
            feed.process(state_path, august_release, august.read_bytes(), august.name, "2026-08-27")
            state = json.loads(state_path.read_text())

        headline = {item["measure_id"]: item for item in state["current"]["observations"]}
        self.assertEqual(headline["ADHD007"]["value"], 37_105)
        self.assertEqual(headline["ADHD003"]["value"], 780_430)
        self.assertEqual(len(state["snapshots"]), 2)
        self.assertEqual(len(state["snapshots"][0]["observations"]), 26)
        self.assertEqual(sum(item["kind"] == "revision" for item in state["changes"]), 20)
        self.assertEqual(sum(item["kind"] == "new_period" for item in state["changes"]), 6)


if __name__ == "__main__":
    unittest.main()
