"""pjvoice command line.

  pjvoice tools [--port 8100]                 run the reference booking tool server
  pjvoice control [--port 8000]               run the control plane
  pjvoice load examples/demo-salon.yaml       upload an assistant to the control plane
  pjvoice chat examples/demo-salon.yaml       talk to an assistant in the terminal (text)
  pjvoice eval examples/demo-salon.yaml evals/scenarios [--tools-app pkg.module:app]
  pjvoice worker dev                          run the LiveKit voice worker
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid


def _tools(args: argparse.Namespace) -> None:
    import uvicorn

    from .booking.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)


def _control(args: argparse.Namespace) -> None:
    import uvicorn

    from .control.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)


def _load(args: argparse.Namespace) -> None:
    import httpx

    from .engine.config import AssistantConfig

    cfg = AssistantConfig.from_file(args.config)
    key = os.environ.get("PJVOICE_API_KEY", "")
    resp = httpx.put(
        f"{args.url.rstrip('/')}/v1/assistants/{cfg.id}",
        json=json.loads(cfg.model_dump_json()),
        headers={"Authorization": f"Bearer {key}"} if key else {},
    )
    if resp.status_code >= 400:
        sys.exit(f"upload failed: {resp.status_code} {resp.text}")
    print(f"loaded assistant {cfg.id} ({', '.join(cfg.phone_numbers) or 'no phone numbers'})")


async def _chat_loop(args: argparse.Namespace) -> None:
    from .engine.config import AssistantConfig
    from .text.runner import TextRunner

    cfg = AssistantConfig.from_file(args.config)
    runner = TextRunner()
    convo = runner.start(cfg, f"cli-{uuid.uuid4().hex[:8]}", args.caller)
    if cfg.greeting:
        print(f"agent> {cfg.greeting}")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        result = await runner.turn(convo, text)
        print(f"agent> {result.reply}" + (f"   [{result.stage}]" if args.show_stage else ""))
        if result.transfer_to:
            print(f"[transferred to {result.transfer_to}]")
            break
        if result.ended:
            print("[call ended]")
            break
    if args.show_tools:
        print(json.dumps(convo.ctx.tool_log, indent=2))


async def _eval(args: argparse.Namespace) -> int:
    from .engine.config import AssistantConfig
    from .evals.harness import format_result, load_scenarios, run_scenario, summarize

    cfg = AssistantConfig.from_file(args.config)
    tools_app = None
    if args.tools_app:
        import importlib

        module, _, attr = args.tools_app.partition(":")
        sys.path.insert(0, os.getcwd())
        tools_app = getattr(importlib.import_module(module), attr or "app")
    results = []
    for sc in load_scenarios(args.scenarios):
        for _ in range(args.repeat):
            r = await run_scenario(cfg, sc, caller_model=args.caller_model, tools_app=tools_app)
            print(format_result(r, verbose=args.verbose), flush=True)
            results.append(r)
    s = summarize(results)
    print(f"\n{s['passed']}/{s['scenarios']} passed, mean score {s['mean_score']:.2f}")
    return 0 if s["passed"] == s["scenarios"] else 1


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "worker":
        # Hand the rest of the command line to the LiveKit CLI.
        try:
            from .voice import worker
        except ImportError:
            sys.exit("Voice extra not installed: pip install 'pjvoice[voice]'")
        sys.argv = ["pjvoice-worker", *argv[1:]]
        worker.main()
        return

    logging.basicConfig(level=os.environ.get("PJVOICE_LOG_LEVEL", "WARNING"))
    p = argparse.ArgumentParser(prog="pjvoice", description="PJ Voice: open source AI phone agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tools", help="run the reference booking tool server")
    t.add_argument("--host", default="127.0.0.1")
    t.add_argument("--port", type=int, default=8100)

    c = sub.add_parser("control", help="run the control plane")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8000)

    ld = sub.add_parser("load", help="upload an assistant config to the control plane")
    ld.add_argument("config")
    ld.add_argument("--url", default=os.environ.get("PJVOICE_CONTROL_URL", "http://localhost:8000"))

    ch = sub.add_parser("chat", help="talk to an assistant in the terminal")
    ch.add_argument("config")
    ch.add_argument("--caller", default="+15550142", help="caller phone number to simulate")
    ch.add_argument("--show-stage", action="store_true")
    ch.add_argument("--show-tools", action="store_true")

    ev = sub.add_parser("eval", help="run simulated-caller scenarios")
    ev.add_argument("config")
    ev.add_argument("scenarios", help="scenario file or directory")
    ev.add_argument("--caller-model", default="openai/gpt-4.1-mini")
    ev.add_argument("--repeat", type=int, default=1)
    ev.add_argument("--tools-app", help="module:app of your own ASGI tool server (default: demo booking server)")
    ev.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("worker", help="run the LiveKit voice worker (pass LiveKit CLI args after it)")

    args = p.parse_args(argv)
    if args.cmd == "tools":
        _tools(args)
    elif args.cmd == "control":
        _control(args)
    elif args.cmd == "load":
        _load(args)
    elif args.cmd == "chat":
        asyncio.run(_chat_loop(args))
    elif args.cmd == "eval":
        sys.exit(asyncio.run(_eval(args)))


if __name__ == "__main__":
    main()
