"""`rag-ingest` CLI — index a bot's declared sources into its collection.

Usage:
    rag-ingest <bot_id> [--wait]

Builds the same in-process RagEngine the chatbot uses, ensures the bot's
collection exists, and ingests every source declared in the bot's YAML `rag:`
block. `--wait` (default on for this CLI) polls each job to completion and
prints the counts — handy for a deterministic "index then demo" flow.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from src.chatbot.core.bot_config_store import load_bot_config  # noqa: E402
from src.chatbot.core.rag_runtime import bootstrap_bot_rag, build_rag_engine  # noqa: E402


async def _run(bot_id: str, wait: bool) -> int:
    bot_config = load_bot_config(bot_id)
    if "rag" not in bot_config.enabled_skills:
        print(f"Bot '{bot_id}' does not enable the rag skill.", file=sys.stderr)
        return 2
    if not bot_config.rag.collection:
        print(f"Bot '{bot_id}' has no rag.collection configured.", file=sys.stderr)
        return 2

    engine, db_engine = await build_rag_engine()
    await engine.start()
    try:
        job_ids = await bootstrap_bot_rag(engine, bot_config, ingest=True, wait=wait)
        if not job_ids:
            print(f"No sources declared for bot '{bot_id}'; collection ensured.")
            return 0
        for job_id in job_ids:
            job = await engine.job_status(job_id)
            status = job.status.value if job else "<missing>"
            counts = job.counts if job else {}
            print(f"job={job_id} status={status} counts={counts}")
            if job and job.errors:
                for err in job.errors:
                    print(f"  error: {err}", file=sys.stderr)
        ok = all(
            (await engine.job_status(j)).status.value == "succeeded"  # type: ignore[union-attr]
            for j in job_ids
        )
        return 0 if ok else 1
    finally:
        await engine.stop()
        await db_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="rag-ingest", description=__doc__)
    parser.add_argument("bot_id", help="Bot id whose sources to ingest, e.g. telecom_support")
    parser.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        help="Fire-and-enqueue without waiting for jobs to finish.",
    )
    parser.set_defaults(wait=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.bot_id, args.wait)))


if __name__ == "__main__":
    main()
