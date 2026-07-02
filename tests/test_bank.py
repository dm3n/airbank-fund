import unittest

import _isolate  # noqa: F401  (must precede airbank imports)

from airbank import config, diligence, outreach, pipeline, sources


def lead(**kw):
    base = {"company": "Summit Fabrication Co", "sector": "manufacturing",
            "source": "searchfunder", "revenue": 5_000_000, "ebitda": 900_000,
            "contact": "Owner"}
    base.update(kw)
    return base


class PipelineTests(unittest.TestCase):
    def test_lead_becomes_sourced_deal_with_score(self):
        state = {}
        deal = pipeline.add_lead(state, lead())
        self.assertEqual(deal["stage"], "sourced")
        self.assertGreater(deal["score"], 40)

    def test_duplicates_merge(self):
        state = {}
        self.assertIsNotNone(pipeline.add_lead(state, lead()))
        self.assertIsNone(pipeline.add_lead(state, lead(company="SUMMIT fabrication co.")))
        self.assertEqual(len(pipeline.book(state)["deals"]), 1)

    def test_stage_skip_refused(self):
        state = {}
        deal = pipeline.add_lead(state, lead())
        with self.assertRaises(ValueError):
            pipeline.advance(state, deal, "loi", "nope")

    def test_advance_one_step_and_dead_keeps_history(self):
        state = {}
        deal = pipeline.add_lead(state, lead())
        pipeline.advance(state, deal, "contacted", "sent")
        pipeline.advance(state, deal, "dead", "no reply")
        self.assertEqual(deal["stage"], "dead")
        self.assertGreaterEqual(len(deal["history"]), 3)

    def test_fit_score_respects_mandate(self):
        mandate = {"sectors": ["software"], "size_min": 1e6, "size_max": 1e7}
        good = pipeline.fit_score(lead(sector="software"), mandate)
        off = pipeline.fit_score(lead(sector="restaurants", revenue=5e8), mandate)
        self.assertGreater(good, off + 30)

    def test_find_by_id_or_company(self):
        state = {}
        deal = pipeline.add_lead(state, lead())
        self.assertEqual(pipeline.find(state, deal["id"][:8])["id"], deal["id"])
        self.assertEqual(pipeline.find(state, "summit")["id"], deal["id"])


class SourcesTests(unittest.TestCase):
    def test_demo_slot_never_repeats(self):
        state = {}
        first = sources.demo_leads(state)
        second = sources.demo_leads(state)   # same slot -> nothing new
        self.assertEqual(second, [])
        for l in first:
            self.assertTrue(l["company"] and l["source"])

    def test_inbox_ingests_json_and_marks_done(self):
        import json
        sources.LEADS_INBOX.mkdir(parents=True, exist_ok=True)
        path = sources.LEADS_INBOX / "searchfunder-batch.json"
        path.write_text(json.dumps([lead(company="Inbox Test Co")]))
        leads = sources.inbox_leads()
        self.assertEqual(leads[0]["company"], "Inbox Test Co")
        self.assertFalse(path.exists())
        self.assertTrue(path.with_suffix(".json.done").exists())

    def test_bad_inbox_file_never_crashes(self):
        sources.LEADS_INBOX.mkdir(parents=True, exist_ok=True)
        bad = sources.LEADS_INBOX / "garbage.json"
        bad.write_text("{not json")
        self.assertEqual(sources.inbox_leads(), [])
        self.assertTrue(bad.with_suffix(".json.error").exists())


class DiligenceTests(unittest.TestCase):
    def _months(self, revs):
        return [{"month": f"m{i}", "revenue": r, "cogs": r * 0.5, "opex": r * 0.3}
                for i, r in enumerate(revs)]

    def test_growth_and_margins_computed(self):
        m = diligence.metrics(self._months([100] * 6 + [120] * 6))
        self.assertAlmostEqual(m["growth_h2_vs_h1"], 0.2, places=2)
        self.assertAlmostEqual(m["gross_margin_avg"], 0.5, places=2)
        self.assertAlmostEqual(m["ebitda_margin_avg"], 0.2, places=2)

    def test_decline_flags_and_lowers_score(self):
        growing = diligence.metrics(self._months([100] * 6 + [130] * 6))
        shrinking = diligence.metrics(self._months([130] * 6 + [90] * 6))
        self.assertIn("revenue declining half-over-half", shrinking["flags"])
        self.assertLess(shrinking["score"], growing["score"])

    def test_memo_grounded_pipeline(self):
        state = {}
        deal = pipeline.add_lead(state, lead())
        diligence.generate_demo_financials(deal)
        m = diligence.run(state, deal, "pre_loi",
                          runner=lambda p: "# Memo\n\n## Verdict\nproceed")
        self.assertIsNotNone(m)
        self.assertIn("pre_loi", deal["diligence"])

    def test_no_docs_no_memo(self):
        state = {}
        deal = pipeline.add_lead(state, lead(company="Docless Co"))
        m = diligence.run(state, deal, "pre_loi", runner=lambda p: "# Memo")
        self.assertIsNone(m)

    def test_llm_failure_means_deal_waits(self):
        state = {}
        deal = pipeline.add_lead(state, lead(company="Waits Co"))
        diligence.generate_demo_financials(deal)

        def boom(prompt):
            raise RuntimeError("down")
        self.assertIsNone(diligence.run(state, deal, "pre_loi", runner=boom))
        self.assertEqual(deal["diligence"], {})


class OutreachTests(unittest.TestCase):
    def test_draft_requires_structure(self):
        with self.assertRaises(RuntimeError):
            outreach.draft(lead() | {"id": "x"}, runner=lambda p: "hi there")

    def test_daily_cap_enforced_in_code(self):
        state = {}
        for _ in range(config.CAPS["max_outreach_per_day"]):
            self.assertTrue(outreach.under_daily_cap(state))
            outreach.count_send(state)
        self.assertFalse(outreach.under_daily_cap(state))

    def test_simulated_response_deterministic(self):
        deal = {"id": "abc-1234", "touches": 1, "score": 80}
        self.assertEqual(outreach.simulate_response(deal),
                         outreach.simulate_response(deal))

    def test_send_writes_outbox(self):
        deal = {"id": "abc-1234", "company": "Outbox Co", "contact": "Owner"}
        path = outreach.send(deal, "TOUCH 1:\nhello", 1)
        try:
            self.assertIn("Outbox Co", path.read_text())
        finally:
            path.unlink(missing_ok=True)


class LoopSimulationTests(unittest.TestCase):
    def test_full_simulation_cycle_runs_clean(self):
        from airbank import loop, outreach as outreach_mod
        original = outreach_mod.draft
        outreach_mod.draft = lambda deal, runner=None: "TOUCH 1:\nhi\nTOUCH 2:\nhi\nTOUCH 3:\nhi"
        try:
            import airbank.diligence as dd
            orig_claude = dd._claude
            dd._claude = lambda p: "# Memo\n\n## Verdict\nproceed"
            score, cycle = loop.run_cycle()
            dd._claude = orig_claude
            self.assertGreaterEqual(score, 0.6)
            self.assertEqual(cycle["errors"], [])
        finally:
            outreach_mod.draft = original


if __name__ == "__main__":
    unittest.main()
