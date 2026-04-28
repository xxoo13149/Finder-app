from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .analysis import run_pipeline
from .config import DEFAULT_CONFIG_PATH, apply_overrides, load_config
from .env import load_project_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Polymarket weather wallet screening and strategy analysis tool."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to config file. Defaults to configs/default_config.json.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to a timestamped folder under artifacts/.",
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=None,
        help="Override wallet_filter.target_count from config.",
    )
    parser.add_argument(
        "--fetch-limit",
        type=int,
        default=None,
        help="Override leaderboard.fetch_limit for the first leaderboard pull.",
    )
    parser.add_argument(
        "--max-fetch-limit",
        type=int,
        default=None,
        help="Override leaderboard.max_fetch_limit for the total candidate cap.",
    )
    parser.add_argument(
        "--max-weather-events",
        type=int,
        default=None,
        help="Override weather.max_events from config.",
    )
    parser.add_argument(
        "--max-wallet-offset",
        type=int,
        default=None,
        help="Override pagination.max_offset for wallet pagination.",
    )
    parser.add_argument(
        "--concurrent-wallets",
        type=int,
        default=None,
        help="Override analysis.concurrent_wallets from config.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=None,
        help="Enable verbose CLI output.",
    )
    parser.add_argument(
        "--disable-cache",
        action="store_true",
        help="Disable local response cache.",
    )
    parser.add_argument(
        "--enable-chain-validation",
        action="store_true",
        help="Enable Polygon chain validation for labels that require on-chain evidence.",
    )
    parser.add_argument(
        "--chain-api-key-env",
        default=None,
        help="Environment variable containing the Etherscan/Polygonscan API key.",
    )
    return parser


def resolve_output_dir(raw_output_dir: str | None) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return Path("artifacts") / f"polymarket-weather-{timestamp}"


def main() -> None:
    args = build_parser().parse_args()
    load_project_env()
    config = load_config(args.config)
    config = apply_overrides(
        config,
        target_count=args.target_count,
        fetch_limit=args.fetch_limit,
        max_fetch_limit=args.max_fetch_limit,
        max_weather_events=args.max_weather_events,
        max_wallet_offset=args.max_wallet_offset,
        concurrent_wallets=args.concurrent_wallets,
        verbose=args.verbose,
        use_cache=False if args.disable_cache else None,
        enable_chain_validation=True if args.enable_chain_validation else None,
        chain_api_key_env=args.chain_api_key_env,
    )
    output_dir = resolve_output_dir(args.output_dir)
    result = run_pipeline(config=config, output_dir=output_dir)
    verbose = bool(config.get("runtime", {}).get("verbose", False))
    if verbose:
        print(f"Output directory: {output_dir}")
        print(f"Config: {args.config}")
    print(f"Report: {result['report_path']}")
    print(f"Selected wallets: {result['selected_wallet_count']}")
    if result["errors"]:
        print(f"Errors: {len(result['errors'])}")
        if verbose:
            for error in result["errors"][:10]:
                print(f"- {error['wallet']}: {error['error']}")


if __name__ == "__main__":
    main()
