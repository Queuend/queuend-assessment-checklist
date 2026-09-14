#!/usr/bin/env python3
"""Maintain the ADHD Junction NHS ADHD management-information feed.

Discovers the latest NHS England release, validates the national CSV, and
updates ``assets/data/nhs-adhd.json``. National totals are derived by summing
the five Age Group rows; a changed source shape pauses for human review.

Exit codes: 0 successful check, 1 review required, 2 operational failure.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree


CONFIG = {
    "series_url": "https://digital.nhs.uk/data-and-information/publications/statistical/mi-adhd",
    "rss_url": "https://digital.nhs.uk/feed/pubfeed.xml",
    "release_url_template": "https://digital.nhs.uk/data-and-information/publications/statistical/mi-adhd/{month}-{year}",
    "release_months": (2, 5, 8, 11),
    "columns": {
        "period_start": "REPORTING_PERIOD_START_DATE",
        "period_end": "REPORTING_PERIOD_END_DATE",
        "breakdown": "BREAKDOWN",
        "level": "PRIMARY_LEVEL",
        "level_description": "PRIMARY_LEVEL_DESCRIPTION",
        "measure_id": "INDICATOR_ID",
        "value": "VALUE",
    },
    "age_breakdown": "Age Group",
    "age_levels": {
        "0 to 4": "People aged 0 to 4",
        "5 to 17": "People aged 5 to 17",
        "18 to 24": "People aged 18 to 24",
        "25+": "People aged 25+",
        "Unknown": "People aged Unknown",
    },
    "measures": {
        "ADHD007": {
            "slug": "new_referrals",
            "label": "The number of new referrals that may be for an ADHD assessment made in the reporting month",
            "minimum": 5_000,
            "maximum": 200_000,
            "qualifier": "up_to",
        },
        "ADHD003": {
            "slug": "open_referrals",
            "label": "The number of open referrals that may be for an ADHD assessment",
            "minimum": 100_000,
            "maximum": 3_000_000,
            "qualifier": None,
        },
    },
    "thresholds": {"new_period": 0.35, "revision": 0.50},
    "max_snapshots": 40,
    "max_changes": 200,
    "output": "assets/data/nhs-adhd.json",
    "user_agent": "ADHDJunction-feed/2.0 (+https://adhdjunction.com)",
    "timeout": 45,
}

MONTH_NAMES = {2: "february", 5: "may", 8: "august", 11: "november"}


class Review(Exception):
    """The source was reachable, but it is unsafe to publish automatically."""


@dataclass(frozen=True)
class Release:
    url: str
    release_period: str
    discovered_via: str
    publication_date: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_bytes(url: str, accept: str | None = None) -> tuple[bytes, str]:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", CONFIG["user_agent"])
    if accept:
        request.add_header("Accept", accept)
    with urllib.request.urlopen(request, timeout=CONFIG["timeout"]) as response:
        return response.read(), response.geturl()


def url_exists(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD")
    request.add_header("User-Agent", CONFIG["user_agent"])
    try:
        with urllib.request.urlopen(request, timeout=CONFIG["timeout"]) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 405}:
            return False
        try:
            request_bytes(url)
            return True
        except Exception:
            return False
    except urllib.error.URLError:
        return False


def release_period_from_text(text: str) -> str | None:
    lowered = text.lower()
    for month, name in MONTH_NAMES.items():
        match = re.search(rf"\b{name}[-\s]+(20\d{{2}})\b", lowered)
        if match:
            return f"{int(match.group(1)):04d}-{month:02d}"
    return None


def normalise_release_url(url: str) -> str:
    return url.strip().split("#", 1)[0].rstrip("/")


def parse_rss_releases(raw: bytes) -> list[Release]:
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise Review(f"NHS publication RSS is not valid XML: {exc}") from exc

    releases: list[Release] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if "adhd management information" not in title.lower() and "/mi-adhd/" not in link:
            continue
        period = release_period_from_text(f"{title} {link}")
        if not period or not link:
            continue
        published = None
        pub_date = (item.findtext("pubDate") or "").strip()
        if pub_date:
            try:
                published = parsedate_to_datetime(pub_date).date().isoformat()
            except (TypeError, ValueError):
                pass
        releases.append(Release(normalise_release_url(link), period, "rss", published))
    return sorted(releases, key=lambda item: item.release_period, reverse=True)


def discover_via_rss(rss_bytes: bytes | None = None) -> Release | None:
    if rss_bytes is None:
        try:
            rss_bytes, _ = request_bytes(CONFIG["rss_url"], "application/rss+xml")
        except Exception:
            return None
    try:
        releases = parse_rss_releases(rss_bytes)
    except Review:
        return None
    return releases[0] if releases else None


def predicted_release_candidates(today: date | None = None) -> list[Release]:
    today = today or date.today()
    candidates: list[Release] = []
    for year in range(today.year, today.year - 3, -1):
        for month in CONFIG["release_months"]:
            if (year, month) > (today.year, today.month):
                continue
            candidates.append(Release(
                CONFIG["release_url_template"].format(month=MONTH_NAMES[month], year=year),
                f"{year:04d}-{month:02d}", "predicted_url"
            ))
    return sorted(candidates, key=lambda item: item.release_period, reverse=True)


def discover_via_predicted_url(today: date | None = None) -> Release | None:
    for release in predicted_release_candidates(today):
        if url_exists(release.url):
            return release
    return None


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def anchors_from_html(raw: bytes) -> list[tuple[str, str]]:
    parser = AnchorParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return parser.anchors


def discover_via_landing_page() -> Release | None:
    try:
        raw, final_url = request_bytes(CONFIG["series_url"])
    except Exception:
        return None
    releases: dict[str, Release] = {}
    for href, text in anchors_from_html(raw):
        absolute = normalise_release_url(urllib.parse.urljoin(final_url, href))
        if "/mi-adhd/" not in absolute:
            continue
        period = release_period_from_text(f"{absolute} {text}")
        if period:
            releases[absolute] = Release(absolute, period, "landing_page")
    return max(releases.values(), key=lambda item: item.release_period, default=None)


def discover_release(rss_bytes: bytes | None = None) -> Release:
    release = discover_via_rss(rss_bytes)
    if release:
        return release
    for finder in (discover_via_predicted_url, discover_via_landing_page):
        release = finder()
        if release:
            return release
    raise Review("No ADHD management-information release was found")


def national_csv_url(release_url: str, release_html: bytes) -> str:
    candidates: list[str] = []
    for href, text in anchors_from_html(release_html):
        absolute = urllib.parse.urljoin(release_url + "/", href)
        filename = urllib.parse.unquote(urllib.parse.urlparse(absolute).path.rsplit("/", 1)[-1])
        combined = f"{filename} {text}".lower()
        if not filename.lower().endswith(".csv"):
            continue
        if any(term in combined for term in ("byregion", "by region", "byicb", "by icb")):
            continue
        if re.fullmatch(r"ADHD_[A-Za-z]+\d{2}(?:_V\d+)?\.csv", filename, re.I):
            candidates.append(absolute)

    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise Review(
            "Expected one national ADHD CSV link on the release page; "
            f"found {len(unique)}"
        )
    return unique[0]


def publication_date_from_html(raw: bytes) -> str | None:
    html = raw.decode("utf-8", errors="replace")
    match = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', html, re.I)
    if match:
        return match.group(1)
    match = re.search(
        r"Publication date:\s*</[^>]+>\s*([0-9]{1,2}\s+[A-Za-z]+\s+20\d{2})",
        html, re.I
    )
    if match:
        try:
            return datetime.strptime(match.group(1), "%d %B %Y").date().isoformat()
        except ValueError:
            pass
    return None


def parse_source_date(raw: str) -> date:
    value = raw.strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise Review(f"Unrecognised reporting date {raw!r}")


def parse_csv(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8-sig", errors="strict")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise Review("CSV has no header row")
    required = set(CONFIG["columns"].values())
    missing = sorted(required.difference(reader.fieldnames))
    if missing:
        raise Review(
            "CSV columns missing: " + ", ".join(missing) +
            ". Present: " + ", ".join(reader.fieldnames)
        )
    rows = list(reader)
    if not rows:
        raise Review("CSV has no data rows")
    return rows


def dimensions_key(observation: dict[str, Any]) -> str:
    return json.dumps(observation["dimensions"], sort_keys=True, separators=(",", ":"))


def observation_key(observation: dict[str, Any]) -> tuple[str, str, str]:
    return observation["measure_id"], observation["data_period"], dimensions_key(observation)


def observation_value_key(observation: dict[str, Any]) -> tuple[int, int]:
    return observation["value_min"], observation["value_max"]


def extract_observations(rows: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    columns = CONFIG["columns"]
    grouped: dict[tuple[str, str], dict[str, dict[str, str]]] = defaultdict(dict)

    for row in rows:
        measure_id = (row.get(columns["measure_id"]) or "").strip()
        if measure_id not in CONFIG["measures"]:
            continue
        if (row.get(columns["breakdown"]) or "").strip() != CONFIG["age_breakdown"]:
            continue
        start = parse_source_date(row.get(columns["period_start"]) or "")
        end = parse_source_date(row.get(columns["period_end"]) or "")
        if (start.year, start.month) != (end.year, end.month):
            raise Review(f"{measure_id} has a reporting period spanning multiple months")
        period = f"{end.year:04d}-{end.month:02d}"
        level = (row.get(columns["level"]) or "").strip()
        if level in grouped[(measure_id, period)]:
            raise Review(f"Duplicate {measure_id} {period} age level {level}")
        grouped[(measure_id, period)][level] = row

    observations: list[dict[str, Any]] = []
    expected_levels = CONFIG["age_levels"]
    for measure_id in CONFIG["measures"]:
        periods = sorted(period for mid, period in grouped if mid == measure_id)
        if not periods:
            raise Review(f"Measure {measure_id} was not found in the Age Group breakdown")
        if len(periods) < 2:
            raise Review(f"Measure {measure_id} contains fewer than two reporting periods")

        for period in periods:
            level_rows = grouped[(measure_id, period)]
            if set(level_rows) != set(expected_levels):
                missing = sorted(set(expected_levels).difference(level_rows))
                extra = sorted(set(level_rows).difference(expected_levels))
                raise Review(
                    f"{measure_id} {period} age levels changed; "
                    f"missing={missing or 'none'}, extra={extra or 'none'}"
                )

            known_total = 0
            suppressed = 0
            for level, expected_description in expected_levels.items():
                row = level_rows[level]
                description = (row.get(columns["level_description"]) or "").strip()
                if description != expected_description:
                    raise Review(
                        f"{measure_id} {period} level {level} description changed "
                        f"from {expected_description!r} to {description!r}"
                    )
                raw_value = (row.get(columns["value"]) or "").strip().replace(",", "")
                if raw_value == "*":
                    suppressed += 1
                    continue
                if not re.fullmatch(r"\d+", raw_value):
                    raise Review(f"{measure_id} {period} has unsupported value {raw_value!r}")
                cell_value = int(raw_value)
                if cell_value % 5:
                    raise Review(f"{measure_id} {period} value {cell_value} is not rounded to five")
                known_total += cell_value

            value_min = known_total
            value_max = known_total + 4 * suppressed
            spec = CONFIG["measures"][measure_id]
            if not (spec["minimum"] <= value_max <= spec["maximum"]):
                raise Review(
                    f"{measure_id} {period} total {value_min}-{value_max} is outside "
                    f"the plausible range {spec['minimum']}-{spec['maximum']}"
                )
            observations.append({
                "measure_id": measure_id,
                "slug": spec["slug"],
                "label": spec["label"],
                "data_period": period,
                "dimensions": {
                    "geography": "England",
                    "breakdown": "Age Group",
                    "aggregation": "all age groups including unknown",
                },
                "value": value_max,
                "value_min": value_min,
                "value_max": value_max,
                "suppressed_cells": suppressed,
                "qualifier": spec["qualifier"],
            })
    return sorted(observations, key=lambda item: (item["measure_id"], item["data_period"]))


def latest_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest = [
        max((item for item in observations if item["measure_id"] == measure_id),
            key=lambda item: item["data_period"])
        for measure_id in CONFIG["measures"]
    ]
    periods = {item["data_period"] for item in latest}
    if len(periods) != 1:
        raise Review(f"Latest headline measures cover different periods: {sorted(periods)}")
    if any(item["suppressed_cells"] for item in latest):
        raise Review("A latest headline total contains a suppressed age-group cell")
    return latest


def snapshot_id_for(source_url: str, csv_sha256: str) -> str:
    identity = f"{normalise_release_url(source_url)}\n{csv_sha256}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:20]


def percent_delta(previous: int, current: int) -> float | None:
    return None if previous == 0 else (current - previous) / previous


def change_record(kind: str, current: dict[str, Any], previous: dict[str, Any],
                  snapshot_id: str, previous_snapshot_id: str | None,
                  source_url: str, observed_at: str) -> dict[str, Any]:
    delta = percent_delta(previous["value"], current["value"])
    return {
        "observed_at": observed_at,
        "snapshot_id": snapshot_id,
        "previous_snapshot_id": previous_snapshot_id,
        "source_url": source_url,
        "kind": kind,
        "measure_id": current["measure_id"],
        "slug": current["slug"],
        "label": current["label"],
        "data_period": current["data_period"],
        "previous_data_period": previous["data_period"],
        "previous_value": previous["value"],
        "previous_value_min": previous["value_min"],
        "previous_value_max": previous["value_max"],
        "new_value": current["value"],
        "new_value_min": current["value_min"],
        "new_value_max": current["value_max"],
        "percentage_change": round(delta * 100, 2) if delta is not None else None,
        "dimensions": current["dimensions"],
    }


def build_changes(observations: list[dict[str, Any]],
                  previous_snapshot: dict[str, Any] | None,
                  snapshot_id: str, source_url: str,
                  observed_at: str) -> list[dict[str, Any]]:
    if not previous_snapshot:
        return []
    previous = {observation_key(item): item for item in previous_snapshot.get("observations", [])}
    current = {observation_key(item): item for item in observations}
    changes: list[dict[str, Any]] = []

    for key, observation in current.items():
        prior = previous.get(key)
        if prior and observation_value_key(prior) != observation_value_key(observation):
            delta = percent_delta(prior["value"], observation["value"])
            if delta is not None and abs(delta) > CONFIG["thresholds"]["revision"]:
                raise Review(
                    f"{observation['measure_id']} {observation['data_period']} was revised "
                    f"by {delta * 100:.1f}%, above the 50% review threshold"
                )
            changes.append(change_record(
                "revision", observation, prior, snapshot_id,
                previous_snapshot.get("snapshot_id"), source_url, observed_at
            ))

    previous_periods = {
        (item["measure_id"], dimensions_key(item), item["data_period"])
        for item in previous_snapshot.get("observations", [])
    }
    series: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        series[(observation["measure_id"], dimensions_key(observation))].append(observation)
    for items in series.values():
        items.sort(key=lambda item: item["data_period"])
        for index, observation in enumerate(items):
            identity = (observation["measure_id"], dimensions_key(observation), observation["data_period"])
            if identity in previous_periods or index == 0:
                continue
            prior_period = items[index - 1]
            delta = percent_delta(prior_period["value"], observation["value"])
            if delta is not None and abs(delta) > CONFIG["thresholds"]["new_period"]:
                raise Review(
                    f"{observation['measure_id']} moved {delta * 100:.1f}% from "
                    f"{prior_period['data_period']} to {observation['data_period']}, "
                    "above the 35% new-period review threshold"
                )
            changes.append(change_record(
                "new_period", observation, prior_period, snapshot_id,
                previous_snapshot.get("snapshot_id"), source_url, observed_at
            ))
    return sorted(changes,
                  key=lambda item: (item["data_period"], item["measure_id"], item["kind"]),
                  reverse=True)


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "feed": {
            "last_attempted_at": None,
            "last_successful_at": None,
            "data_status": "awaiting_first_run",
            "review_note": None,
        },
        "current": None,
        "snapshots": [],
        "changes": [],
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Review(f"Existing feed state cannot be read: {exc}") from exc
    if state.get("schema_version") != 2:
        raise Review("Existing feed state is not schema version 2")
    return state


def validate_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") != 2:
        raise Review("Feed schema_version must be 2")
    feed = state.get("feed")
    if not isinstance(feed, dict):
        raise Review("Feed metadata is missing")
    allowed = {"awaiting_first_run", "current", "review_required", "fetch_failed"}
    if feed.get("data_status") not in allowed:
        raise Review(f"Unknown data_status {feed.get('data_status')!r}")
    current = state.get("current")
    if current is None:
        return
    required = {
        "snapshot_id", "source_url", "csv_url", "csv_sha256", "release_period",
        "publication_date", "retrieved_at", "discovered_via", "observations",
    }
    missing = required.difference(current)
    if missing:
        raise Review("Current snapshot fields missing: " + ", ".join(sorted(missing)))
    headlines = current.get("observations")
    if (not isinstance(headlines, list) or
            {item.get("measure_id") for item in headlines} != set(CONFIG["measures"])):
        raise Review("Current snapshot must contain both headline measures")


def save_state(path: Path, state: dict[str, Any]) -> None:
    validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_review_file(message: str) -> None:
    path = Path(os.environ.get("REVIEW_FILE", "review.txt"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(message + "\n", encoding="utf-8")


def default_output_path() -> Path:
    return Path(__file__).resolve().parents[1] / CONFIG["output"]


def inspect_csv(raw: bytes) -> None:
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    for index, row in enumerate(reader):
        print(row)
        if index >= 12:
            break


def process(output_path: Path, release: Release, csv_raw: bytes,
            csv_url: str, publication_date: str) -> str:
    state = load_state(output_path)
    attempted_at = now_iso()
    state["feed"]["last_attempted_at"] = attempted_at
    digest = hashlib.sha256(csv_raw).hexdigest()
    snapshot_id = snapshot_id_for(release.url, digest)
    current = state.get("current")

    if current and current.get("snapshot_id") == snapshot_id:
        state["feed"].update(last_successful_at=attempted_at,
                             data_status="current", review_note=None)
        save_state(output_path, state)
        return f"unchanged (snapshot {snapshot_id})"
    if current and release.release_period < current.get("release_period", ""):
        state["feed"].update(last_successful_at=attempted_at,
                             data_status="current", review_note=None)
        save_state(output_path, state)
        return f"older release {release.release_period} ignored; current is {current.get('release_period')}"

    observations = extract_observations(parse_csv(csv_raw))
    headlines = latest_observations(observations)
    retrieved_at = now_iso()
    for observation in observations:
        observation["snapshot_id"] = snapshot_id

    previous_snapshot = (state.get("snapshots") or [None])[0]
    changes = build_changes(observations, previous_snapshot, snapshot_id,
                            release.url, retrieved_at)
    snapshot = {
        "snapshot_id": snapshot_id,
        "source_url": release.url,
        "csv_url": csv_url,
        "csv_sha256": digest,
        "release_period": release.release_period,
        "publication_date": publication_date,
        "retrieved_at": retrieved_at,
        "discovered_via": release.discovered_via,
        "observations": observations,
    }
    state["current"] = dict(snapshot, observations=headlines)
    state["snapshots"] = (
        [snapshot] + [item for item in state.get("snapshots", [])
                      if item.get("snapshot_id") != snapshot_id]
    )[:CONFIG["max_snapshots"]]
    state["changes"] = (changes + state.get("changes", []))[:CONFIG["max_changes"]]
    state["feed"].update(last_successful_at=attempted_at,
                         data_status="current", review_note=None)
    save_state(output_path, state)
    return f"updated snapshot {snapshot_id}: {len(observations)} observations, {len(changes)} changes"


def cli_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true",
                        help="print the source header and sample rows without updating state")
    parser.add_argument("--csv-file", type=Path,
                        help="use a local CSV instead of downloading the release")
    parser.add_argument("--rss-file", type=Path,
                        help="use a local RSS file for release discovery")
    parser.add_argument("--release-url",
                        help="release page URL (required with --csv-file)")
    parser.add_argument("--csv-url",
                        help="original CSV URL to record when importing a local fixture")
    parser.add_argument("--publication-date",
                        help="publication date YYYY-MM-DD (required with --csv-file)")
    parser.add_argument("--output", type=Path, default=default_output_path(),
                        help="feed JSON path")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    if args.csv_file and (not args.release_url or not args.publication_date):
        raise ValueError("--csv-file requires --release-url and --publication-date")

    if args.csv_file:
        csv_raw = args.csv_file.read_bytes()
        period = release_period_from_text(args.release_url)
        if not period:
            raise ValueError("Could not derive a release period from --release-url")
        release = Release(normalise_release_url(args.release_url), period,
                          "local_fixture", args.publication_date)
        csv_url = args.csv_url or args.csv_file.name
        publication_date = args.publication_date
    else:
        rss_raw = args.rss_file.read_bytes() if args.rss_file else None
        release = discover_release(rss_raw)
        release_html, final_release_url = request_bytes(release.url)
        release = Release(normalise_release_url(final_release_url),
                          release.release_period, release.discovered_via,
                          release.publication_date)
        csv_url = national_csv_url(release.url, release_html)
        csv_raw, csv_url = request_bytes(csv_url, "text/csv")
        publication_date = release.publication_date or publication_date_from_html(release_html)
        if not publication_date:
            raise Review("Publication date was not found in RSS or release page")

    if args.inspect:
        inspect_csv(csv_raw)
        return 0
    print(process(args.output, release, csv_raw, csv_url, publication_date))
    return 0


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    try:
        args = cli_arguments(argv)
        return run(args)
    except Review as exc:
        message = str(exc)
        if args is not None:
            try:
                state = load_state(args.output)
                state["feed"].update(last_attempted_at=now_iso(),
                                     data_status="review_required",
                                     review_note=message)
                save_state(args.output, state)
            except Exception as state_exc:
                message = f"{message}; could not update state: {state_exc}"
        write_review_file(message)
        print(f"REVIEW REQUIRED: {message}", file=sys.stderr)
        return 1
    except Exception as exc:
        message = f"Fetch error: {exc}"
        if args is not None:
            try:
                state = load_state(args.output)
                state["feed"].update(last_attempted_at=now_iso(),
                                     data_status="fetch_failed",
                                     review_note=message)
                save_state(args.output, state)
            except Exception as state_exc:
                message = f"{message}; could not update state: {state_exc}"
        write_review_file(message)
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
