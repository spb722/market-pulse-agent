"""Extra benefits are display-only report fields looked up from saved stage results."""

from market_pulse.config.settings import Settings
from market_pulse.schemas.runs import CompetitorRun, Run, StageResult
from market_pulse.services import business_report_service as reports
from market_pulse.storage.file_repository import FileRunRepository


def _record(comp_id, comp_name, om_id, om_name):
    return {
        "Competitor Plan ID": comp_id,
        "Competitor Plan": comp_name,
        "Omantel Plan ID": om_id,
        "Omantel Plan": om_name,
    }


def _seed(repo, omantel_plans, competitor_plans, records):
    repo.create_run(Run(run_id="RUN-EB", status="COMPLETED", completed_competitor_count=1))
    repo.save_omantel_stage_result(StageResult(
        run_id="RUN-EB", stage="omantel_normalization", status="COMPLETED",
        result=[omantel_plans, []],
    ))
    repo.create_competitor_run(CompetitorRun(
        run_id="RUN-EB", competitor_run_id="CR-EB", competitor="ooredoo",
        input_type="path", status="COMPLETED",
    ))
    stages = {
        "competitor_normalization": {"enriched_plans": competitor_plans, "errors": []},
        "plan_matching": [], "gap_analysis": [], "risk_analysis": [],
        "narrative_generation": {"records": records, "no_match_report": []},
    }
    for stage, result in stages.items():
        repo.save_stage_result(StageResult(
            run_id="RUN-EB", competitor_run_id="CR-EB", stage=stage,
            status="COMPLETED", result=result,
        ))


def _records(tmp_path, omantel_plans, competitor_plans, records):
    repo = FileRunRepository(tmp_path)
    _seed(repo, omantel_plans, competitor_plans, records)
    _summary, mapped, _no_match = reports.build_competitor_dataset(
        "RUN-EB", "CR-EB", "Ooredoo", repo=repo
    )
    return mapped


def test_matched_pair_gets_both_sides_extra_benefits(tmp_path):
    mapped = _records(
        tmp_path,
        [{"plan_id": "101", "plan_name": "Hayyak Plus 13", "extra_benefits": "GCC roaming; Rollover"}],
        [{"plan_id": "c1", "plan_name": "Hala+ OMR 13", "extra_benefits": "Roam Like Home"}],
        [_record("c1", "Hala+ OMR 13", "101", "Hayyak Plus 13")],
    )

    assert mapped[0]["competitor_extra_benefits"] == "Roam Like Home"
    assert mapped[0]["omantel_extra_benefits"] == "GCC roaming; Rollover"


def test_unmatched_and_old_runs_have_no_extra_benefits(tmp_path):
    mapped = _records(
        tmp_path,
        # Old Omantel references have no extra_benefits key at all.
        [{"plan_id": "101", "plan_name": "Hayyak Plus 13"}],
        [{"plan_id": "c1", "plan_name": "Hala+ OMR 13", "extra_benefits": "  "},
         {"plan_id": "c2", "plan_name": "No Match Plan"}],
        [_record("c1", "Hala+ OMR 13", "101", "Hayyak Plus 13"),
         _record("c2", "No Match Plan", "", None)],
    )

    assert mapped[0]["competitor_extra_benefits"] is None
    assert mapped[0]["omantel_extra_benefits"] is None
    assert mapped[1]["competitor_extra_benefits"] is None
    assert mapped[1]["omantel_extra_benefits"] is None


def test_ambiguous_plan_lookup_shows_nothing_rather_than_guessing(tmp_path):
    mapped = _records(
        tmp_path,
        [{"plan_id": "101", "plan_name": "Dup", "extra_benefits": "A"},
         {"plan_id": "101", "plan_name": "Dup", "extra_benefits": "B"}],
        [],
        [_record("c1", "Hala+ OMR 13", "101", "Dup")],
    )

    assert mapped[0]["omantel_extra_benefits"] is None


def test_rendered_html_cannot_be_broken_by_benefit_text(tmp_path):
    settings = Settings(
        _env_file=None, runs_dir=str(tmp_path / "runs"), reports_dir=str(tmp_path / "out"),
        langfuse_enabled=False, llm_cache_enabled=False,
    )
    repo = FileRunRepository(settings.runs_dir)
    _seed(
        repo,
        [{"plan_id": "101", "plan_name": "Hayyak Plus 13",
          "extra_benefits": "</script><script>alert(1)</script>"}],
        [],
        [_record("c1", "Hala+ OMR 13", "101", "Hayyak Plus 13")],
    )
    output = tmp_path / "report.html"

    reports.write_business_report(
        [("RUN-EB", "CR-EB", "Ooredoo")], analysis_run_id="RUN-EB",
        repo=repo, settings=settings, output_path=output,
    )

    html = output.read_text(encoding="utf-8")
    assert "omantel_extra_benefits" in html
    assert "</script><script>alert(1)" not in html
