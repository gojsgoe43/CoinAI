#!/usr/bin/env python3
"""
APEX Trading AI — CLI Entry Point

Commands:
  scan              Scan top-20 Bitget USDT perp pairs and output signals
  signal <PAIR>     Analyse a specific pair (e.g. BTC/USDT:USDT)
  status            Show current risk manager status and open positions

Environment variables:
  BITGET_API_KEY        Bitget API key
  BITGET_SECRET         Bitget API secret
  BITGET_PASSPHRASE     Bitget API passphrase
  LOG_LEVEL             Logging level (default: INFO)

Examples:
  python main.py scan
  python main.py scan --pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT
  python main.py signal BTC/USDT:USDT
  python main.py status
"""

import argparse
import json
import logging
import sys

# Config must be imported first to initialise logging
from apex import config  # noqa: F401

logger = logging.getLogger(__name__)

DISCLAIMER = """
╔══════════════════════════════════════════════════════════════╗
║                     ⚠  DISCLAIMER  ⚠                        ║
║                                                              ║
║  APEX는 교육 및 분석 목적의 AI입니다.                         ║
║  모든 선물거래는 원금 손실 위험이 있으며,                     ║
║  최종 투자 결정의 책임은 사용자 본인에게 있습니다.            ║
║  절대 감당할 수 없는 금액으로 거래하지 마세요.                ║
╚══════════════════════════════════════════════════════════════╝
"""


def get_client():
    """Initialise and return a BitgetClient."""
    from apex.bitget_client import BitgetClient
    return BitgetClient(
        api_key=config.BITGET_API_KEY,
        secret=config.BITGET_SECRET,
        passphrase=config.BITGET_PASSPHRASE,
    )


def cmd_scan(args):
    """Run a full market scan."""
    from apex.risk_manager import RiskManager
    from apex.scanner import Scanner

    print(DISCLAIMER)
    print(f"  APEX v{__import__('apex').__version__} — Initialising market scan...\n")

    client = get_client()
    rm = RiskManager(account_balance=args.balance)
    scanner = Scanner(client, rm, n_pairs=args.top)

    pairs = args.pairs if args.pairs else None

    signals = scanner.scan(pairs=pairs, show_no_trades=args.show_all)
    scanner.print_scan_results(signals, use_color=not args.no_color)

    if args.json:
        from apex.signal_formatter import signal_to_dict
        output = [signal_to_dict(s) for s in signals]
        print(json.dumps(output, indent=2))


def cmd_signal(args):
    """Analyse a single trading pair."""
    from apex.risk_manager import RiskManager
    from apex.scanner import Scanner
    from apex.signal_formatter import format_signal

    print(DISCLAIMER)

    symbol = args.pair
    # Normalise symbol
    if ":" not in symbol and symbol.endswith("USDT"):
        symbol = symbol.replace("USDT", "/USDT:USDT")

    print(f"  APEX — Analysing {symbol}...\n")

    client = get_client()
    rm = RiskManager(account_balance=args.balance)
    scanner = Scanner(client, rm)

    signal = scanner.scan_single(symbol)
    print(format_signal(signal, use_color=not args.no_color))

    if args.json:
        from apex.signal_formatter import signal_to_dict
        print(json.dumps(signal_to_dict(signal), indent=2))


def cmd_status(args):
    """Display current risk manager status."""
    from apex.risk_manager import RiskManager

    rm = RiskManager(account_balance=args.balance)

    # Optionally sync from exchange
    if args.sync and config.BITGET_API_KEY:
        try:
            client = get_client()
            equity = client.get_usdt_equity()
            if equity > 0:
                rm.sync_balance(equity)
                print(f"  [sync] Account balance from exchange: {equity:.2f} USDT\n")
        except Exception as exc:
            logger.warning("Could not sync balance from exchange: %s", exc)

    status = rm.get_status_report()

    print(f"\n{'═' * 60}")
    print("  APEX RISK MANAGER STATUS")
    print(f"{'═' * 60}")
    print(f"  Account Balance      : {status['account_balance']:>12.2f} USDT")
    print(f"  Daily Start Equity   : {status['daily_start_equity']:>12.2f} USDT")
    print(f"  Weekly Start Equity  : {status['weekly_start_equity']:>12.2f} USDT")
    print(f"{'─' * 60}")
    print(f"  Daily PnL            : {status['daily_pnl']:>+12.2f} USDT")
    print(f"  Daily Drawdown       : {status['daily_drawdown_pct']:>11.2f}%  (limit: {status['daily_drawdown_limit_pct']:.0f}%)")
    print(f"  Weekly PnL           : {status['weekly_pnl']:>+12.2f} USDT")
    print(f"  Weekly Drawdown      : {status['weekly_drawdown_pct']:>11.2f}%  (limit: {status['weekly_drawdown_limit_pct']:.0f}%)")
    print(f"{'─' * 60}")
    print(f"  Open Positions       : {status['open_positions']}/{status['max_positions']}")
    print(f"  Trading Halted       : {'⛔ YES — ' + status['halt_reason'] if status['trading_halted'] else '✅ NO'}")
    print(f"{'─' * 60}")

    if status["positions"]:
        print("  Open Position Details:")
        for pos in status["positions"]:
            pnl_str = f"{pos['unrealized_pnl']:+.2f} USDT"
            print(
                f"    {pos['symbol']:<20} {pos['direction']:<6} "
                f"entry={pos['entry_price']:.4f}  "
                f"sl={pos['stop_loss']:.4f}  "
                f"upnl={pnl_str}  "
                f"lev={pos['leverage']}x  grade={pos['grade']}"
            )
    else:
        print("  No open positions.")

    print(f"{'═' * 60}\n")

    if args.json:
        print(json.dumps(status, indent=2))


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apex",
        description="APEX — Adaptive Predictive EXecution Trading AI for Bitget",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--balance", type=float, default=10_000.0,
        help="Account balance in USDT for position sizing (default: 10000)"
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable colored terminal output"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Also print JSON output after the formatted signal"
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # ---- scan ----
    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan top Bitget USDT perpetual pairs for trade signals",
    )
    scan_parser.add_argument(
        "--top", type=int, default=20,
        help="Number of top pairs to scan (default: 20)"
    )
    scan_parser.add_argument(
        "--pairs", nargs="+", metavar="PAIR",
        help="Override pairs to scan (e.g. BTC/USDT:USDT ETH/USDT:USDT)"
    )
    scan_parser.add_argument(
        "--show-all", action="store_true",
        help="Show no-trade results alongside valid signals"
    )

    # ---- signal ----
    sig_parser = subparsers.add_parser(
        "signal",
        help="Analyse a specific trading pair",
    )
    sig_parser.add_argument(
        "pair", metavar="PAIR",
        help="Trading pair symbol (e.g. BTC/USDT:USDT or BTCUSDT)"
    )

    # ---- status ----
    status_parser = subparsers.add_parser(
        "status",
        help="Show current risk manager state and open positions",
    )
    status_parser.add_argument(
        "--sync", action="store_true",
        help="Sync account balance from the exchange before displaying"
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "scan": cmd_scan,
        "signal": cmd_signal,
        "status": cmd_status,
    }

    try:
        dispatch[args.command](args)
    except KeyboardInterrupt:
        print("\n  [APEX] Interrupted by user.")
        sys.exit(0)
    except Exception as exc:
        logger.exception("Unhandled error in APEX: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
