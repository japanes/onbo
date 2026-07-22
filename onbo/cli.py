"""onbo command-line entry point: serve / kb / about / scan."""
from __future__ import annotations

import argparse
import asyncio

from . import __version__
from .config import ConfigError, check_env_keys, load_settings


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

    kb_import = kb_sub.add_parser(
        "import", help="Import Q&A pairs from a seed_faq.yaml-shaped file"
    )
    kb_import.add_argument("path", help="Path to a YAML file with a top-level `qa:` list")

    llm_export = sub.add_parser(
        "llm-export", help="Write the public llm.json manifest for static hosting"
    )
    llm_export.add_argument("--out", default="llm.json", help="Output file (default: llm.json)")

    users = sub.add_parser("users", help="User directory management")
    users_sub = users.add_subparsers(dest="users_command", required=True)
    users_sub.add_parser("seed", help="Upsert the demo users into Postgres")

    demo = sub.add_parser("demo-backend", help="Run the bundled demo product backend")
    demo.add_argument("--port", type=int, default=18100)

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
        elif args.kb_command == "import":
            n = await admin.seed(args.path)
            print(f"Imported {n} Q&A pairs from {args.path}.")
        elif args.kb_command == "reindex":
            n = await admin.reindex()
            print(f"Reindexed {n} chunks.")

    elif args.command == "llm-export":
        import json

        from .core.manifest import build_llm_manifest

        manifest = build_llm_manifest(settings)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        print(f"Wrote {args.out} ({len(manifest['qa'])} public Q&A, "
              f"{len(manifest['actions'])} actions, {len(manifest['pipelines'])} pipelines).")

    elif args.command == "users":
        if args.users_command == "seed":
            from .auth.profiles import seed_demo_users

            n = seed_demo_users(settings)
            print(f"Seeded {n} demo users." if n else "No DB available; nothing seeded.")


def main() -> None:
    args = _build_parser().parse_args()
    try:
        check_env_keys()
    except ConfigError as exc:
        raise SystemExit(f"onbo: {exc}") from None
    if args.command == "demo-backend":
        # uvicorn.run manages its own event loop, so keep it out of asyncio.run.
        from .demo.backend import run

        run(args.port)
        return
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
