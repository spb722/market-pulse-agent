"""Offline report API/CLI tests: real HTML rendering, no LLM or Redis calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

from market_pulse.api.app import create_app
from market_pulse.api.routes import get_repository
from market_pulse.config.settings import Settings, get_settings
from market_pulse.schemas.runs import CompetitorRun, ReportJob, Run, StageResult, utcnow
from market_pulse.services import business_report_service as reports
from market_pulse.storage.file_repository import FileRunRepository


def seed_completed_run(repo, run_id="RUN-TEST"):
    repo.create_run(Run(run_id=run_id, status="COMPLETED", completed_competitor_count=2))
    repo.save_omantel_stage_result(StageResult(
        run_id=run_id, stage="omantel_normalization", status="COMPLETED",
        result=[[{"plan_id": "OM-1"}], []],
    ))
    for cr_id, name in [("CR-OO", "ooredoo"), ("CR-VF", "vodafone")]:
        repo.create_competitor_run(CompetitorRun(
            run_id=run_id, competitor_run_id=cr_id, competitor=name,
            input_type="path", status="COMPLETED",
        ))
        stages = {
            "competitor_normalization": {"enriched_plans": [], "errors": []},
            "plan_matching": [], "gap_analysis": [], "risk_analysis": [],
            "narrative_generation": {"records": [], "no_match_report": []},
        }
        for stage, result in stages.items():
            repo.save_stage_result(StageResult(
                run_id=run_id, competitor_run_id=cr_id, stage=stage,
                status="COMPLETED", result=result,
            ))


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        _env_file=None, runs_dir=str(tmp_path / "inputs"), reports_dir=str(tmp_path / "outputs"),
        langfuse_enabled=False, llm_cache_enabled=False,
    )
    repo = FileRunRepository(settings.runs_dir)
    seed_completed_run(repo)
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_settings] = lambda: settings
    # Avoid application logging setup writing to the real project logs.
    return TestClient(app), repo, settings


def test_generate_returns_json_then_status_exposes_absolute_path(setup):
    client, repo, settings = setup
    before = {p: p.read_bytes() for p in Path(repo.root).rglob("stages/*.json")}
    response = client.post("/runs/RUN-TEST/report")
    assert response.status_code == 202
    assert response.headers["content-type"] == "application/json"
    assert response.json()["report_status"] == "PROCESSING"
    assert response.json()["report_path"] is None
    status = client.get("/runs/RUN-TEST").json()
    assert status["status"] == "COMPLETED"
    assert status["report_status"] == "COMPLETED"
    assert status["report_error"] is None
    path = Path(status["report_path"])
    assert path == Path(settings.reports_dir) / "RUN-TEST/market_pulse_business_report.html"
    assert path.is_absolute() and path.is_file()
    html = path.read_text()
    assert "/*__MARKET_PULSE_DATA__*/null" not in html
    assert '"run_id": "RUN-TEST"' in html
    assert '"name": "Ooredoo"' in html and '"name": "Vodafone"' in html
    assert "section-executive-decisions" in html
    assert len(status["competitors"]) == 2
    assert repo.get_portfolio_analysis("RUN-TEST")["competitor_run_ids"] == ["CR-OO", "CR-VF"]
    assert repo.get_report_job("RUN-TEST").completed_at is not None
    assert all(p.read_bytes() == content for p, content in before.items())


def test_status_is_read_only_before_generation(setup):
    client, repo, settings = setup
    data = client.get("/runs/RUN-TEST").json()
    assert data["report_status"] == "NOT_GENERATED"
    assert data["report_path"] is None
    assert not Path(settings.reports_dir).exists()
    assert not (repo.root / "RUN-TEST/report.json").exists()


def test_unknown_run_returns_404(setup):
    client, repo, _ = setup
    assert client.post("/runs/RUN-MISSING/report").status_code == 404
    assert not (repo.root / "RUN-MISSING").exists()


def test_no_competitors_returns_409(setup):
    client, repo, _ = setup
    repo.create_run(Run(run_id="RUN-EMPTY"))
    assert client.post("/runs/RUN-EMPTY/report").status_code == 409
    assert repo.get_report_job("RUN-EMPTY").report_status == "NOT_GENERATED"


@pytest.mark.parametrize("status", ["CREATED", "PROCESSING", "FAILED"])
def test_unfinished_competitor_returns_409(setup, status):
    client, repo, _ = setup
    cr = repo.get_competitor_run("RUN-TEST", "CR-OO")
    cr.status = status
    repo.save_competitor_run(cr)
    response = client.post("/runs/RUN-TEST/report")
    assert response.status_code == 409
    assert "CR-OO" in response.json()["detail"]
    assert repo.get_report_job("RUN-TEST").report_status == "NOT_GENERATED"


@pytest.mark.parametrize("status", ["PENDING", "PROCESSING", "FAILED"])
def test_unfinished_omantel_returns_409(setup, status):
    client, repo, _ = setup
    stage = repo.get_omantel_stage_result("RUN-TEST")
    stage.status = status
    repo.save_omantel_stage_result(stage)
    response = client.post("/runs/RUN-TEST/report")
    assert response.status_code == 409
    assert "Omantel" in response.json()["detail"]


def test_missing_stage_rejected_even_with_completed_competitor(setup):
    client, repo, _ = setup
    (repo.root / "RUN-TEST/competitors/CR-OO/stages/risk_analysis.json").unlink()
    response = client.post("/runs/RUN-TEST/report")
    assert response.status_code == 409
    assert "risk_analysis" in response.json()["detail"]


def test_duplicate_request_does_not_start_another_build(setup, monkeypatch):
    client, repo, _ = setup
    job, lock = reports.reserve_report_generation("RUN-TEST", repo)
    assert lock is not None
    def must_not_run(*args, **kwargs):
        pytest.fail("Duplicate build started")
    monkeypatch.setattr("market_pulse.api.routes.generate_run_report", must_not_run)
    try:
        response = client.post("/runs/RUN-TEST/report")
        assert response.status_code == 202
        assert response.json()["report_status"] == "PROCESSING"
        assert response.json()["report_path"] is None
        assert repo.get_report_job("RUN-TEST").started_at == job.started_at
    finally:
        lock.close()


def test_concurrent_reservations_admit_one_build(setup):
    _, repo, _ = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: reports.reserve_report_generation("RUN-TEST", repo), range(4)))
    locks = [lock for _, lock in results if lock is not None]
    try:
        assert len(locks) == 1
    finally:
        for lock in locks:
            lock.close()


def test_failed_build_records_safe_error_and_can_retry(setup, monkeypatch):
    client, repo, _ = setup
    with monkeypatch.context() as patcher:
        patcher.setattr(reports, "TEMPLATE_PATH", Path("/not-a-real-report-template.html"))
        assert client.post("/runs/RUN-TEST/report").status_code == 202
    failed = client.get("/runs/RUN-TEST").json()
    assert failed["status"] == "COMPLETED"
    assert failed["report_status"] == "FAILED"
    assert failed["report_path"] is None
    assert "not-a-real" not in failed["report_error"]
    assert failed["report_error"]
    assert client.post("/runs/RUN-TEST/report").status_code == 202
    assert client.get("/runs/RUN-TEST").json()["report_status"] == "COMPLETED"


def test_interrupted_job_can_be_retried_after_lock_is_released(setup):
    client, repo, _ = setup
    repo.save_report_job(ReportJob(run_id="RUN-TEST", report_status="PROCESSING", started_at=utcnow()))
    assert client.post("/runs/RUN-TEST/report").status_code == 202
    assert repo.get_report_job("RUN-TEST").report_status == "COMPLETED"


def test_reports_are_isolated_per_run(setup):
    client, repo, _ = setup
    seed_completed_run(repo, "RUN-OTHER")
    client.post("/runs/RUN-TEST/report")
    first = Path(repo.get_report_job("RUN-TEST").report_path)
    content = first.read_bytes()
    client.post("/runs/RUN-OTHER/report")
    second = Path(repo.get_report_job("RUN-OTHER").report_path)
    assert first != second and second.is_file()
    assert first.read_bytes() == content


def test_failed_atomic_publish_preserves_previous_html(setup, monkeypatch):
    client, repo, settings = setup
    client.post("/runs/RUN-TEST/report")
    path = Path(repo.get_report_job("RUN-TEST").report_path)
    previous = path.read_bytes()
    replace = reports.os.replace
    def fail_html_publish(source, destination):
        if Path(destination).suffix == ".html":
            raise OSError("Simulated HTML write failure")
        return replace(source, destination)
    monkeypatch.setattr(reports.os, "replace", fail_html_publish)
    client.post("/runs/RUN-TEST/report")
    assert repo.get_report_job("RUN-TEST").report_status == "FAILED"
    assert repo.get_report_job("RUN-TEST").report_path is None
    assert path.read_bytes() == previous
    assert not list(Path(settings.reports_dir).rglob("*.tmp"))


def test_cli_run_id_uses_shared_generator(setup, monkeypatch, capsys):
    from scripts import generate_business_report as cli
    _, repo, settings = setup
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["generate_business_report.py", "--run-id", "RUN-TEST"])
    cli.main()
    job = repo.get_report_job("RUN-TEST")
    assert job.report_status == "COMPLETED"
    assert job.report_path in capsys.readouterr().out


@pytest.mark.parametrize("explicit", [True, False])
def test_legacy_cli_selections_still_generate_shared_file(setup, monkeypatch, explicit):
    from scripts import generate_business_report as cli
    _, repo, settings = setup
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    specs = [("RUN-TEST", "CR-OO", "Ooredoo")]
    monkeypatch.setattr(cli, "DEFAULT_RUNS", specs)
    args = ["generate_business_report.py"]
    if explicit:
        args += ["--run", ":".join(specs[0])]
    monkeypatch.setattr(sys, "argv", args)
    cli.main()
    assert (Path(settings.reports_dir) / "market_pulse_business_report.html").is_file()


def test_invalid_run_id_cannot_write_outside_storage(setup):
    _, repo, _ = setup
    with pytest.raises(ValueError, match="Invalid run ID"):
        reports.reserve_report_generation("../escape", repo)


def test_openapi_has_post_report_but_no_html_route(setup):
    client, _, _ = setup
    path = client.get("/openapi.json").json()["paths"]["/runs/{run_id}/report"]
    assert set(path) == {"post"}
    assert "202" in path["post"]["responses"]


def test_shared_renderer_preserves_scores_and_batches_advice(setup):
    from market_pulse.schemas.portfolio import PortfolioRecommendation, PortfolioSegmentAdvice
    _, repo, settings = setup
    for cr_id in ["CR-OO", "CR-VF"]:
        narrative = repo.get_stage_result("RUN-TEST", cr_id, "narrative_generation")
        narrative.result["records"] = [{
            "Competitor Plan ID": cr_id, "Competitor Plan": "Test pack",
            "Omantel Plan ID": "OM-1", "Omantel Plan": "</script><script>unsafe()</script>",
            "Category": "prepaid", "Product Type": "COMBO", "Similarity": 0.9,
            "Gap Analysis Status": "ANALYZED", "Risk Status": "SCORED",
            "Risk Score": 9.5, "Risk Level": "MEDIUM",
            "VOICE Gap %": -20, "VOICE Position": "COMPETITOR_ADVANTAGE",
        }]
        repo.save_stage_result(narrative)
    calls = []
    def advisor(facts):
        calls.append(facts)
        return PortfolioSegmentAdvice(
            segment_summary="Review the measured voice gap.",
            recommendations=[PortfolioRecommendation(
                omantel_plan_id="OM-1", decision="ENHANCE", suggested_action="Review voice allowance."
            )],
        )
    path = Path(settings.reports_dir) / "sample.html"
    dataset = reports.write_business_report(
        reports.discover_run_specs("RUN-TEST", repo), analysis_run_id="RUN-TEST", repo=repo,
        settings=settings, output_path=path, advisor=advisor,
    )
    assert len(calls) == 1
    assert len(calls[0]["plans"]) == 1
    assert len(calls[0]["plans"][0]["comparisons"]) == 2
    assert [r["risk_score"] for r in dataset["records"]] == [9.5, 9.5]
    assert dataset["portfolio_analysis"]["rows"][0]["suggested_action"] == "Review voice allowance."
    assert "</script><script>unsafe()" not in path.read_text()
    assert "\\u003c/script>" in path.read_text()


def test_tracing_flush_called_even_when_render_fails(setup, monkeypatch):
    _, repo, settings = setup
    calls = []
    monkeypatch.setattr(reports, "flush_langfuse", lambda settings: calls.append(settings))
    monkeypatch.setattr(reports, "TEMPLATE_PATH", Path("/missing-template.html"))
    job = reports.generate_run_report("RUN-TEST", repo, settings)
    assert job.report_status == "FAILED"
    assert calls == [settings]
