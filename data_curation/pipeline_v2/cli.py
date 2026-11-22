import argparse
import re
import sys
from pathlib import Path

from data_curation.pipeline_v2.analysis import analyze_run, retry_failed
from data_curation.pipeline_v2.publication import publish_run
from data_curation.pipeline_v2.recipe import build_analysis_recipe
from data_curation.pipeline_v2.runs import prepare_run

OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Prepare and publish document metadata runs.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--source", action="append")
    for name in ("analyze", "retry-failed", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    if args.command != "prepare" and not re.fullmatch(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{4}", args.run):
        parser.error(
            f"{args.command}: --run {args.run!r} must have the form "
            "YYYYMMDD_HHMMSS_<4 lowercase hexadecimal characters>"
        )
    try:
        if args.command == "prepare":
            selection = tuple(args.source) if args.source is not None else None
            run_dir = prepare_run(
                args.manifest,
                recipe=build_analysis_recipe(),
                output_root=OUTPUT_ROOT,
                selected_source_paths=selection,
            )
            print(run_dir.name)
            print(run_dir)
        else:
            run_dir = OUTPUT_ROOT / args.run
            if args.command == "analyze":
                print(analyze_run(run_dir))
            elif args.command == "retry-failed":
                print(retry_failed(run_dir))
            else:
                revision = publish_run(run_dir)
                print(revision)
                print(revision / "report.md")
    except (ValueError, OSError, RuntimeError) as error:
        print(f"{args.command}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print(f"{args.command}: interrupted", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
