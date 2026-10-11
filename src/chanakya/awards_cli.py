"""`chanakya awards ...` commands. Kept out of ``cli.py`` so that file only gains a
two-line registration. ``settings`` and ``_writer`` are imported lazily from ``cli``
(which imports this module) to avoid a circular import."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

awards_app = typer.Typer(help="Award notices (CPPP Result of Tenders): who won what, and at what value")


@awards_app.command("scan")
def awards_scan(max_pages: Annotated[Optional[int], typer.Option(help="Pages to read; default awards.max_pages")] = None) -> None:
    """Read the public award listing and store award lines (no analysis, no model calls)."""
    from .app import make_http_client, open_session
    from .awards import scan_awards
    from .cli import _writer, settings

    st = settings()
    if not st.awards.enabled:
        typer.secho("awards: monitoring is off (awards.enabled: false). The CPPP Result of Tenders page shows only "
                    "a CAPTCHA search form, which Chanakya does not submit. Use `chanakya awards import <file>` "
                    "for a page or CSV saved by a person.", fg="yellow", err=True)
        raise typer.Exit(1)
    with _writer(st, "awards"), open_session(st) as s:
        client = make_http_client(st)
        try:
            res = scan_awards(st, s, client, max_pages=max_pages)
        finally:
            client.close()
    c = res.counts
    typer.secho(f"awards scan: {res.status.upper()} pages={res.pages} seen={c.seen} new={c.new} updated={c.updated} "
                f"unchanged={c.unchanged} malformed={res.malformed}",
                fg={"ok": "green", "blocked": "yellow"}.get(res.status, "red"))
    if res.note:
        typer.echo(f"  {res.note}")
    if res.error:
        typer.secho(f"  {res.error}", fg="yellow" if res.status == "blocked" else "red")
    raise typer.Exit({"ok": 0, "blocked": 3}.get(res.status, 2))


@awards_app.command("import")
def awards_import(path: Path) -> None:
    """Store awards from a file a person saved: a CPPP result page (.html) or a CSV. No network."""
    from .app import open_session
    from .awards import records_from_file, store_awards
    from .cli import _writer, settings
    from .connectors.base import ConnectorError
    from .http import BlockedError

    st = settings()
    if not path.is_file():
        typer.secho(f"awards import: no such file {path}", fg="red", err=True)
        raise typer.Exit(1)
    try:
        records, malformed = records_from_file(path, st)
    except (BlockedError, ConnectorError, ValueError) as exc:
        typer.secho(f"awards import: {type(exc).__name__}: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    with _writer(st, "awards"), open_session(st) as s:
        c = store_awards(s, records)
        s.commit()
    typer.echo(f"awards import: seen={c.seen} new={c.new} updated={c.updated} unchanged={c.unchanged} malformed={malformed}")


@awards_app.command("test")
def awards_test() -> None:
    """Fetch and parse the first award listing page (no database writes). The awards
    equivalent of `chanakya sources test`: run it before changing verification_status."""
    from .app import make_http_client
    from .cli import settings
    from .connectors.cppp_awards import CPPPAwardsConnector

    st = settings()
    client = make_http_client(st)
    try:
        h = CPPPAwardsConnector(st.awards, client).health_check()
    finally:
        client.close()
    typer.secho(f"{st.awards.source_id:<22} {'OK ' if h.ok else 'FAIL'} {h.detail} ({h.sample_count} records, {h.latency_ms} ms)",
                fg="green" if h.ok else "red")
    raise typer.Exit(0 if h.ok else 2)


@awards_app.command("report")
def awards_report(top: Annotated[int, typer.Option(help="How many winners to list")] = 10) -> None:
    """Print awards for notices already in the book, then frequent winners and value
    ranges for awards whose title matched a Wayam capability."""
    from .app import open_session
    from .awards import build_report, format_report
    from .cli import settings
    from .pipeline.knowledge import load_knowledge_base

    st = settings()
    with open_session(st) as s:
        report = build_report(s, load_knowledge_base(st.app.capabilities_file), st, top=top)
        for line in format_report(report):
            typer.echo(line)
