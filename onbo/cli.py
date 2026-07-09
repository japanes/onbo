"""onbo command-line entry point: serve / kb / about / scan."""
from __future__ import annotations

import argparse
import asyncio

from . import __version__
from .config import load_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onbo", description="Open-source onboarding assistant")
    parser.add_argument("--version", action="version", version=f"onbo {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run a channel (telegram|web)")
    serve.add_argument("channel", choices=["telegram", "web"])

    about = sub.add_parser("about", help="Index the self-docs into the public `about` collection")

    scan = sub.add_parser("scan", help="Draft an actions.yaml from a target project (needs review)")
    scan.add_argument("path", help="Path to the target project")

    kb = sub.add_parser("kb", help="Knowledge-base management")
    kb_sub = kb.add_subparsers(dest="kb_command", required=True)

    add_doc = kb_sub.add_parser("add-doc", help="Ingest a file/dir/URL into a collection")
    add_doc.add_argument("path")
    add_doc.add_argument("--collection", required=True)
    add_doc.add_argument("--department", default=None)
    add_doc.add_argument("--roles", nargs="*", default=None)

    add_qa = kb_sub.add_parser("add-qa", help="Add a curated Q&A pair")
    add_qa.add_argument("question")
    add_qa.add_argument("answer")
    add_qa.add_argument("--collection", default="common")
    add_qa.add_argument("--department", default=None)
    add_qa.add_argument("--roles", nargs="*", default=None)

    kb_sub.add_parser("reindex", help="Rebuild the Qdrant index from Postgres")
    kb_sub.add_parser("seed", help="Load config/seed_faq.yaml")

    return parser


async def _run(args: argparse.Namespace) -> None:
    settings = load_settings()

    if args.command == "serve":
        from .core.pipeline import Pipeline

        pipeline = Pipeline(settings)
        if args.channel == "telegram":
            from .channels.telegram import TelegramChannel

            await TelegramChannel(settings, pipeline).start()
        else:
            from .channels.web import WebChannel

            await WebChannel(settings, pipeline).start()

    elif args.command == "about":
        from .handlers.about import index_self_docs  # optional helper

        count = await index_self_docs(settings)
        print(f"Indexed {count} self-doc chunks into the `about` collection.")

    elif args.command == "scan":
        from .generator.scan import scan_project

        print(await scan_project(args.path, settings))

    elif args.command == "kb":
        from .kb.admin import KnowledgeBaseAdmin

        admin = KnowledgeBaseAdmin(settings)
        if args.kb_command == "add-doc":
            n = await admin.add_doc(args.path, args.collection, args.department, args.roles)
            print(f"Indexed {n} chunks into `{args.collection}`.")
        elif args.kb_command == "add-qa":
            await admin.add_qa(args.question, args.answer, args.collection, args.department, args.roles)
            print("Q&A added.")
        elif args.kb_command == "seed":
            n = await admin.seed()
            print(f"Seeded {n} Q&A pairs.")
        elif args.kb_command == "reindex":
            n = await admin.reindex()
            print(f"Reindexed {n} chunks.")


def main() -> None:
    args = _build_parser().parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
