"""Learning loop (roadmap Phase 7): propose scoring weights from BD feedback.

This module reads review feedback and the stored score breakdowns, fits a small
logistic regression, and returns a *proposal*. It never edits the configuration
and never changes a stored score. A human accepts a proposal by editing
``scoring.weights`` by hand (procedure in doc 10).

Which feedback counts
---------------------
One label per notice, from the most specific signal available:

1. The latest explicit ``relevance_label`` (relevant / irrelevant) recorded with
   ``chanakya review --label`` or the desk. It always wins over the status.
2. Otherwise the notice's current review status:
   - positive: Shortlisted, Qualified, Partner needed, Pursuing, Won, Lost.
     BD judged each of these worth effort. Lost is positive on purpose: the score
     ranks what deserves BD attention, not who wins the bid, and a lost bid was
     still a notice worth pursuing.
   - negative: Rejected.
   - ignored: New and Reviewing (no decision yet) and Expired (it ran out of
     time, which says nothing about relevance).

Model
-----
Features are the six stored component scores exactly as the scorer uses them:
capability as is, every other component multiplied by the relevance gate
``min(1, capability / 0.5)``. With those features the 0 to 100 total is linear in
the weights, so a logistic regression on them learns the same kind of weights the
scorer applies.

The fit is an L2-regularised logistic regression with non-negative coefficients,
solved with a projected Newton method in pure Python (no numpy). The L2 penalty
is centred on the *current* weights, not on zero: a first one-parameter fit finds
the scale at which the current total best explains the labels, and the full fit
is then pulled toward the current weights at that scale. Where the labels carry
no information (a component that never varies, or two components that always move
together) the proposal therefore stays at the current weights instead of drifting.
Coefficients are projected back onto the configured form: clipped at zero, scaled
to sum to 100, and rounded to whole numbers by the largest-remainder rule.

No proposal is made when there are too few labels, when a class is missing or
nearly missing, or when the six components together do not explain the labels
better than the class balance alone (a likelihood-ratio test at the 5% level).
Each refusal says why.

Everything is deterministic: notices are processed in opportunity-id order and
there is no random initialisation, sampling or shuffling.

Limits (also printed in every report)
-------------------------------------
See ``LIMITS``. The short version: labels exist only for notices BD was shown, so
this cannot learn about notices the funnel set aside; the sample is small; and
weights are the last thing to tune, after KB phrases, exclusions and thresholds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import CalibrationSettings, ScoringSettings, Settings
from ..db import Opportunity, UserReviewFeedback
from ..models import FilterDecision, ReviewStatus
from .scoring import CLOSED_STATUSES

POSITIVE_STATUSES = frozenset({
    ReviewStatus.SHORTLISTED.value, ReviewStatus.QUALIFIED.value, ReviewStatus.PARTNER_NEEDED.value,
    ReviewStatus.PURSUING.value, ReviewStatus.WON.value, ReviewStatus.LOST.value,
})
NEGATIVE_STATUSES = frozenset({ReviewStatus.REJECTED.value})
IGNORED_STATUSES = frozenset({ReviewStatus.NEW.value, ReviewStatus.REVIEWING.value, ReviewStatus.EXPIRED.value})

FLAGGED_BANDS = ("P1", "P2")

LIMITS = (
    "This is a proposal. Nothing was changed: not the configuration, not any stored score.",
    "Selection bias: labels exist only for notices BD was shown. Notices the funnel set aside (REJECTED or LOW) "
    "are almost never labelled, so this cannot learn about missed opportunities. Recall of the funnel is "
    "measured by `chanakya benchmark`, not here.",
    "Small sample: a few hundred labels from a few reviewers is thin evidence. Expect the proposal to move "
    "when more labels arrive. Treat a change of a few points as noise.",
    "The 'labelled set' precision is measured on the same notices the weights were fitted to, so it flatters "
    "the proposal. The held-out figure is the more honest one.",
    "Only the six weights are tuned. Component formulas, the relevance gate, priority thresholds, KB phrases "
    "and exclusion terms are untouched. Doc 10 says to fix those first: KB phrases, then exclusions, then "
    "thresholds, then weights.",
    "Components overlap: every non-capability component is multiplied by the relevance gate, so they rise and "
    "fall with capability. Weight can shift between overlapping components without changing the ranking much.",
    "Labels are BD judgement of relevance or worth, not outcomes. Lost counts as positive (it was worth "
    "pursuing); Expired, New and Reviewing are ignored.",
)


# --------------------------------------------------------------------------- data
@dataclass(frozen=True)
class Notice:
    """One scored notice, reduced to what calibration needs."""

    opportunity_id: str
    scores: dict[str, float]          # stored component scores, 0 to 1
    fit_scores: dict[str, float]      # the same, without any win-history bonus (so labels cannot leak into a feature)
    filter_decision: str | None = "candidate"
    status: str = "active"
    equipment_cap: bool = False       # equipment supply: the scorer caps P1/P2 at P3
    stored_priority: str | None = None
    label: int | None = None          # 1 relevant, 0 irrelevant, None unlabelled
    label_source: str | None = None


@dataclass
class FitResult:
    intercept: float
    coef: list[float]
    iterations: int
    converged: bool
    loss: float


@dataclass
class CalibrationReport:
    status: str                                   # proposal | no_change | refused
    reasons: list[str] = field(default_factory=list)
    n_scored: int = 0
    n_labelled: int = 0
    n_positive: int = 0
    n_negative: int = 0
    label_sources: dict[str, int] = field(default_factory=dict)
    ignored_statuses: dict[str, int] = field(default_factory=dict)
    n_conflicts: int = 0
    min_labels: int = 0
    min_labels_per_class: int = 0
    l2: float = 0.0
    current_weights: dict[str, float] = field(default_factory=dict)   # normalised to sum to 100
    proposed_weights: dict[str, int] | None = None
    components: list[dict[str, Any]] = field(default_factory=list)
    current_metrics: dict[str, Any] | None = None
    proposed_metrics: dict[str, Any] | None = None
    cv_current_metrics: dict[str, Any] | None = None
    cv_metrics: dict[str, Any] | None = None
    cv_note: str | None = None
    labelled_band_changes: int = 0
    labelled_changes: list[dict[str, Any]] = field(default_factory=list)
    book_band_changes: int = 0
    book_transitions: dict[str, int] = field(default_factory=dict)
    stored_bands_reproduced: int = 0
    stored_bands_compared: int = 0
    fit: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    verdict: str | None = None                    # one plain sentence on whether the evidence supports the proposal

    @property
    def movers(self) -> list[dict[str, Any]]:
        """Components ordered by the size of the proposed change, largest first."""
        moved = [c for c in self.components if c.get("delta")]
        return sorted(moved, key=lambda c: (-abs(c["delta"]), c["key"]))


def derive_label(explicit: str | None, review_status: str | None) -> tuple[int | None, str | None]:
    """Turn BD feedback into (label, source). An explicit relevance label wins over the status."""
    if explicit == "relevant":
        return 1, "label: relevant"
    if explicit == "irrelevant":
        return 0, "label: irrelevant"
    if review_status in POSITIVE_STATUSES:
        return 1, f"status: {review_status}"
    if review_status in NEGATIVE_STATUSES:
        return 0, f"status: {review_status}"
    return None, None


def load_notices(session: Session) -> tuple[list[Notice], dict[str, Any]]:
    """Read every scored notice and its label, in opportunity-id order. Reads only."""
    explicit: dict[str, str] = {}
    rows = session.execute(
        select(UserReviewFeedback.opportunity_id, UserReviewFeedback.relevance_label)
        .where(UserReviewFeedback.relevance_label.is_not(None))
        .order_by(UserReviewFeedback.created_at, UserReviewFeedback.id)
    ).all()
    for oid, lab in rows:
        if lab in ("relevant", "irrelevant"):
            explicit[oid] = lab        # later rows overwrite earlier ones: the latest label wins

    notices: list[Notice] = []
    ignored: dict[str, int] = {}
    conflicts = 0
    opps = session.execute(
        select(Opportunity.id, Opportunity.score_breakdown, Opportunity.filter_decision, Opportunity.status,
               Opportunity.priority, Opportunity.review_status).order_by(Opportunity.id)
    ).all()
    for oid, breakdown, decision, status, priority, review_status in opps:
        comps = (breakdown or {}).get("components") or {}
        if not comps:
            continue
        scores: dict[str, float] = {}
        fit_scores: dict[str, float] = {}
        for name, comp in comps.items():
            try:
                value = float(comp.get("score", 0.0))
                bonus = float(comp.get("history_bonus", 0.0) or 0.0)
            except (AttributeError, TypeError, ValueError):
                value, bonus = 0.0, 0.0
            scores[name] = value
            fit_scores[name] = max(0.0, value - bonus)
        flags = (breakdown or {}).get("flags") or []
        tech_reason = str((comps.get("technical") or {}).get("reason", ""))
        equipment = any(str(f).startswith("EQUIPMENT SUPPLY") for f in flags) or tech_reason.startswith("equipment supply")
        exp = explicit.get(oid)
        label, source = derive_label(exp, review_status)
        if label is None and review_status in IGNORED_STATUSES and review_status != ReviewStatus.NEW.value:
            ignored[review_status] = ignored.get(review_status, 0) + 1
        if exp and ((exp == "relevant" and review_status in NEGATIVE_STATUSES)
                    or (exp == "irrelevant" and review_status in POSITIVE_STATUSES)):
            conflicts += 1
        notices.append(Notice(opportunity_id=oid, scores=scores, fit_scores=fit_scores, filter_decision=decision,
                              status=status or "active", equipment_cap=equipment, stored_priority=priority,
                              label=label, label_source=source))
    return notices, {"ignored_statuses": ignored, "n_conflicts": conflicts}


# --------------------------------------------------------------------------- scoring replica
def gate_of(scores: dict[str, float]) -> float:
    """The scorer's relevance gate, from the stored capability score."""
    return round(min(1.0, scores.get("capability", 0.0) / 0.5), 3)


def feature_row(scores: dict[str, float], keys: list[str]) -> list[float]:
    """Component scores as the scorer weighs them: capability as is, the rest times the gate."""
    g = gate_of(scores)
    return [scores.get(k, 0.0) * (1.0 if k == "capability" else g) for k in keys]


def total_for(scores: dict[str, float], weights: dict[str, float]) -> float:
    """The 0 to 100 total these component scores would get under ``weights`` (same arithmetic as the scorer)."""
    wsum = sum(weights.values())
    if wsum <= 0:
        return 0.0
    g = gate_of(scores)
    total = 0.0
    for name, score in scores.items():
        w = weights.get(name, 0.0)
        total += round(score * w / wsum * 100 * (1.0 if name == "capability" else g), 2)
    return round(total, 1)


def band_for(n: Notice, weights: dict[str, float], thresholds: dict[str, float], *, respect_status: bool) -> str:
    """Priority band under ``weights``, following the scorer's rules as far as stored data allows.

    ``respect_status=False`` ignores the NOT ACTIVE rule: a label is a judgement of relevance, and a
    relevant notice that has since closed should still count for or against the weights.
    """
    cap = n.scores.get("capability", 0.0)
    total = total_for(n.scores, weights)
    if respect_status and n.status in CLOSED_STATUSES:
        return "P4"
    if n.filter_decision in (FilterDecision.REJECTED.value, FilterDecision.LOW.value) or cap < 0.3:
        return "P4"
    if total >= thresholds["P1"] and cap >= 0.5:
        band = "P1"
    elif total >= thresholds["P2"]:
        band = "P2"
    elif total >= thresholds["P3"]:
        band = "P3"
    else:
        band = "P4"
    if n.equipment_cap and band in FLAGGED_BANDS:
        band = "P3"
    return band


def band_metrics(pairs: list[tuple[str, int]]) -> dict[str, Any]:
    """Precision and recall of the P1/P2 bands against labels. ``pairs`` is (band, label)."""
    tp = sum(1 for b, y in pairs if b in FLAGGED_BANDS and y == 1)
    fp = sum(1 for b, y in pairs if b in FLAGGED_BANDS and y == 0)
    fn = sum(1 for b, y in pairs if b not in FLAGGED_BANDS and y == 1)
    return {"n": len(pairs), "flagged": tp + fp, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}


# --------------------------------------------------------------------------- logistic regression
def _log_loss_term(margin: float, y: int) -> float:
    return max(margin, 0.0) + math.log1p(math.exp(-abs(margin))) - y * margin


def _sigmoid(margin: float) -> float:
    if margin >= 0:
        return 1.0 / (1.0 + math.exp(-margin))
    e = math.exp(margin)
    return e / (1.0 + e)


def _objective(theta: list[float], X: list[list[float]], y: list[int], l2: float, prior: list[float]) -> float:
    loss = 0.0
    for row, yi in zip(X, y, strict=True):
        margin = theta[0] + sum(t * x for t, x in zip(theta[1:], row, strict=True))
        loss += _log_loss_term(margin, yi)
    return loss + 0.5 * l2 * sum((t - p) ** 2 for t, p in zip(theta[1:], prior, strict=True))


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Solve a x = b by Gaussian elimination with partial pivoting. None when singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


def fit_logistic(X: list[list[float]], y: list[int], *, l2: float, prior: list[float] | None = None,
                 max_iter: int = 200, tol: float = 1e-7) -> FitResult:
    """L2-regularised logistic regression with non-negative coefficients (the intercept is free).

    Minimises  sum_i logloss(y_i, b + x_i . beta) + (l2 / 2) * |beta - prior|^2   subject to beta >= 0
    by projected Newton steps with a backtracking line search, falling back to a projected gradient
    step when a Newton step does not descend. The problem is convex, so the result does not depend on
    a starting guess; the start is fixed anyway (the prior), which makes the fit reproducible.
    """
    d = len(X[0]) if X else 0
    prior = list(prior) if prior is not None else [0.0] * d
    if l2 <= 0:
        raise ValueError("l2 must be positive")
    theta = [0.0] + [max(0.0, p) for p in prior]
    f_now = _objective(theta, X, y, l2, prior)
    converged = False
    iterations = 0
    for _ in range(max_iter):
        iterations += 1
        grad = [0.0] * (d + 1)
        hess = [[0.0] * (d + 1) for _ in range(d + 1)]
        for row, yi in zip(X, y, strict=True):
            z = [1.0] + row
            p = _sigmoid(sum(t * v for t, v in zip(theta, z, strict=True)))
            r, w = p - yi, p * (1.0 - p)
            for j in range(d + 1):
                grad[j] += r * z[j]
                if w:
                    wz = w * z[j]
                    for k in range(j, d + 1):
                        hess[j][k] += wz * z[k]
        for j in range(d + 1):
            for k in range(j):
                hess[j][k] = hess[k][j]
        for j in range(1, d + 1):
            grad[j] += l2 * (theta[j] - prior[j - 1])
            hess[j][j] += l2
        hess[0][0] += 1e-9
        # A coefficient sitting on the bound whose gradient pushes it further out stays fixed.
        bound = {j for j in range(1, d + 1) if theta[j] <= 0.0 and grad[j] > 0.0}
        free = [j for j in range(d + 1) if j not in bound]
        if max(abs(grad[j]) for j in free) < tol:
            converged = True
            break
        step = _solve([[hess[j][k] for k in free] for j in free], [-grad[j] for j in free])
        directions = []
        if step is not None:
            newton = [0.0] * (d + 1)
            for j, s in zip(free, step, strict=True):
                newton[j] = s
            directions.append(newton)
        directions.append([0.0 if j in bound else -grad[j] for j in range(d + 1)])
        moved = False
        for direction in directions:
            t = 1.0
            for _ in range(50):
                cand = [theta[0] + t * direction[0]] + [max(0.0, theta[j] + t * direction[j]) for j in range(1, d + 1)]
                slope = sum(g * (c - th) for g, c, th in zip(grad, cand, theta, strict=True))
                f_cand = _objective(cand, X, y, l2, prior)
                if slope < 0 and f_cand <= f_now + 1e-4 * slope:
                    theta, f_now, moved = cand, f_cand, True
                    break
                t *= 0.5
            if moved:
                break
        if not moved:
            break   # no descent possible at machine precision: we are at the optimum
    if not converged:
        # Stalled or out of iterations: accept only if the projected gradient is small anyway.
        grad = [0.0] * (d + 1)
        for row, yi in zip(X, y, strict=True):
            z = [1.0] + row
            r = _sigmoid(sum(t * v for t, v in zip(theta, z, strict=True))) - yi
            for j in range(d + 1):
                grad[j] += r * z[j]
        for j in range(1, d + 1):
            grad[j] += l2 * (theta[j] - prior[j - 1])
        pg = [grad[0]] + [min(grad[j], 0.0) if theta[j] <= 0.0 else grad[j] for j in range(1, d + 1)]
        converged = max(abs(v) for v in pg) < 1e-4
    return FitResult(intercept=theta[0], coef=theta[1:], iterations=iterations, converged=converged, loss=f_now)


def project_weights(coef: list[float], keys: list[str]) -> dict[str, int] | None:
    """Project coefficients onto the configured form: non-negative whole numbers that sum to 100.

    Largest-remainder rounding; ties go to the component listed first. None when every coefficient is zero.
    """
    clipped = [max(0.0, c) for c in coef]
    total = sum(clipped)
    if total <= 1e-12:
        return None
    exact = [c / total * 100 for c in clipped]
    floors = [int(math.floor(e + 1e-9)) for e in exact]
    order = sorted(range(len(keys)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[: 100 - sum(floors)]:
        floors[i] += 1
    return dict(zip(keys, floors, strict=True))


_CHI2_5PCT = (3.84, 5.99, 7.81, 9.49, 11.07, 12.59, 14.07, 15.51, 16.92, 18.31)


def chi2_critical_5pct(df: int) -> float:
    """Upper 5% point of the chi-square distribution (table to 10 degrees of freedom, then Wilson-Hilferty)."""
    if 1 <= df <= len(_CHI2_5PCT):
        return _CHI2_5PCT[df - 1]
    return df * (1 - 2 / (9 * df) + 1.645 * math.sqrt(2 / (9 * df))) ** 3


def propose_weights(X: list[list[float]], y: list[int], keys: list[str], current: dict[str, float],
                    l2: float) -> tuple[dict[str, int] | None, dict[str, Any]]:
    """Fit and project. Returns (weights or None, fit details)."""
    wsum = sum(current.get(k, 0.0) for k in keys)
    unit = [current.get(k, 0.0) / wsum for k in keys]
    # Step 1: the scale at which the current total best explains the labels (one parameter).
    z = [[sum(u * x for u, x in zip(unit, row, strict=True))] for row in X]
    scale_fit = fit_logistic(z, y, l2=l2 * sum(u * u for u in unit))
    scale = scale_fit.coef[0]
    # Step 2: the full fit, pulled toward the current weights at that scale.
    centre = [scale * u for u in unit]
    fit = fit_logistic(X, y, l2=l2, prior=centre)
    data_loss = _objective([fit.intercept] + fit.coef, X, y, 0.0, centre)     # log-loss without the penalty
    info = {"scale": round(scale, 4), "intercept": round(fit.intercept, 4), "data_loss": round(data_loss, 4),
            "coef": {k: round(c, 4) for k, c in zip(keys, fit.coef, strict=True)},
            "iterations": fit.iterations, "converged": fit.converged and scale_fit.converged,
            "loss": round(fit.loss, 4)}
    return project_weights(fit.coef, keys), info


# --------------------------------------------------------------------------- calibration
def _folds(labelled: list[Notice], k: int) -> list[int]:
    """Deterministic stratified folds: within each class, notices are dealt out in id order."""
    counters = {0: 0, 1: 0}
    out = []
    for n in labelled:
        out.append(counters[n.label] % k)
        counters[n.label] += 1
    return out


def calibrate_notices(notices: list[Notice], scoring: ScoringSettings, cal: CalibrationSettings | None = None,
                      extra: dict[str, Any] | None = None) -> CalibrationReport:
    """Build the calibration report for a set of scored notices. Pure: no database, no files."""
    cal = cal or CalibrationSettings()
    extra = extra or {}
    notices = sorted(notices, key=lambda n: n.opportunity_id)
    keys = list(scoring.weights)
    wsum = sum(scoring.weights.values())
    current = {k: round(scoring.weights[k] / wsum * 100, 2) for k in keys}
    thresholds = scoring.priority_thresholds
    labelled = [n for n in notices if n.label is not None]
    y = [int(n.label) for n in labelled]
    sources: dict[str, int] = {}
    for n in labelled:
        sources[n.label_source or "unknown"] = sources.get(n.label_source or "unknown", 0) + 1

    rep = CalibrationReport(
        status="refused", n_scored=len(notices), n_labelled=len(labelled), n_positive=sum(y),
        n_negative=len(y) - sum(y), label_sources=dict(sorted(sources.items())),
        ignored_statuses=dict(sorted((extra.get("ignored_statuses") or {}).items())),
        n_conflicts=int(extra.get("n_conflicts") or 0), min_labels=cal.min_labels,
        min_labels_per_class=cal.min_labels_per_class, l2=cal.l2, current_weights=current)
    if abs(wsum - 100) > 1e-9:
        rep.notes.append(f"Configured weights sum to {wsum:g}, not 100. They are shown here scaled to 100; "
                         "the scorer already divides by the sum, so scores are unaffected.")

    if labelled:
        rep.current_metrics = band_metrics([(band_for(n, current, thresholds, respect_status=False), n.label)
                                            for n in labelled])
    compared = [n for n in notices if n.stored_priority]
    rep.stored_bands_compared = len(compared)
    rep.stored_bands_reproduced = sum(
        1 for n in compared if band_for(n, current, thresholds, respect_status=True) == n.stored_priority)
    if rep.stored_bands_reproduced < rep.stored_bands_compared:
        rep.notes.append(
            f"{rep.stored_bands_compared - rep.stored_bands_reproduced} of {rep.stored_bands_compared} stored bands "
            "could not be reproduced from the stored components at the configured weights. Those notices were "
            "scored under different weights or thresholds, or hit a rule this report cannot see. Run "
            "`chanakya analyze --scope all` before trusting the comparison.")

    # ---- refusals
    if rep.n_positive == 0 or rep.n_negative == 0:
        missing = "relevant (positive)" if rep.n_positive == 0 else "irrelevant (negative)"
        rep.reasons.append(f"No {missing} labels. A model needs examples of both classes; with one class missing "
                           "every weighting looks equally good.")
    if rep.n_labelled < cal.min_labels:
        rep.reasons.append(f"Only {rep.n_labelled} labelled notices; calibration.min_labels is {cal.min_labels}. "
                           "A proposal from fewer labels would mostly be noise.")
    smaller = min(rep.n_positive, rep.n_negative)
    if 0 < smaller < cal.min_labels_per_class:
        rep.reasons.append(f"The smaller class has {smaller} labels; calibration.min_labels_per_class is "
                           f"{cal.min_labels_per_class}.")
    if rep.reasons:
        return rep

    # ---- fit
    X = [feature_row(n.fit_scores, keys) for n in labelled]
    proposed, info = propose_weights(X, y, keys, current, cal.l2)
    rep.fit = info
    if proposed is None:
        rep.reasons.append("The fit put zero weight on every component: the labels are unrelated to, or run "
                           "against, every component at once. That points to a labelling or KB problem, not to "
                           "weights.")
        return rep
    # Do the components explain the labels better than the class balance alone? (likelihood-ratio test, 5%)
    share = rep.n_positive / rep.n_labelled
    null_loss = -(rep.n_positive * math.log(share) + rep.n_negative * math.log(1.0 - share))
    lr, needed = 2.0 * (null_loss - info["data_loss"]), chi2_critical_5pct(len(keys))
    info["likelihood_ratio"], info["likelihood_ratio_needed"] = round(lr, 2), round(needed, 2)
    if lr < needed:
        rep.reasons.append(
            f"The components do not explain these labels better than chance (likelihood-ratio statistic {lr:.1f} on "
            f"{len(keys)} degrees of freedom; {needed:.1f} is needed at the 5% level). Any weights fitted to them "
            "would be noise. Check the labels, collect more, or look at KB phrases and thresholds instead.")
        return rep
    if not info["converged"]:
        rep.notes.append("The optimiser did not fully converge. Treat the proposal with extra caution.")
    if info["scale"] <= 0:
        rep.notes.append("At the current weights the total score does not separate relevant from irrelevant "
                         "labels at all, so the fit was not anchored to the current weights. Check the labels "
                         "and the KB before changing weights.")
    if any(n.scores != n.fit_scores for n in labelled):
        rep.notes.append("Some labelled notices carry a win-history bonus in the strategic component. The fit "
                         "used the strategic score without that bonus, so a recorded win cannot teach the model "
                         "to reward itself.")
    rep.proposed_weights = proposed
    rep.status = "proposal" if any(abs(proposed[k] - current[k]) >= 0.5 for k in keys) else "no_change"

    # ---- per-component diagnostics
    pos = [row for row, yi in zip(X, y, strict=True) if yi == 1]
    neg = [row for row, yi in zip(X, y, strict=True) if yi == 0]
    flat = []
    for j, k in enumerate(keys):
        raw = {round(n.fit_scores.get(k, 0.0), 6) for n in labelled}
        varies = len(raw) > 1
        if not varies:
            flat.append(k)
        rep.components.append({
            "key": k, "current": current[k], "proposed": proposed[k], "delta": round(proposed[k] - current[k], 2),
            "mean_relevant": round(sum(r[j] for r in pos) / len(pos), 3),
            "mean_irrelevant": round(sum(r[j] for r in neg) / len(neg), 3), "varies": varies})
    if flat:
        rep.notes.append("No variation among the labelled notices in: " + ", ".join(flat) + ". The labels say "
                         "nothing about these components on their own; their proposed weights only reflect the "
                         "pull toward the current weights and the rescaling of the others.")
    stuck = sum(1 for n in labelled if n.label == 1 and (
        n.filter_decision in (FilterDecision.REJECTED.value, FilterDecision.LOW.value)
        or n.scores.get("capability", 0.0) < 0.3))
    if stuck:
        rep.notes.append(f"{stuck} notices labelled relevant are held at P4 by the relevance rules (LOW or REJECTED "
                         "decision, or capability below 0.3) whatever the weights. Fix those with KB phrases.")

    # ---- band comparison on the labelled set (as if active)
    prop_f = {k: float(v) for k, v in proposed.items()}
    before = [band_for(n, current, thresholds, respect_status=False) for n in labelled]
    after = [band_for(n, prop_f, thresholds, respect_status=False) for n in labelled]
    rep.proposed_metrics = band_metrics(list(zip(after, y, strict=True)))
    changes = [{"id": n.opportunity_id, "from": b, "to": a, "label": "relevant" if n.label else "irrelevant"}
               for n, b, a in zip(labelled, before, after, strict=True) if a != b]
    rep.labelled_band_changes = len(changes)
    rep.labelled_changes = changes

    # ---- band changes across the whole book (actual rules, closed notices stay P4)
    for n in notices:
        b = band_for(n, current, thresholds, respect_status=True)
        a = band_for(n, prop_f, thresholds, respect_status=True)
        if a != b:
            rep.book_band_changes += 1
            key = f"{b} to {a}"
            rep.book_transitions[key] = rep.book_transitions.get(key, 0) + 1
    rep.book_transitions = dict(sorted(rep.book_transitions.items()))

    # ---- held-out check
    k = cal.cv_folds
    if k < 2:
        rep.cv_note = "Held-out check turned off (calibration.cv_folds < 2)."
    elif smaller < k:
        rep.cv_note = f"Held-out check skipped: the smaller class has {smaller} labels, fewer than {k} folds."
    else:
        folds = _folds(labelled, k)
        held: list[tuple[str, int]] = []
        failed = False
        for f in range(k):
            train = [i for i, g in enumerate(folds) if g != f]
            w_f, _ = propose_weights([X[i] for i in train], [y[i] for i in train], keys, current, cal.l2)
            if w_f is None:
                failed = True
                break
            wf = {kk: float(v) for kk, v in w_f.items()}
            held += [(band_for(labelled[i], wf, thresholds, respect_status=False), y[i])
                     for i, g in enumerate(folds) if g == f]
        if failed:
            rep.cv_note = "Held-out check failed: one fold produced no usable weights."
        else:
            rep.cv_metrics = band_metrics(held)
            rep.cv_current_metrics = rep.current_metrics
            rep.cv_note = (f"{k} folds, dealt out in opportunity-id order within each class. Each notice is "
                           "banded with weights fitted without it.")
    rep.verdict = _verdict(rep)
    return rep


def _verdict(rep: CalibrationReport) -> str:
    if rep.status == "no_change":
        return "The labels support the current weights to within rounding. Nothing to accept."
    cur, cv = rep.current_metrics or {}, rep.cv_metrics
    if cv is None:
        return ("There is no held-out check for this proposal, so the only evidence is measured on the notices it "
                "was fitted to. Do not accept it on that alone.")
    p0, p1, r0, r1 = cur.get("precision"), cv.get("precision"), cur.get("recall") or 0.0, cv.get("recall") or 0.0
    if p1 is None:
        return ("Held out, the proposal puts no labelled notice in P1/P2. It would empty the shortlist. "
                "Keep the current weights.")
    if p0 is not None and p1 <= p0:
        return (f"Held out, the proposal does not improve P1/P2 precision ({p0:.2f} now, {p1:.2f} proposed). "
                "Keep the current weights.")
    text = (f"Held out, P1/P2 precision moves from {'n/a' if p0 is None else format(p0, '.2f')} to {p1:.2f} and "
            f"recall of relevant notices from {r0:.2f} to {r1:.2f}.")
    if r1 < r0:
        text += " Precision is bought with recall: fewer relevant notices reach P1/P2. Weigh that before accepting."
    return text + " A human decides."


def run_calibration(session: Session, settings: Settings, *, min_labels: int | None = None) -> CalibrationReport:
    """Read feedback and scores from the database and build the report. Writes nothing."""
    cal = settings.calibration
    if min_labels is not None:
        cal = cal.model_copy(update={"min_labels": min_labels})
    notices, extra = load_notices(session)
    return calibrate_notices(notices, settings.scoring, cal, extra)


# --------------------------------------------------------------------------- output
def _pct(v: float | None) -> str:
    return "n/a (nothing in P1/P2)" if v is None else f"{v:.2f}"


def _metric_row(name: str, m: dict[str, Any] | None) -> str:
    if m is None:
        return f"| {name} | n/a | n/a | n/a | n/a | n/a |"
    recall = "n/a" if m["recall"] is None else f"{m['recall']:.2f}"
    return f"| {name} | {_pct(m['precision'])} | {recall} | {m['flagged']} | {m['tp']} | {m['fp']} |"


def _fmt_w(v: float) -> str:
    return f"{v:g}"


def weights_inline(weights: dict[str, Any]) -> str:
    return "{" + ", ".join(f"{k}: {_fmt_w(float(v))}" for k, v in weights.items()) + "}"


def format_report(rep: CalibrationReport) -> str:
    """Markdown report for the console or a file. ASCII only, so it prints on any Windows console."""
    headline = {"proposal": "PROPOSAL (not applied)", "no_change": "NO CHANGE PROPOSED",
                "refused": "NO PROPOSAL"}[rep.status]
    lines = ["# Chanakya weight calibration", "", f"Result: {headline}", "",
             "## Labels", "",
             f"- Scored notices in the book: {rep.n_scored}",
             f"- Labelled notices used: {rep.n_labelled} ({rep.n_positive} relevant, {rep.n_negative} irrelevant)"]
    for src, n in rep.label_sources.items():
        lines.append(f"  - {src}: {n}")
    if rep.ignored_statuses:
        lines.append("- Reviewed but ignored (no relevance signal): "
                     + ", ".join(f"{k} {v}" for k, v in rep.ignored_statuses.items()))
    if rep.n_conflicts:
        lines.append(f"- {rep.n_conflicts} notices have an explicit label that disagrees with their review status. "
                     "The explicit label was used.")
    lines += [f"- Minimums: {rep.min_labels} labels in total, {rep.min_labels_per_class} in each class "
              "(calibration.min_labels, calibration.min_labels_per_class)", ""]

    if rep.status == "refused":
        lines += ["## Why there is no proposal", ""] + [f"- {r}" for r in rep.reasons] + [""]
        lines += ["Current weights (unchanged): " + weights_inline(rep.current_weights), ""]
        if rep.current_metrics and rep.current_metrics["n"]:
            m = rep.current_metrics
            lines += [f"For reference, P1/P2 precision at the current weights on the {m['n']} labelled notices: "
                      f"{_pct(m['precision'])} ({m['tp']} relevant of {m['flagged']} in P1/P2). With this few labels "
                      "the figure is indicative only.", ""]
    else:
        lines += ["## Weights", "",
                  "| Component | Current | Proposed | Change | Mean value, relevant | Mean value, irrelevant | Varies |",
                  "|---|---|---|---|---|---|---|"]
        for c in rep.components:
            lines.append(f"| {c['key']} | {_fmt_w(c['current'])} | {c['proposed']} | {c['delta']:+g} | "
                         f"{c['mean_relevant']:.3f} | {c['mean_irrelevant']:.3f} | {'yes' if c['varies'] else 'no'} |")
        lines += ["", "Mean value is the component score as the scorer weighs it (after the relevance gate), "
                      "averaged over the labelled notices of each class.", ""]
        movers = rep.movers[:3]
        if movers:
            lines.append("Largest movers: " + "; ".join(
                f"{c['key']} {_fmt_w(c['current'])} to {c['proposed']} ({c['delta']:+g})" for c in movers) + ".")
        else:
            lines.append("Largest movers: none. The labels support the current weights to within rounding.")
        if rep.verdict:
            lines += ["", f"Reading: {rep.verdict}"]
        lines += ["", "## P1/P2 against the labels", "",
                  "| Weights | Precision | Recall | In P1/P2 | Relevant | Irrelevant |", "|---|---|---|---|---|---|",
                  _metric_row("Current, labelled set", rep.current_metrics),
                  _metric_row("Proposed, labelled set (fitted on these notices)", rep.proposed_metrics),
                  _metric_row("Proposed, held out", rep.cv_metrics), ""]
        if rep.cv_note:
            lines += [f"Held-out check: {rep.cv_note}", ""]
        lines += ["Bands here ignore the NOT ACTIVE rule, because a label judges relevance and many labelled "
                  "notices have since closed.", "",
                  "## Band changes if the proposal were accepted", "",
                  f"- Labelled notices changing band: {rep.labelled_band_changes} of {rep.n_labelled}",
                  f"- Whole book (actual rules, closed notices stay P4): {rep.book_band_changes} of {rep.n_scored}"]
        for key, n in rep.book_transitions.items():
            lines.append(f"  - {key}: {n}")
        if rep.labelled_changes:
            lines += ["", "Labelled notices that would move (first 10):"]
            for c in rep.labelled_changes[:10]:
                lines.append(f"- {c['id']}: {c['from']} to {c['to']} (labelled {c['label']})")
        f = rep.fit
        lines += ["", "## Fit", "",
                  f"- L2 strength (calibration.l2): {rep.l2:g}, centred on the current weights at scale "
                  f"{f.get('scale', 0):g}",
                  f"- Iterations: {f.get('iterations')}, converged: {'yes' if f.get('converged') else 'no'}",
                  f"- Likelihood-ratio statistic against 'class balance only': {f.get('likelihood_ratio')} "
                  f"(at least {f.get('likelihood_ratio_needed')} was required to make a proposal)",
                  "- Coefficients before projection: "
                  + ", ".join(f"{k} {v:g}" for k, v in (f.get("coef") or {}).items()), ""]

    if rep.notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in rep.notes] + [""]
    lines += ["## Limits", ""] + [f"- {x}" for x in LIMITS] + [""]
    if rep.status == "proposal":
        lines += ["## To accept (a human decision)", "",
                  "1. Read the limits above. If the held-out precision is not better than the current one, stop.",
                  "2. Edit `scoring.weights` in `config/chanakya.yaml` by hand to:",
                  "   `weights: " + weights_inline(rep.proposed_weights or {}) + "`",
                  "3. Run `chanakya analyze --scope all` to re-score the stored notices.",
                  "4. Run `chanakya benchmark`. Funnel recall must not drop and cost must not rise. If either "
                  "regresses, put the old weights back and re-run step 3.",
                  "5. Record the change and the date in doc 10.", ""]
    return "\n".join(lines)


def proposal_yaml(rep: CalibrationReport) -> str | None:
    """A YAML fragment a human can copy into the config. None when there is no proposal."""
    if rep.proposed_weights is None:
        return None
    cur, prop, cv = rep.current_metrics or {}, rep.proposed_metrics or {}, rep.cv_metrics
    lines = [
        "# Chanakya weight PROPOSAL from `chanakya calibrate`. NOT applied. Review before use.",
        f"# Based on {rep.n_labelled} labelled notices ({rep.n_positive} relevant, {rep.n_negative} irrelevant).",
        f"# P1/P2 precision on the labelled set: current {_pct(cur.get('precision'))}, "
        f"proposed {_pct(prop.get('precision'))} (fitted on the same notices, so optimistic).",
        "# Held-out P1/P2 precision of the proposal: " + (_pct(cv["precision"]) if cv else "not available") + ".",
        f"# Reading: {rep.verdict}",
        "# Labels exist only for notices BD was shown; this says nothing about notices that were set aside.",
        "# To accept: copy `weights` into the scoring section of config/chanakya.yaml, run",
        "# `chanakya analyze --scope all`, then `chanakya benchmark` (it must not regress). See doc 10.",
        f"# Current weights: {weights_inline(rep.current_weights)}",
        "scoring:",
        f"  weights: {weights_inline(rep.proposed_weights)}",
        "",
    ]
    return "\n".join(lines)
