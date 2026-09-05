from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from download_release_history import (  # noqa: E402
    select_history_tags,
    select_release_asset_names,
)
from reputation_index import (  # noqa: E402
    HISTORY_WINDOW,
    PUBLIC_COLUMNS,
    SHOCK_DAMPING,
    band_noise_confidence,
    build_reputation_snapshot,
    discover_release_assets,
    json_safe,
    rolling_components,
    sha256_file,
    source_mask,
    validate_reputation_snapshot,
    write_deterministic_csv_gz,
)


class ReputationIndexTest(unittest.TestCase):
    def create_release_history(self, root: Path) -> tuple[list, np.ndarray]:
        domains = [f"domain-{index:03d}.example" for index in range(120)]
        start = date(2026, 6, 1)
        score_columns = []
        for week in range(HISTORY_WINDOW):
            snapshot_date = start + timedelta(days=7 * week)
            iso_year, iso_week, _ = snapshot_date.isocalendar()
            tag = f"data-{iso_year}-W{iso_week:02d}"
            release_dir = root / tag
            release_dir.mkdir(parents=True)
            slopes = (np.arange(len(domains)) % 5 - 2) * 0.2
            scores = 55 + np.arange(len(domains)) * 0.25 + slopes * week
            score_columns.append(scores)
            source_sets = np.full(
                len(domains),
                "tranco,majestic,radar,crux,opr",
                dtype=object,
            )
            source_counts = np.full(len(domains), 5, dtype=np.uint8)
            if week == HISTORY_WINDOW - 1:
                source_sets[0] = "tranco,majestic,radar,crux"
                source_counts[0] = 4
            risk_counts = np.zeros(len(domains), dtype=np.uint8)
            if week == HISTORY_WINDOW - 1:
                risk_counts[1] = 2
            if week == HISTORY_WINDOW - 2:
                risk_counts[2] = 1
            frame = pd.DataFrame(
                {
                    "registered_domain": domains,
                    "consensus_score": scores,
                    "sources_count": source_counts,
                    "ranking_sources_present": source_sets,
                    "risk_sources_count": risk_counts,
                    "snapshot_date": snapshot_date.isoformat(),
                    "methodology_version": "v0.3.0-beta",
                }
            )
            csv_path = release_dir / f"domain_consensus_{snapshot_date.isoformat()}.csv.gz"
            frame.to_csv(csv_path, index=False, compression="gzip")
            metadata = {
                "snapshot_date": snapshot_date.isoformat(),
                "methodology_version": "v0.3.0-beta",
                "release": {"tag": tag},
            }
            (release_dir / f"meta_{snapshot_date.isoformat()}.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
        return discover_release_assets(root), np.column_stack(score_columns)

    def test_source_mask_uses_exact_source_names(self) -> None:
        values = pd.Series(
            ["tranco,opr", "majestic,radar,crux", "", pd.NA, "not-opr"]
        )
        self.assertEqual(source_mask(values).tolist(), [17, 14, 0, 0, 0])

    def test_build_applies_half_structural_shock_damping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assets, scores = self.create_release_history(Path(temp_dir))
            build = build_reputation_snapshot(assets)
            frame = build.frame.set_index("registered_domain")

            current_level = rolling_components(scores, 4)["ewma"][0]
            previous_level = rolling_components(scores[:, :-1], 4)["ewma"][0]
            expected = previous_level + SHOCK_DAMPING * (current_level - previous_level)

            self.assertEqual(tuple(build.frame.columns), PUBLIC_COLUMNS)
            self.assertEqual(frame.loc["domain-000.example", "structural_shock"], "true")
            self.assertAlmostEqual(
                frame.loc["domain-000.example", "ddri_score_candidate"],
                expected,
                places=6,
            )
            self.assertTrue(
                frame["reputation_confidence"].dropna().between(0, 1).all()
            )
            self.assertEqual(
                frame.loc["domain-001.example", "observed_risk"],
                "multi-source-observed",
            )
            self.assertEqual(
                frame.loc["domain-002.example", "observed_risk"],
                "recent-history-only",
            )

    def test_missing_week_is_padded_without_compressing_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets, _ = self.create_release_history(root)
            missing_tag = assets[3].tag
            shutil.rmtree(assets[3].csv_path.parent)

            selected = discover_release_assets(root)
            build = build_reputation_snapshot(selected)

            self.assertEqual(len(selected), HISTORY_WINDOW - 1)
            self.assertIn(missing_tag, build.history_tags)
            self.assertTrue(
                (build.frame["history_observations"] == HISTORY_WINDOW - 1).all()
            )

    def test_history_accepts_one_day_schedule_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets, _ = self.create_release_history(root)
            asset = assets[1]
            shifted_date = date.fromisoformat(asset.snapshot_date) + timedelta(days=1)

            frame = pd.read_csv(asset.csv_path)
            frame["snapshot_date"] = shifted_date.isoformat()
            shifted_csv = asset.csv_path.with_name(
                f"domain_consensus_{shifted_date.isoformat()}.csv.gz"
            )
            frame.to_csv(shifted_csv, index=False, compression="gzip")
            asset.csv_path.unlink()

            metadata = json.loads(asset.metadata_path.read_text(encoding="utf-8"))
            metadata["snapshot_date"] = shifted_date.isoformat()
            shifted_metadata = asset.metadata_path.with_name(
                f"meta_{shifted_date.isoformat()}.json"
            )
            shifted_metadata.write_text(json.dumps(metadata), encoding="utf-8")
            asset.metadata_path.unlink()

            selected = discover_release_assets(root)
            self.assertEqual(len(selected), HISTORY_WINDOW)
            build_reputation_snapshot(selected)

    def test_history_rejects_too_many_missing_calendar_weeks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets, _ = self.create_release_history(root)
            for asset in assets[2:5]:
                shutil.rmtree(asset.csv_path.parent)

            with self.assertRaisesRegex(RuntimeError, "At least 6 of the latest 8"):
                discover_release_assets(root)

    def test_history_rejects_release_tag_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets, _ = self.create_release_history(root)
            metadata_path = assets[0].metadata_path
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["release"]["tag"] = assets[1].tag
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Release tag mismatch"):
                discover_release_assets(root)

    def test_history_rejects_mixed_methodology_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets, _ = self.create_release_history(root)
            metadata_path = assets[0].metadata_path
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["methodology_version"] = "v0.4.0-beta"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "one consensus methodology"):
                discover_release_assets(root)

    def test_history_rejects_asset_filename_date_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets, _ = self.create_release_history(root)
            asset = assets[0]
            wrong_name = asset.csv_path.with_name("domain_consensus_2026-06-02.csv.gz")
            asset.csv_path.rename(wrong_name)

            with self.assertRaisesRegex(RuntimeError, "Asset date mismatch"):
                discover_release_assets(root)

    def test_every_csv_snapshot_date_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets, _ = self.create_release_history(root)
            historical = assets[0]
            frame = pd.read_csv(historical.csv_path)
            frame["snapshot_date"] = assets[1].snapshot_date
            frame.to_csv(historical.csv_path, index=False, compression="gzip")

            with self.assertRaisesRegex(RuntimeError, "Snapshot date mismatch"):
                build_reputation_snapshot(discover_release_assets(root))

    def test_validate_reputation_snapshot_rejects_invalid_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assets, _ = self.create_release_history(Path(temp_dir))
            frame = build_reputation_snapshot(assets).frame.copy()
            frame.loc[0, "reputation_confidence"] = 1.1
            with self.assertRaisesRegex(RuntimeError, r"outside \[0, 1\]"):
                validate_reputation_snapshot(frame)

    def test_empty_rank_band_noise_scale_serializes_as_null(self) -> None:
        confidence, scales = band_noise_confidence(
            np.array([0.2]),
            np.array([0]),
            np.array([True]),
        )
        self.assertGreater(confidence[0], 0)
        self.assertIsNone(json_safe(scales)[1])

    def test_deterministic_gzip_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets, _ = self.create_release_history(root / "history")
            frame = build_reputation_snapshot(assets).frame
            first = root / "first.csv.gz"
            second = root / "second.csv.gz"
            write_deterministic_csv_gz(frame, first)
            write_deterministic_csv_gz(frame, second)
            self.assertEqual(sha256_file(first), sha256_file(second))

    def test_history_selection_crosses_iso_year(self) -> None:
        tags = [
            "data-2026-W50",
            "data-2026-W51",
            "data-2026-W52",
            "data-2026-W53",
            "data-2027-W01",
            "data-2027-W02",
            "data-2027-W03",
            "data-2027-W04",
            "data-latest",
            "v1.0.0",
        ]
        self.assertEqual(
            select_history_tags(tags, "data-2027-W04", 4),
            ["data-2027-W01", "data-2027-W02", "data-2027-W03", "data-2027-W04"],
        )

    def test_history_selection_allows_one_missing_week(self) -> None:
        tags = [
            "data-2026-W31",
            "data-2026-W32",
            "data-2026-W34",
            "data-2026-W35",
        ]
        self.assertEqual(
            select_history_tags(tags, "data-2026-W35", 4),
            ["data-2026-W32", "data-2026-W34", "data-2026-W35"],
        )

    def test_release_asset_selection_ignores_ddri_metadata(self) -> None:
        names = [
            "domain_consensus_2026-08-30.csv.gz",
            "meta_2026-08-30.json",
            "domain_reputation_experimental_2026-08-30.csv.gz",
            "meta_reputation_experimental_2026-08-30.json",
        ]
        self.assertEqual(
            select_release_asset_names(names, "data-2026-W35"),
            ("domain_consensus_2026-08-30.csv.gz", "meta_2026-08-30.json"),
        )


if __name__ == "__main__":
    unittest.main()
