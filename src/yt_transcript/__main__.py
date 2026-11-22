import argparse
import asyncio
import json
import logging
import sys

from .captions import length_text, parse_language_param
from .config import Settings
from .errors import ApiError
from .ids import parse_video_id
from .services import open_services


async def _fetch(settings: Settings, args: argparse.Namespace) -> dict:
    async with open_services(settings) as services:
        vid = parse_video_id(args.video)
        res = await services.transcripts.get_transcript(vid, parse_language_param(args.language), True)
    out = {
        "video_id": res.video_id,
        "language": res.language,
        "served_by": res.served_by,
        "cache": "HIT" if res.cache_hit else "MISS",
        "segments_count": len(res.segments),
        "first_segment": res.segments[0] if res.segments else None,
        "length_seconds": res.data.length_seconds,
        "lengthText": length_text(res.data.length_seconds),
        "title": res.data.title,
    }
    if args.full:
        out["transcript"] = res.segments
    return out


def main() -> None:
    parser = argparse.ArgumentParser(prog="yt-transcript")
    sub = parser.add_subparsers(dest="command", required=False)
    sub.add_parser("serve", help="Run the HTTP API (default)")
    sub.add_parser("mcp", help="Run as an MCP server over stdio")
    f = sub.add_parser("fetch", help="Fetch one transcript (diagnostics)")
    f.add_argument("video", help="YouTube URL or video ID")
    f.add_argument("--language", help="Comma-separated language codes")
    f.add_argument("--clients", help="Comma-separated InnerTube player clients")
    f.add_argument("--no-ytdlp", action="store_true", help="Disable yt-dlp fallback")
    f.add_argument("--no-cache", action="store_true", help="Disable the cache")
    f.add_argument("--full", action="store_true", help="Include the full transcript")
    args = parser.parse_args()

    settings = Settings()
    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "mcp":
        from .mcp_server import create_server

        create_server(settings).run("stdio")
        return

    if args.command in (None, "serve"):
        import uvicorn

        uvicorn.run(
            "yt_transcript.main:app",
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
        )
        return

    update: dict = {}
    if args.clients:
        update["yt_player_clients"] = args.clients
    if args.no_ytdlp:
        update["ytdlp_fallback"] = False
    if args.no_cache:
        update["cache_enabled"] = False
    settings = settings.model_copy(update=update)
    try:
        out = asyncio.run(_fetch(settings, args))
    except ApiError as err:
        print(json.dumps({"status": err.status, **err.body()}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
