"""Command line entry point.

    python -m pipebreak build            seed the warehouse and build all tasks
    python -m pipebreak tasks            list the task instances
    python -m pipebreak run MODEL...     evaluate one or more models
    python -m pipebreak report MODEL...  print the comparison table
"""

from __future__ import annotations

import argparse
import sys

from pipebreak import config, evaluate, validate
from pipebreak.corruptions import CATALOG
from pipebreak.tasks.build import build_all, build_reference, load_tasks


def cmd_build(args: argparse.Namespace) -> int:
    print(f"seeding warehouse ({args.orders:,} orders, seed {config.SEED})")
    build_reference(n_orders=args.orders)
    print(f"building {len(CATALOG)} task instances")
    tasks = build_all()
    print()
    validate.check_all()
    print(f"\n{len(tasks)} tasks written to {config.TASKS_DIR}")
    return 0


def cmd_tasks(_: argparse.Namespace) -> int:
    for task in load_tasks():
        print(f"{task.task_id:24s} {task.expected_action:8s} {task.family:22s} "
              f"{task.primary_mart}")
        print(f"    {task.symptom}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        config.api_key()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    tasks = load_tasks()
    if args.task:
        tasks = [t for t in tasks if t.task_id in args.task]
        if not tasks:
            print(f"no task matching {args.task}", file=sys.stderr)
            return 1
    for model in args.models:
        print(f"\n{model}  ({len(tasks)} tasks)")
        scores = evaluate.run_model(model, tasks)
        s = evaluate.summarise(scores)
        print(f"  -> detection {s['detection']:.0%}  repair {s['repair_rate']:.0%}  "
              f"abstention {s['abstention_rate']:.0%}  "
              f"false repair {s['false_repair_rate']:.0%}  "
              f"score {s['composite']:.3f}")
    print()
    print(evaluate.report(args.models))
    return 0


def cmd_models(_: argparse.Namespace) -> int:
    """List models this API key can actually reach."""
    from openai import OpenAI
    try:
        client = OpenAI(api_key=config.api_key(), base_url=config.GROQ_BASE_URL)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    available = sorted(m.id for m in client.models.list().data)
    configured = config.model_list()
    print(f"{len(available)} models available to this key:")
    for m in available:
        print(f"  {'*' if m in configured else ' '} {m}")
    missing = [m for m in configured if m not in available]
    if missing:
        print("\nconfigured but NOT available: " + ", ".join(missing))
        print("set PIPEBREAK_MODELS in .env to a subset of the list above")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    models = args.models or config.model_list()
    print(evaluate.report(models))
    out = evaluate.write_report(models)
    print(f"\nwritten to {out}")
    return 0


def cmd_validate_scoring(_: argparse.Namespace) -> int:
    from pipebreak import validate_scoring
    try:
        print(validate_scoring.report())
    except AssertionError as exc:
        print(f"SCORER VALIDATION FAILED\n\n{exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipebreak", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="seed the warehouse and build all tasks")
    p.add_argument("--orders", type=int, default=6000)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("tasks", help="list task instances")
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("run", help="evaluate models")
    p.add_argument("models", nargs="*", default=config.model_list())
    p.add_argument("--task", action="append", help="restrict to a task id (repeatable)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="print the comparison table")
    p.add_argument("models", nargs="*")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("models", help="list models this API key can reach")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("validate-scoring",
                       help="attack the scorer with degenerate strategies")
    p.set_defaults(func=cmd_validate_scoring)

    args = parser.parse_args(argv)
    config.ensure_dirs()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
