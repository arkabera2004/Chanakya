"""Create an offline demo inbox (SYNTHETIC records with dates relative to today, plus
one sample RFP PDF) so `chanakya --config config/chanakya.demo.yaml run --dry-run`
works without network access or an API key. Every record is marked SYNTHETIC."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import yaml

from chanakya.timeutil import now_ist

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo" / "inbox"


def d(days: float) -> str:
    return (now_ist() + timedelta(days=days)).strftime("%d-%b-%Y %I:%M %p")


RECORDS = [
    ("SYN_DEMO_001", "Implementation of AI-based video analytics on existing CCTV cameras for Smart City ICCC",
     "Synthetic Smart City Ltd", 21, 45_000_000, "docs/SYN_DEMO_001_RFP.pdf"),
    ("SYN_DEMO_002", "Speech analytics and quality monitoring for CM Helpline call centre", "Synthetic CM Office", 9, None, ""),
    ("SYN_DEMO_003", "Thermographic inspection of solar PV plants using UAV with defect analysis", "Synthetic Renewable Energy Agency", 18, None, ""),
    ("SYN_DEMO_004", "Comprehensive VAPT of departmental web applications", "Synthetic Finance Department", 5, None, ""),
    ("SYN_DEMO_005", "Expression of Interest for empanelment of Artificial Intelligence solution providers", "Synthetic NeGD", 14, None, ""),
    ("SYN_DEMO_006", "Road resurfacing work with traffic monitoring signboards", "Synthetic PWD", 12, None, ""),
    ("SYN_DEMO_007", "Catering services for hostel mess", "Synthetic University", 7, None, ""),
    ("SYN_DEMO_008", "Supply of CCTV camera spares and cables for police stations", "Synthetic Police HQ", 10, None, ""),
]


def make_pdf(path: Path) -> None:
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        print("reportlab not installed (pip install '.[dev]'); skipping sample PDF")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    pages = [
        ["SYNTHETIC SAMPLE — NOT A REAL TENDER", "Request for Proposal: AI Video Analytics for Smart City ICCC"],
        ["1. Scope of Work", "Deploy video analytics on 450 existing RTSP cameras: crowd density, intrusion,",
         "abandoned object and vehicle classification; integrate alerts with the ICCC dashboard."],
        ["2. Eligibility Criteria", "Average annual turnover of Rs. 10 crore in the last three financial years.",
         "Bidder must be ISO 27001 certified. Three similar completed works with Government or PSU.",
         "Consortium is not allowed."],
        ["3. Earnest Money Deposit", "EMD of Rs. 9,00,000 through online payment. MSEs are exempted from EMD."],
    ]
    for lines in pages:
        y = 800
        for line in lines:
            c.drawString(50, y, line)
            y -= 18
        c.showPage()
    c.save()


def main() -> None:
    DEMO.mkdir(parents=True, exist_ok=True)
    recs = []
    for tid, title, org, close, value, docs in RECORDS:
        recs.append({"platform": "Demo (synthetic)", "tender_id": tid, "title": title, "organisation": org,
                     "published_at": d(-1), "closing_at": d(close), "estimated_value_inr": value,
                     "document_paths": docs, "evidence": "SYNTHETIC demo record — not a real tender"})
    (DEMO / "demo_records.yaml").write_text(yaml.safe_dump({"records": recs}, sort_keys=False))
    make_pdf(DEMO / "docs" / "SYN_DEMO_001_RFP.pdf")
    print(f"demo inbox written to {DEMO}")


if __name__ == "__main__":
    main()
