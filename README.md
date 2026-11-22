# youtube-search-mcp

A YouTube transcript service that runs on your own machine. You can use it as an **MCP server**
for AI agents, or as an HTTP API that copies the
[TranscriptAPI.com v2](https://transcriptapi.com/docs/api/) REST API. It covers transcripts, video
info, search, channel lookups (resolve, search, videos, latest via RSS) and playlist listing. It
needs no API key and has no usage credits. Requests go straight from your machine to YouTube.

> **AI agent installing this?** Follow [Install as an MCP server](#install-as-an-mcp-server).
> The installed command is called `yt-transcript`.

## Install as an MCP server

You don't need to clone the repo. [`uvx`](https://docs.astral.sh/uv/) fetches the code, installs
Python 3.12 if it's missing, and runs the server over stdio.

1. **Make sure `uv` is installed.** Run `uv --version`. If that fails, install it:
   - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`, then open a new shell or
     run `source $HOME/.local/bin/env`
   - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. **Smoke test.** This should print one JSON line containing `"language": "en"` and
   `"segments_count"`. The first run takes about 30 seconds while dependencies install.
   ```sh
   uvx --from git+https://github.com/PinkiNice/youtube-search-mcp yt-transcript fetch dQw4w9WgXcQ
   ```
3. **Register the server with your MCP client.** The command is always
   `uvx --from git+https://github.com/PinkiNice/youtube-search-mcp yt-transcript mcp`.
   - Claude Code:
     ```sh
     claude mcp add yt-transcript --scope user -- uvx --from git+https://github.com/PinkiNice/youtube-search-mcp yt-transcript mcp
     ```
   - Codex CLI:
     ```sh
     codex mcp add yt-transcript -- uvx --from git+https://github.com/PinkiNice/youtube-search-mcp yt-transcript mcp
     ```
   - Clients configured with JSON: Claude Desktop (`claude_desktop_config.json`), Cursor
     (`~/.cursor/mcp.json`), Windsurf, Cline, Gemini CLI (`~/.gemini/settings.json`) and others.
     VS Code (`.vscode/mcp.json`) uses `"servers"` instead of `"mcpServers"` and adds
     `"type": "stdio"`.
     ```json
     {
       "mcpServers": {
         "yt-transcript": {
           "command": "uvx",
           "args": ["--from", "git+https://github.com/PinkiNice/youtube-search-mcp", "yt-transcript", "mcp"]
         }
       }
     }
     ```
   - Codex `~/.codex/config.toml`, if you prefer editing the file:
     ```toml
     [mcp_servers.yt-transcript]
     command = "uvx"
     args = ["--from", "git+https://github.com/PinkiNice/youtube-search-mcp", "yt-transcript", "mcp"]
     ```
4. **Restart or reload the client.** Check that the `yt-transcript` server lists the tools below.
   If a GUI app can't find `uvx`, put its absolute path in `command`. Run `which uvx` (macOS/Linux)
   or `where uvx` (Windows) to find it.

Settings are optional and passed as environment variables: `-e KEY=value` for `claude mcp add`, or
an `"env": {…}` object in JSON configs. The useful ones are `YT_PROXIES`, `YT_COOKIES_FILE` and
`LOG_LEVEL=WARNING` (quieter stderr). See [`.env.example`](.env.example) for the full list. The cache
is stored at `~/.cache/yt-transcript/cache.sqlite3`.

To update to the latest commit, run the smoke test once with `uvx --refresh --from …`. To pin a
version, use `git+https://github.com/PinkiNice/youtube-search-mcp@<tag-or-commit>`.

### Tools

All tools are read-only. Errors come back as JSON `{"detail", "code"}`, the same codes the HTTP API
uses.

| Tool | Purpose |
|---|---|
| `get_transcript` | Transcript of a video. Defaults to plain text without timestamps. Options: `language`, `format=json`, `include_timestamp`, `include_metadata` |
| `get_video_info` | Title, author, thumbnail and the transcript languages available |
| `search_youtube` | Search for videos, channels, playlists or movies, with sort and filters |
| `resolve_channel` | Turn a `@handle` or URL into a `UC…` channel ID |
| `search_channel` | Search for videos within one channel |
| `list_channel_videos` | A channel's uploads, shorts or streams, optionally sorted |
| `get_channel_latest` | The 15 newest uploads, with exact dates and view counts (RSS) |
| `list_playlist_videos` | The videos in a playlist |

Listing tools return a `continuation_token`. To get the next page, pass it as `continuation` on its
own.

## Run from a clone (HTTP API, development)

```sh
uv sync
uv run yt-transcript serve            # HTTP API on 127.0.0.1:8000 (Swagger UI at /docs)
uv run yt-transcript mcp              # MCP server over stdio
uv run yt-transcript fetch <video>    # fetch one transcript from the command line, for diagnostics
```

`fetch` options: `--language en,de`, `--clients IOS,ANDROID`, `--no-ytdlp`, `--no-cache` and
`--full` (prints the whole transcript). It prints one JSON object and exits with status 1 on an
API error.

Settings come from environment variables or a `.env` file. See [`.env.example`](.env.example) for
every variable and its default. When `API_KEY` is set, each `/api/v2` request must send
`Authorization: Bearer <API_KEY>`.

Tests run offline from saved fixtures: `uv run pytest -q`.

## Endpoints

```sh
B=localhost:8000/api/v2/youtube
curl "$B/transcript?video_url=https://youtu.be/dQw4w9WgXcQ&format=text&send_metadata=true&language=en,asr"
curl "$B/info?video_url=dQw4w9WgXcQ"
curl "$B/search?q=climate%20change&type=video&sort=views&upload_date=month&duration=long&features=hd,cc"
curl "$B/channel/resolve?input=@TED"
curl "$B/channel/search?channel=@TED&q=AI"
curl "$B/channel/videos?channel=@TED&tab=videos&sort=popular"
curl "$B/channel/latest?channel=@TED"
curl "$B/playlist/videos?playlist=PL68492A0FC99185C7"
curl localhost:8000/health
```

Listing endpoints return a `continuation_token`. Pass it back as `continuation=` to get the next
page.

Language codes: `en` picks the manual English track. If there is none, it falls back to
auto-generated English, unless you pass `language_fallback=false`. `asr` picks any auto-generated
track, and `asr-en` picks auto-generated English.

Transcript responses carry two extra headers. `X-Cache-Status` is `HIT` or `MISS`. `X-Served-By`
says where the transcript came from: `innertube:<CLIENT>`, `yt-dlp` or `cache`.

Errors look like `{"detail": "...", "code": "..."}`. A `408 temporarily_blocked` response means
YouTube blocked every attempt. Retry after the `Retry-After` interval.

## When YouTube blocks you

1. Check `GET /health`. The `cooldowns` list shows which client and proxy pairs are paused after a
   bot check or rate limit. Check the `X-Served-By` header to see which strategy still works.
2. Update yt-dlp, which is the fallback: `uv lock --upgrade-package yt-dlp && uv sync`.
3. Set `YT_PROXIES` to one or more residential proxies, separated by commas. Requests rotate
   across them.
4. Export a `cookies.txt` from a throwaway YouTube account and set `YT_COOKIES_FILE`. The yt-dlp
   fallback uses it. It also unlocks age-restricted videos.
5. If every client returns `UNPLAYABLE` or `ERROR` for videos that should work, refresh the client
   versions in `innertube.PLAYER_CLIENTS`. Copy them from yt-dlp's `INNERTUBE_CLIENTS` in
   `.venv/lib/python3.12/site-packages/yt_dlp/extractor/youtube/_base.py`.
6. If stderr shows `reason=empty_body` for every client, YouTube is demanding PO tokens. Run
   `uv add bgutil-ytdlp-pot-provider` and
   `docker run -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider` so the yt-dlp fallback can
   mint tokens.

## Disclaimer

This project is not affiliated with or endorsed by YouTube or Google. It reads public data through
the same endpoints YouTube's own apps use. Automated access may conflict with YouTube's Terms of
Service, so use it for personal, low-volume purposes and at your own risk.

## License

[MIT](LICENSE)
