"""CLI entry point for the spot-perp arb scanner."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich import box

from .config import settings
from .timeutil import format_utc
from .venues import ALL_VENUES

console = Console()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )


def _build_table(df) -> Table:
    table = Table(
        title="Spot-Perp Arb Opportunities",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )

    col_config = [
        ("Rank", "right"),
        ("Exchange", "left"),
        ("Symbol", "left"),
        ("Funding Latest", "right"),
        ("Funding Avg 24h", "right"),
        ("Window (h)", "right"),
        ("Perp Bid", "right"),
        ("Bid Size USDT", "right"),
        ("Spot Price", "right"),
        ("Basis USD", "right"),
        ("Basis bps", "right"),
        ("Est. Edge", "right"),
        ("Notes", "left"),
    ]
    for name, justify in col_config:
        table.add_column(name, justify=justify)

    def _fmt(v, fmt=".6f"):
        return f"{v:{fmt}}" if v is not None else "—"

    for _, row in df.iterrows():
        funding_avg = row.get("funding_avg_24h")
        color = "green" if (funding_avg or 0) > 0 else "red" if (funding_avg or 0) < 0 else "white"

        table.add_row(
            str(int(row["rank"])),
            row["exchange"],
            row["symbol"],
            f"[{color}]{_fmt(row.get('funding_latest'), '.6f')}[/{color}]",
            f"[{color}]{_fmt(row.get('funding_avg_24h'), '.6f')}[/{color}]",
            _fmt(row.get("funding_window_hours"), ".1f"),
            _fmt(row.get("perp_bid"), ".4f"),
            _fmt(row.get("perp_bid_size_usdt"), ".2f"),
            _fmt(row.get("spot_price"), ".4f"),
            _fmt(row.get("basis_usd"), ".4f"),
            _fmt(row.get("basis_bps"), ".2f"),
            _fmt(row.get("est_gross_edge"), ".4f"),
            str(row.get("notes") or ""),
        )

    return table


def _run_command(args: argparse.Namespace) -> None:
    from . import core

    _setup_logging(settings.LOG_LEVEL)

    venues = args.venues if args.venues else ALL_VENUES
    unknown = [v for v in venues if v not in ALL_VENUES]
    if unknown:
        console.print(f"[red]Unknown venues: {unknown}. Valid: {ALL_VENUES}[/red]")
        sys.exit(1)

    console.print("[bold]Fetching funding rates and orderbook data…[/bold]")

    df, status = core.run(
        top_n=args.top,
        min_funding=args.min_funding,
        notional_usdt=args.notional,
        venues=venues,
    )

    if df.empty:
        console.print("[yellow]No opportunities found matching filters.[/yellow]")
    else:
        console.print(_build_table(df))

    # Write CSV
    csv_path = Path(settings.OUT_DIR) / "latest.csv"
    df.to_csv(csv_path, index=False)
    console.print(f"\n[dim]CSV written to {csv_path}[/dim]")
    console.print(f"[dim]DB: {settings.DB_PATH}[/dim]")
    console.print(f"[dim]Refreshed at {format_utc(status['last_refresh'])}[/dim]")

    if status["partial_failures"]:
        console.print("\n[yellow]Partial failures:[/yellow]")
        for msg in status["partial_failures"]:
            console.print(f"  [yellow]• {msg}[/yellow]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spot-Perp Funding Arbitrage Scanner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Scan for arb opportunities")
    run_parser.add_argument("--top", type=int, default=30, help="Max rows to display")
    run_parser.add_argument(
        "--min-funding",
        type=float,
        default=0.0,
        dest="min_funding",
        help="Minimum 24h avg funding rate to include",
    )
    run_parser.add_argument(
        "--notional",
        type=float,
        default=200.0,
        help="Notional USDT for gross edge estimate",
    )
    run_parser.add_argument(
        "--venues",
        nargs="+",
        default=None,
        choices=ALL_VENUES,
        metavar="VENUE",
        help=f"Venues to include. Choices: {ALL_VENUES}",
    )

    args = parser.parse_args()

    if args.command == "run":
        _run_command(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
