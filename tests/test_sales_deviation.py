import unittest

from feature_engineering.features import MarketValidationSignals, SkinFeatureVector
from models.rule_engine import EvaluationResult
from models.sales_deviation import compare_score_to_sales, sales_blind_signals


def feature_vector(signals: MarketValidationSignals | None = None) -> SkinFeatureVector:
    return SkinFeatureVector(
        source_key="107-08",
        hero_id="107",
        hero_name="Zhao Yun",
        skin_name="Long Dan",
        skin_id="10708",
        quality="limited",
        online_date="2020-05-05",
        acquire_method="direct_sale",
        price_text="888",
        official_tier=3,
        quality_score=0.6,
        is_limited=True,
        is_gacha=False,
        is_direct_sale=True,
        is_event=False,
        is_battle_pass=False,
        is_shard_exchange=False,
        has_detail_record=True,
        has_primary_asset=True,
        skin_age_days=1000,
        hero_skin_count=8,
        market_signals=signals or MarketValidationSignals(),
    )


def evaluation(score: int | None = 65) -> EvaluationResult:
    return EvaluationResult(
        source_key="107-08",
        hero_name="Zhao Yun",
        skin_name="Long Dan",
        evaluation_score=score,
        official_prior_score=55,
        aspect_scores={},
        confidence=0.7,
        validation_status="evidence_validated",
        evidence_coverage=0.8,
        warnings=[],
        evidence={},
    )


class SalesDeviationTest(unittest.TestCase):
    def test_estimated_volume_can_exceed_score(self):
        result = compare_score_to_sales(
            feature_vector(),
            evaluation(65),
            [
                {
                    "platform": "sales_public",
                    "title": "sample",
                    "url": "https://example.com/sales",
                    "metrics": {
                        "estimated_sales_volume": 1_000_000,
                        "sales_volume_relation": "estimated",
                        "source_confidence": 0.5,
                    },
                }
            ],
        )

        self.assertEqual(result["sales_basis"], "estimated_sales_volume")
        self.assertEqual(result["gap_direction"], "sales_above_score")
        self.assertIn("sales_volume_is_estimated", result["warnings"])
        self.assertLess(result["gap"], 0)

    def test_rank_proxy_generates_sales_score(self):
        result = compare_score_to_sales(
            feature_vector(),
            evaluation(80),
            [
                {
                    "platform": "sales_public",
                    "title": "top ten",
                    "metrics": {"sales_rank": 1, "rank_size": 10},
                }
            ],
        )

        self.assertEqual(result["sales_score"], 100)
        self.assertEqual(result["sales_basis"], "sales_rank")
        self.assertIn("sales_score_uses_rank_proxy", result["warnings"])

    def test_missing_sales_data_is_explicit(self):
        result = compare_score_to_sales(feature_vector(), evaluation(None), [])

        self.assertEqual(result["score_basis"], "official_prior_score")
        self.assertEqual(result["gap_direction"], "insufficient_sales_data")
        self.assertIn("missing_sales_evidence", result["warnings"])

    def test_sales_blind_signals_remove_direct_sales_fields(self):
        signals = sales_blind_signals(
            MarketValidationSignals(
                visual_score=0.8,
                discussion_count=1000,
                sales_volume=1_000_000,
                avg_spend_to_obtain=88.8,
                ownership_rate=0.2,
            )
        )

        self.assertEqual(signals.visual_score, 0.8)
        self.assertEqual(signals.discussion_count, 1000)
        self.assertIsNone(signals.sales_volume)
        self.assertIsNone(signals.avg_spend_to_obtain)
        self.assertIsNone(signals.ownership_rate)

    def _cash_item(self, volume: int, confidence: float | None = 0.55) -> dict:
        resolved = {"sales_volume": volume, "volume_relation": "estimated", "confidence": confidence,
                    "attribution_method": "csv_release_window_uplift"}
        return {"title": "现金价值归因", "url": None, "cash_value_resolved": resolved}

    def test_cash_value_informs_gap_when_no_public_evidence(self):
        # Lowest-priority candidate: only wins when no public evidence exists.
        result = compare_score_to_sales(
            feature_vector(), evaluation(65), [self._cash_item(1_000_000, 0.6)]
        )

        self.assertEqual(result["sales_basis"], "cash_value_attributed")
        self.assertGreater(result["sales_score"], 0)
        self.assertIn("sales_volume_is_estimated", result["warnings"])
        self.assertLess(result["gap"], 0)

    def test_cash_value_loses_to_exact_public_volume(self):
        # Exact volume (priority 50) must outrank cash-value (priority 22).
        result = compare_score_to_sales(
            feature_vector(),
            evaluation(65),
            [
                self._cash_item(1_000_000, 0.6),
                {
                    "platform": "sales_public",
                    "title": "exact",
                    "metrics": {"sales_volume": 50_000, "sales_volume_relation": "exact"},
                },
            ],
        )

        self.assertEqual(result["sales_basis"], "sales_volume")
        self.assertNotEqual(result["sales_basis"], "cash_value_attributed")

    def test_cash_value_loses_to_aggregate_market_signal_volume(self):
        # Aggregate market_signals volume (priority 25) outranks cash-value (22).
        result = compare_score_to_sales(
            feature_vector(MarketValidationSignals(sales_volume=30_000)),
            evaluation(65),
            [self._cash_item(1_000_000, 0.6)],
        )

        self.assertEqual(result["sales_basis"], "aggregate_sales_volume")
        self.assertNotEqual(result["sales_basis"], "cash_value_attributed")

    def test_cash_value_zero_volume_is_ignored(self):
        # A zero/None attributed volume must not produce a candidate.
        result = compare_score_to_sales(
            feature_vector(), evaluation(65), [self._cash_item(0, 0.6)]
        )

        self.assertEqual(result["gap_direction"], "insufficient_sales_data")
        self.assertIsNone(result["sales_score"])

    def test_cash_value_legacy_aggregate_is_not_double_counted(self):
        # The legacy-aggregate fallback must not be re-surfaced as a cash-value
        # candidate; the aggregate path (priority 25) already handles it.
        legacy = {"sales_volume": 30_000, "volume_relation": "estimated",
                  "confidence": 0.7, "attribution_method": "legacy_aggregate"}
        result = compare_score_to_sales(
            feature_vector(),
            evaluation(65),
            [{"title": "现金价值归因", "url": None, "cash_value_resolved": legacy}],
        )

        self.assertEqual(result["gap_direction"], "insufficient_sales_data")
        self.assertIsNone(result["sales_score"])


if __name__ == "__main__":
    unittest.main()
