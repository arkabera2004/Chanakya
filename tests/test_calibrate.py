"""Learning loop: weight proposals from BD labels, win/loss summary, win-history bonus.

Every notice, label and outcome in this file is SYNTHETIC. The data is generated
here from a fixed seed (ids start with ``SYN-``) so the right answer is known. No
real tender, buyer or BD decision is represented.
"""

from __future__ import annotations

import random

import pytest
import yaml
from sqlalchemy import select
from typer.testing import CliRunner

from chanakya.app import run_pipeline
from chanakya.cli import app as cli_app
from chanakya.config import CalibrationSettings, ScoringSettings
from chanakya.db import Opportunity, UserReviewFeedback
from chanakya.llm import FakeProvider
from chanakya.models import EligibilityStatus, FilterDecision
from chanakya.pipeline.calibrate import (
    Notice, band_for, calibrate_notices, derive_label, fit_logistic, format_report, load_notices,
    project_weights, proposal_yaml, run_calibration, total_for,
)
from chanakya.pipeline.scoring import ScoreInput, Scorer
from chanakya.pipeline.winloss import buyer_win_count, format_winloss, winloss_summary
from chanakya.timeutil import now_utc

from .conftest import ROOT, sample_records, write_records

KEYS = ["capability", "eligibility", "technical", "deadline", "commercial", "strategic"]
CAL = CalibrationSettings(min_labels=100, min_labels_per_class=10)


def synthetic_scores(rng: random.Random) -> dict[str, float]:
    """SYNTHETIC component scores. Capability stays at or above 0.5 so the relevance gate is 1."""
    return {"capability": round(rng.uniform(0.5, 1.0), 3), "eligibility": rng.choice([0.1, 0.45, 0.5, 0.7, 1.0]),
            "technical": rng.choice([0.2, 0.45, 0.5, 0.65, 0.7, 1.0]), "deadline": rng.choice([0.15, 0.4, 0.75, 1.0]),
            "commercial": rng.choice([0.25, 0.4, 0.6, 0.8, 1.0]), "strategic": rng.choice([0.3, 0.5, 0.8, 1.0])}


def synthetic_notices(n: int, rule, seed: int = 7, unlabelled: int = 0) -> list[Notice]:
    """SYNTHETIC labelled notices: ``rule(scores, rng)`` returns the label (1 relevant, 0 irrelevant)."""
    rng = random.Random(seed)
    out = []
    for i in range(n + unlabelled):
        scores = synthetic_scores(rng)
        label = rule(scores, rng) if i < n else None
        out.append(Notice(opportunity_id=f"SYN-{seed}-{i:04d}", scores=scores, fit_scores=dict(scores),
                          label=label, label_source="label: synthetic" if label is not None else None))
    return out


def by_commercial(scores, _rng) -> int:
    return int(scores["commercial"] >= 0.6)


def by_deadline(scores, _rng) -> int:
    return int(scores["deadline"] >= 0.75)


def by_current_total(scores, rng) -> int:
    """Labels that agree with the default weights, with a little noise."""
    total = total_for(scores, ScoringSettings().weights)
    return int(total + rng.gauss(0, 4) >= 68)


def assert_valid(weights: dict) -> None:
    assert list(weights) == KEYS
    assert all(isinstance(w, int) and w >= 0 for w in weights.values())
    assert sum(weights.values()) == 100
    ScoringSettings(weights=weights)      # accepted by the strict config model


# ---------------------------------------------------------------- labels
def test_label_rules_explicit_label_wins_and_undecided_statuses_are_ignored():
    assert derive_label("relevant", "Rejected") == (1, "label: relevant")
    assert derive_label("irrelevant", "Pursuing") == (0, "label: irrelevant")
    for status in ("Shortlisted", "Qualified", "Partner needed", "Pursuing", "Won", "Lost"):
        assert derive_label(None, status)[0] == 1, status
    assert derive_label(None, "Rejected")[0] == 0
    for status in ("New", "Reviewing", "Expired", None):
        assert derive_label(None, status) == (None, None), status


# ---------------------------------------------------------------- the fit
def test_fit_recovers_direction_and_keeps_coefficients_non_negative():
    rng = random.Random(3)
    X = [[rng.random(), rng.random()] for _ in range(200)]      # SYNTHETIC
    y = [int(a - b > 0) for a, b in X]                          # first helps, second hurts
    fit = fit_logistic(X, y, l2=1.0)
    assert fit.converged
    assert fit.coef[0] > 1.0
    assert fit.coef[1] == 0.0                                   # would be negative; held at the bound


def test_fit_with_strong_penalty_stays_at_the_prior():
    rng = random.Random(4)
    X = [[rng.random(), rng.random()] for _ in range(60)]       # SYNTHETIC
    y = [int(a > 0.5) for a, _ in X]
    fit = fit_logistic(X, y, l2=1e6, prior=[2.0, 3.0])
    assert fit.coef == pytest.approx([2.0, 3.0], abs=1e-3)


def test_project_weights_is_exact_and_handles_edges():
    assert project_weights([1, 1, 1], ["a", "b", "c"]) == {"a": 34, "b": 33, "c": 33}
    assert project_weights([-5, 3, 1], ["a", "b", "c"]) == {"a": 0, "b": 75, "c": 25}
    assert project_weights([0, 0, 0], ["a", "b", "c"]) is None
    assert project_weights([-1, -2, 0], ["a", "b", "c"]) is None


# ---------------------------------------------------------------- proposals with a known answer
@pytest.mark.parametrize("rule,driver", [(by_commercial, "commercial"), (by_deadline, "deadline")])
def test_labels_driven_by_one_component_move_weight_to_it(rule, driver):
    rep = calibrate_notices(synthetic_notices(240, rule), ScoringSettings(), CAL)
    assert rep.status == "proposal", rep.reasons
    assert_valid(rep.proposed_weights)
    assert rep.proposed_weights[driver] > 40                    # from 10
    assert rep.movers[0]["key"] == driver and rep.movers[0]["delta"] > 0
    assert rep.proposed_weights[driver] == max(rep.proposed_weights.values())
    # the proposal ranks the labelled set better than the current weights do
    assert rep.proposed_metrics["precision"] > rep.current_metrics["precision"]
    assert rep.cv_metrics is not None and rep.cv_metrics["precision"] > rep.current_metrics["precision"]
    assert rep.labelled_band_changes > 0 and rep.book_band_changes > 0


def test_labels_that_agree_with_current_weights_keep_capability_on_top():
    rep = calibrate_notices(synthetic_notices(300, by_current_total), ScoringSettings(), CAL)
    assert rep.status in ("proposal", "no_change")
    assert_valid(rep.proposed_weights)
    assert rep.proposed_weights["capability"] == max(rep.proposed_weights.values())
    assert rep.fit["scale"] > 0 and rep.fit["converged"]


def test_report_counts_classes_and_states_the_limits():
    notices = synthetic_notices(240, by_commercial, unlabelled=30)
    rep = calibrate_notices(notices, ScoringSettings(), CAL)
    assert rep.n_scored == 270 and rep.n_labelled == 240
    assert rep.n_positive + rep.n_negative == 240 and rep.n_positive == sum(n.label or 0 for n in notices)
    text = format_report(rep)
    assert "PROPOSAL (not applied)" in text
    assert f"({rep.n_positive} relevant, {rep.n_negative} irrelevant)" in text
    assert "Largest movers: commercial" in text
    assert "Reading: Held out, P1/P2 precision moves from" in text and "A human decides." in text
    assert "Selection bias" in text and "Small sample" in text and "set aside" in text
    assert "chanakya analyze --scope all" in text and "chanakya benchmark" in text
    assert chr(0x2013) not in text and chr(0x2014) not in text      # no en or em dashes
    text.encode("ascii")                                         # prints on any Windows console


def test_component_without_variation_is_called_out():
    def flat_eligibility(scores, rng):
        return by_commercial(scores, rng)
    notices = [Notice(n.opportunity_id, {**n.scores, "eligibility": 0.5}, {**n.fit_scores, "eligibility": 0.5},
                      label=n.label, label_source=n.label_source)
               for n in synthetic_notices(200, flat_eligibility)]
    rep = calibrate_notices(notices, ScoringSettings(), CAL)
    assert any("No variation" in note and "eligibility" in note for note in rep.notes)
    row = next(c for c in rep.components if c["key"] == "eligibility")
    assert row["varies"] is False


# ---------------------------------------------------------------- refusals
def test_refuses_below_minimum_label_count_and_says_why():
    rep = calibrate_notices(synthetic_notices(60, by_commercial), ScoringSettings(), CAL)
    assert rep.status == "refused" and rep.proposed_weights is None
    assert any("Only 60 labelled notices" in r and "100" in r for r in rep.reasons)
    text = format_report(rep)
    assert "NO PROPOSAL" in text and "Only 60 labelled notices" in text and "Selection bias" in text
    assert proposal_yaml(rep) is None
    # the same data is accepted when a human lowers the minimum on purpose
    assert calibrate_notices(synthetic_notices(60, by_commercial), ScoringSettings(),
                             CalibrationSettings(min_labels=50)).status == "proposal"


@pytest.mark.parametrize("label,missing", [(1, "irrelevant"), (0, "relevant")])
def test_refuses_when_one_class_is_missing(label, missing):
    rep = calibrate_notices(synthetic_notices(150, lambda s, r: label), ScoringSettings(), CAL)
    assert rep.status == "refused" and rep.proposed_weights is None
    assert any(f"No {missing}" in r for r in rep.reasons)


def test_refuses_when_one_class_is_nearly_missing():
    notices = synthetic_notices(150, lambda s, r: 0)
    notices[:3] = [Notice(n.opportunity_id, n.scores, n.fit_scores, label=1, label_source="label: synthetic")
                   for n in notices[:3]]
    rep = calibrate_notices(notices, ScoringSettings(), CAL)
    assert rep.status == "refused"
    assert any("smaller class has 3" in r for r in rep.reasons)


def test_refuses_when_labels_are_noise():
    rep = calibrate_notices(synthetic_notices(160, lambda s, r: int(r.random() < 0.5), seed=5), ScoringSettings(), CAL)
    assert rep.status == "refused" and rep.proposed_weights is None
    assert any("better than chance" in r for r in rep.reasons)
    assert "better than chance" in format_report(rep)


def test_verdict_says_keep_current_weights_when_held_out_precision_does_not_improve():
    from chanakya.pipeline.calibrate import CalibrationReport, _verdict
    worse = CalibrationReport(status="proposal", current_metrics={"precision": 0.7, "recall": 0.9},
                              cv_metrics={"precision": 0.65, "recall": 0.9})
    assert "Keep the current weights" in _verdict(worse)
    traded = CalibrationReport(status="proposal", current_metrics={"precision": 0.6, "recall": 0.9},
                               cv_metrics={"precision": 0.8, "recall": 0.5})
    assert "bought with recall" in _verdict(traded)
    unchecked = CalibrationReport(status="proposal", current_metrics={"precision": 0.6, "recall": 0.9})
    assert "Do not accept it on that alone" in _verdict(unchecked)


def test_no_labels_at_all_is_a_report_not_an_error():
    rep = calibrate_notices([], ScoringSettings(), CAL)
    assert rep.status == "refused" and rep.n_labelled == 0
    assert "NO PROPOSAL" in format_report(rep)


# ---------------------------------------------------------------- validity and determinism
@pytest.mark.parametrize("seed", range(8))
def test_weights_are_always_valid(seed):
    rules = [by_commercial, by_deadline, by_current_total,
             lambda s, r: int(r.random() < 0.5),                                  # pure noise
             lambda s, r: int(s["capability"] < 0.7),                             # runs against capability
             lambda s, r: int(s["strategic"] + s["technical"] > 1.2),
             lambda s, r: int(s["commercial"] < 0.5 and s["deadline"] < 0.5),     # runs against two components
             lambda s, r: int(total_for(s, ScoringSettings().weights) < 60)]      # runs against everything
    rep = calibrate_notices(synthetic_notices(160, rules[seed], seed=seed + 20), ScoringSettings(), CAL)
    if rep.status == "refused":
        assert rep.proposed_weights is None and rep.reasons
    else:
        assert_valid(rep.proposed_weights)
        assert yaml.safe_load(proposal_yaml(rep))["scoring"]["weights"] == rep.proposed_weights


def test_non_default_weight_keys_and_sums_are_respected():
    scoring = ScoringSettings(weights={"capability": 7, "eligibility": 4, "technical": 3, "deadline": 2,
                                       "commercial": 2, "strategic": 2})    # sums to 20
    rep = calibrate_notices(synthetic_notices(200, by_deadline), scoring, CAL)
    assert rep.current_weights == {"capability": 35, "eligibility": 20, "technical": 15, "deadline": 10,
                                   "commercial": 10, "strategic": 10}
    assert_valid(rep.proposed_weights)
    assert any("sum to 20" in n for n in rep.notes)


def test_same_input_gives_the_same_proposal_whatever_the_order():
    notices = synthetic_notices(220, by_current_total, unlabelled=20)
    a = calibrate_notices(notices, ScoringSettings(), CAL)
    b = calibrate_notices(list(notices), ScoringSettings(), CAL)
    shuffled = list(notices)
    random.Random(99).shuffle(shuffled)
    c = calibrate_notices(shuffled, ScoringSettings(), CAL)
    assert a.proposed_weights == b.proposed_weights == c.proposed_weights
    assert a.fit == b.fit == c.fit
    assert format_report(a) == format_report(b) == format_report(c)
    assert proposal_yaml(a) == proposal_yaml(c)


# ---------------------------------------------------------------- database: read-only
def add_synthetic_book(session, n: int, rule, seed: int = 11) -> None:
    """SYNTHETIC opportunities with stored breakdowns and BD feedback (half labels, half statuses)."""
    weights = ScoringSettings().weights
    thresholds = ScoringSettings().priority_thresholds
    for i, note in enumerate(synthetic_notices(n, rule, seed=seed)):
        total = total_for(note.scores, weights)
        prio = band_for(note, weights, thresholds, respect_status=True)
        status = "New"
        if i % 2:
            status = "Shortlisted" if note.label else "Rejected"
        session.add(Opportunity(
            id=note.opportunity_id, dedup_key=f"syn:{seed}:{i}", source_id="synthetic", platform="Synthetic portal",
            source_record_key=str(i), title=f"SYNTHETIC notice {i}", organisation=f"Synthetic Buyer {i % 5}",
            content_hash="0" * 64, filter_decision="candidate", status="active", relevance_score=total,
            priority=prio, review_status=status, top_capabilities=[{"id": "CAP-CV-VA", "score": 0.8}],
            score_breakdown={"total": total, "priority": prio, "flags": [],
                             "components": {k: {"score": v, "reason": "synthetic"} for k, v in note.scores.items()}}))
        if i % 2 == 0:
            session.add(UserReviewFeedback(opportunity_id=note.opportunity_id, reviewer="synthetic",
                                           relevance_label="relevant" if note.label else "irrelevant"))
        else:
            session.add(UserReviewFeedback(opportunity_id=note.opportunity_id, reviewer="synthetic",
                                           review_status=status))
    session.commit()


def snapshot(session) -> list[tuple]:
    session.expire_all()
    return [(o.id, o.relevance_score, o.priority, repr(o.score_breakdown), o.review_status)
            for o in session.scalars(select(Opportunity).order_by(Opportunity.id))]


def test_run_calibration_reads_labels_and_statuses_and_changes_nothing(settings, session):
    add_synthetic_book(session, 200, by_commercial)
    before = snapshot(session)
    feedback_before = len(session.scalars(select(UserReviewFeedback)).all())
    rep = run_calibration(session, settings)
    assert rep.status == "proposal"
    assert rep.n_labelled == 200 and rep.proposed_weights["commercial"] > 40
    assert set(rep.label_sources) == {"label: relevant", "label: irrelevant", "status: Shortlisted", "status: Rejected"}
    assert rep.stored_bands_reproduced == rep.stored_bands_compared == 200
    assert not session.dirty and not session.new
    assert snapshot(session) == before
    assert len(session.scalars(select(UserReviewFeedback)).all()) == feedback_before
    assert settings.scoring.weights == ScoringSettings().weights          # in-memory settings untouched too


def test_latest_explicit_label_wins_and_undecided_reviews_are_not_labels(settings, session):
    add_synthetic_book(session, 4, lambda s, r: 1)
    ids = [o.id for o in session.scalars(select(Opportunity).order_by(Opportunity.id))]
    session.add(UserReviewFeedback(opportunity_id=ids[0], relevance_label="irrelevant"))     # overrides "relevant"
    session.get(Opportunity, ids[2]).review_status = "Reviewing"
    session.get(Opportunity, ids[2]).score_breakdown = {**session.get(Opportunity, ids[2]).score_breakdown}
    session.execute(UserReviewFeedback.__table__.delete().where(UserReviewFeedback.opportunity_id == ids[2]))
    session.get(Opportunity, ids[1]).review_status = "Lost"                                  # pursued, so positive
    session.commit()
    notices, extra = load_notices(session)
    got = {n.opportunity_id: (n.label, n.label_source) for n in notices}
    assert got[ids[0]] == (0, "label: irrelevant")
    assert got[ids[1]] == (1, "status: Lost")
    assert got[ids[2]] == (None, None)
    assert extra["ignored_statuses"] == {"Reviewing": 1}


def cli_config(settings, tmp_path, **calibration):
    cfg = tmp_path / "config" / "chanakya.yaml"
    cfg.parent.mkdir()
    data = settings.model_dump(mode="json", exclude={"config_path"})
    data["app"]["capabilities_file"] = str(settings.app.capabilities_file)
    data["calibration"].update(calibration)
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return cfg


def test_cli_calibrate_writes_report_and_proposal_but_never_config_or_scores(settings, session, tmp_path):
    add_synthetic_book(session, 200, by_commercial)
    before = snapshot(session)
    cfg = cli_config(settings, tmp_path)
    cfg_bytes = cfg.read_bytes()
    report, proposal = tmp_path / "calibration.md", tmp_path / "proposal.yaml"
    runner = CliRunner()
    res = runner.invoke(cli_app, ["--config", str(cfg), "calibrate", "--out", str(report),
                                  "--write-proposal", str(proposal)])
    assert res.exit_code == 0, res.output
    assert "PROPOSAL (not applied)" in res.output and "Limits" in res.output
    assert cfg.read_bytes() == cfg_bytes
    assert snapshot(session) == before
    assert "PROPOSAL (not applied)" in report.read_text(encoding="utf-8")
    fragment = yaml.safe_load(proposal.read_text(encoding="utf-8"))
    assert list(fragment) == ["scoring"] and list(fragment["scoring"]) == ["weights"]
    assert_valid(fragment["scoring"]["weights"])
    assert "NOT applied" in proposal.read_text(encoding="utf-8")

    # pointing an output at the config (or the KB) is refused before anything is read or written
    for flag in ("--out", "--write-proposal"):
        bad = runner.invoke(cli_app, ["--config", str(cfg), "calibrate", flag, str(cfg)])
        assert bad.exit_code == 1 and "refusing to write" in bad.output
        assert cfg.read_bytes() == cfg_bytes
    kb_file = settings.app.capabilities_file
    kb_bytes = kb_file.read_bytes()
    bad = runner.invoke(cli_app, ["--config", str(cfg), "calibrate", "--write-proposal", str(kb_file)])
    assert bad.exit_code == 1 and kb_file.read_bytes() == kb_bytes


def test_cli_calibrate_with_too_few_labels_exits_zero_and_writes_no_proposal(settings, session, tmp_path):
    add_synthetic_book(session, 30, by_commercial)
    cfg = cli_config(settings, tmp_path)
    proposal = tmp_path / "proposal.yaml"
    runner = CliRunner()
    res = runner.invoke(cli_app, ["--config", str(cfg), "calibrate", "--write-proposal", str(proposal)])
    assert res.exit_code == 0, res.output
    assert "NO PROPOSAL" in res.output and "Only 30 labelled notices" in res.output
    assert not proposal.exists() and "no proposal file written" in res.output
    lowered = runner.invoke(cli_app, ["--config", str(cfg), "calibrate", "--min-labels", "20"])
    assert lowered.exit_code == 0 and "NO PROPOSAL" not in lowered.output


# ---------------------------------------------------------------- the replica matches the real scorer
def test_band_and_total_replica_match_the_pipeline(settings, session):
    write_records(settings.sources[0].path, sample_records())           # SYNTHETIC records (conftest)
    run_pipeline(settings, session, provider=FakeProvider())
    notices, _ = load_notices(session)
    stored = {o.id: o for o in session.scalars(select(Opportunity))}
    assert len(notices) == len(stored) == 6
    for n in notices:
        o = stored[n.opportunity_id]
        assert total_for(n.scores, settings.scoring.weights) == pytest.approx(o.relevance_score, abs=0.05)
        assert band_for(n, settings.scoring.weights, settings.scoring.priority_thresholds,
                        respect_status=True) == o.priority


# ---------------------------------------------------------------- win/loss
def add_outcomes(session) -> None:
    """SYNTHETIC pursuits and outcomes."""
    rows = [("SYN-W1", "Synthetic Metro Rail Ltd", "CAP-CV-VA", "Portal A", "Won"),
            ("SYN-W2", "Synthetic  Metro Rail Ltd.", "CAP-CV-VA", "Portal A", "Won"),     # same buyer, other spelling
            ("SYN-W3", "Synthetic Metro Rail Ltd", "CAP-VOICE", "Portal B", "Lost"),
            ("SYN-W4", "Synthetic Health Department", "CAP-VOICE", "Portal B", "Pursuing"),
            ("SYN-W5", "Synthetic Health Department", None, "Portal B", "Lost"),
            ("SYN-W6", "Synthetic Metro Rail Ltd", "CAP-CV-VA", "Portal A", "Shortlisted"),  # not pursued yet
            ("SYN-W7", None, "CAP-CV-VA", "Portal A", "Won")]
    for i, (oid, org, cap, platform, status) in enumerate(rows):
        session.add(Opportunity(id=oid, dedup_key=f"syn:w:{i}", source_id="synthetic", platform=platform,
                                source_record_key=str(i), title=f"SYNTHETIC pursuit {i}", organisation=org,
                                content_hash="0" * 64, review_status=status,
                                top_capabilities=[{"id": cap, "score": 0.8}] if cap else []))
    session.commit()


def test_winloss_summary_by_buyer_capability_and_platform(session):
    add_outcomes(session)
    before = snapshot(session)
    s = winloss_summary(session)
    assert s["totals"] == {"key": "All", "pursued": 6, "open": 1, "won": 3, "lost": 2, "win_rate": 0.6}
    buyers = {r["key"]: r for r in s["by_buyer"]}
    metro = buyers["Synthetic Metro Rail Ltd"]
    assert (metro["pursued"], metro["won"], metro["lost"], metro["open"]) == (3, 2, 1, 0)
    assert metro["win_rate"] == pytest.approx(0.667)
    health = buyers["Synthetic Health Department"]
    assert (health["pursued"], health["won"], health["lost"], health["open"], health["win_rate"]) == (2, 0, 1, 1, 0.0)
    assert buyers["Not stated"]["won"] == 1
    assert s["by_buyer"][0]["key"] == "Synthetic Metro Rail Ltd"            # most pursued first
    caps = {r["key"]: r for r in s["by_capability"]}
    assert (caps["CAP-CV-VA"]["pursued"], caps["CAP-CV-VA"]["won"]) == (3, 3)
    assert (caps["CAP-VOICE"]["pursued"], caps["CAP-VOICE"]["lost"], caps["CAP-VOICE"]["open"]) == (2, 1, 1)
    assert caps["No capability match"]["lost"] == 1
    platforms = {r["key"]: r for r in s["by_platform"]}
    assert (platforms["Portal A"]["pursued"], platforms["Portal B"]["pursued"]) == (3, 3)
    assert snapshot(session) == before and not session.dirty
    text = format_winloss(s)
    assert "Pursued 6 (open 1, won 3, lost 2)" in text and "| Synthetic Metro Rail Ltd | 3 | 0 | 2 | 1 | 67% |" in text


def test_winloss_empty_book_and_cli(settings, session, tmp_path):
    empty = winloss_summary(session)
    assert empty["totals"]["pursued"] == 0 and empty["totals"]["win_rate"] is None and empty["by_buyer"] == []
    assert "No notice has the review status" in format_winloss(empty)
    add_outcomes(session)
    cfg = cli_config(settings, tmp_path)
    out = tmp_path / "winloss.md"
    res = CliRunner().invoke(cli_app, ["--config", str(cfg), "winloss", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert "By buyer organisation" in res.output and "By capability" in res.output and "By platform" in res.output
    assert "Synthetic Metro Rail Ltd" in out.read_text(encoding="utf-8")


def test_buyer_win_count_excludes_the_notice_itself_and_unnamed_buyers(session):
    add_outcomes(session)
    assert buyer_win_count(session, "Synthetic Metro Rail Ltd") == 2
    assert buyer_win_count(session, "synthetic metro rail ltd", exclude_id="SYN-W1") == 1
    assert buyer_win_count(session, "Synthetic Health Department") == 0     # a loss is not a win
    assert buyer_win_count(session, None) == 0 and buyer_win_count(session, "  ") == 0


# ---------------------------------------------------------------- win history in the strategic component
def score_input(**kw) -> ScoreInput:
    from datetime import timedelta
    d = dict(filter_decision=FilterDecision.STRONG, best_capability="CAP-CV-VA", capability_score=0.9,
             eligibility=EligibilityStatus.UNKNOWN, status="active", closing_at_utc=now_utc() + timedelta(days=20),
             estimated_value_inr=6e7, organisation="Synthetic Trading Board", opportunity_type="Tender")
    d.update(kw)
    return ScoreInput(**d)


def test_win_history_is_off_by_default_and_changes_nothing(kb):
    assert ScoringSettings().win_history_enabled is False
    plain = Scorer(ScoringSettings(), kb).score(score_input())
    with_wins = Scorer(ScoringSettings(), kb).score(score_input(buyer_wins=5))
    assert with_wins.total == plain.total and with_wins.priority == plain.priority
    assert with_wins.components["strategic"]["score"] == plain.components["strategic"]["score"] == 0.3
    assert "history_bonus" not in with_wins.components["strategic"]
    assert "win" not in with_wins.components["strategic"]["reason"]


def test_win_history_bonus_is_bounded_and_explained(kb):
    on = Scorer(ScoringSettings(win_history_enabled=True), kb)
    base = on.score(score_input())
    assert base.components["strategic"]["score"] == 0.3 and "history_bonus" not in base.components["strategic"]
    one = on.score(score_input(buyer_wins=1)).components["strategic"]
    assert one["score"] == pytest.approx(0.4) and "1 win with this buyer (+0.10)" in one["reason"]
    two = on.score(score_input(buyer_wins=2))
    assert two.components["strategic"]["score"] == pytest.approx(0.5)
    assert "2 wins with this buyer (+0.20)" in two.components["strategic"]["reason"]
    assert two.components["strategic"]["history_bonus"] == pytest.approx(0.2)
    assert two.total == pytest.approx(base.total + 2.0, abs=0.05)           # 0.2 of a 10 point component
    assert two.total == pytest.approx(sum(c["points"] for c in two.components.values()), abs=0.05)
    many = on.score(score_input(buyer_wins=50)).components["strategic"]
    assert many["score"] == pytest.approx(0.5) and many["history_bonus"] == pytest.approx(0.2)   # capped
    # never above 1.0: a strategic buyer on an entry route is already at the ceiling
    top = on.score(score_input(organisation="Synthetic Smart City Ltd", opportunity_type="RFP", buyer_wins=3))
    assert top.components["strategic"]["score"] == 1.0 and "history_bonus" not in top.components["strategic"]
    # same input, same score
    assert on.score(score_input(buyer_wins=2)).total == two.total


def test_pipeline_applies_win_history_only_when_enabled(settings, session):
    records = sample_records()                                              # SYNTHETIC records (conftest)
    records.append({**records[0], "tender_id": "2026_SYNSC_000107_1", "ref_no": "SYN/SC/VA/07",
                    "title": "AI video analytics software for city surveillance cameras, phase 2"})
    write_records(settings.sources[0].path, records)
    run_pipeline(settings, session, provider=FakeProvider())
    opps = {o.source_tender_id: o for o in session.scalars(select(Opportunity))}
    first, second = opps["2026_SYNSC_000101_1"], opps["2026_SYNSC_000107_1"]
    assert "win" not in second.score_breakdown["components"]["strategic"]["reason"]
    base_total = second.relevance_score
    first.review_status = "Won"
    session.commit()

    run_pipeline(settings, session, provider=FakeProvider(), skip_scan=True, export=False, assess_scope="all")
    session.refresh(second)
    assert second.relevance_score == base_total                             # flag off: a recorded win changes nothing

    settings.scoring.win_history_enabled = True
    run_pipeline(settings, session, provider=FakeProvider(), skip_scan=True, export=False, assess_scope="all")
    session.refresh(second)
    session.refresh(first)
    strategic = second.score_breakdown["components"]["strategic"]
    assert "1 win with this buyer" in strategic["reason"]
    assert "win" not in first.score_breakdown["components"]["strategic"]["reason"]   # its own win is excluded


def test_calibration_fits_on_the_strategic_score_without_the_win_bonus():
    notices = []
    for n in synthetic_notices(200, by_commercial):
        bonus = 0.2 if n.label else 0.0                                     # a bonus that follows the label exactly
        scores = {**n.scores, "strategic": min(1.0, n.scores["strategic"] + bonus)}
        notices.append(Notice(n.opportunity_id, scores, dict(n.scores), label=n.label, label_source=n.label_source))
    clean = calibrate_notices(synthetic_notices(200, by_commercial), ScoringSettings(), CAL)
    rep = calibrate_notices(notices, ScoringSettings(), CAL)
    assert rep.proposed_weights == clean.proposed_weights                   # the leak is not learned
    assert any("win-history bonus" in note for note in rep.notes)


def test_config_files_declare_the_new_keys_with_safe_defaults():
    for name in ("chanakya.yaml", "chanakya.example.yaml"):
        path = ROOT / "config" / name
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["scoring"]["win_history_enabled"] is False, name
        assert data["calibration"]["min_labels"] == 100, name
