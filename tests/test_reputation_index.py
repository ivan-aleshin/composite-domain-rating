from __future__ import annotations

import json
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
    build_reputation_snapshot,
    discover_release_assets,
    rolling_components,
    sha256_file,
    source_mask,
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
            frame = pd.DataFrame(
                {
                    "registered_domain": domains,
                    "consensus_score": scores,
                    "sources_count": source_counts,
                    "ranking_sources_present": source_sets,
                    "risk_sources_count": risk_counts,
                    "snapshot_date": snapshot_date.isoformat(),
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
