import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="ClipFactory API", version="0.031")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "clipfactory_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


class DownloadRequest(BaseModel):
    url: Optional[str] = None
    video_url: Optional[str] = None
    link: Optional[str] = None
    start_sec: Optional[float] = 0
    end_sec: Optional[float] = 1800
    start: Optional[float] = None
    end: Optional[float] = None
    full_if_shorter: Optional[bool] = True


def _clean_youtube_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        raise ValueError("URL vazia.")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def _run_yt_dlp(url: str, out_base: Path, start_sec: Optional[float], end_sec: Optional[float]) -> Path:
    strategies = [
        ["--extractor-args", "youtube:player_client=tv_embedded,web_safari,web"],
        ["--extractor-args", "youtube:player_client=mweb,web_safari"],
        ["--extractor-args", "youtube:player_client=android_vr,mweb,web"],
        ["--extractor-args", "youtube:player_client=ios,mweb"],
        ["--extractor-args", "youtube:player_client=web_creator,mweb"],
        ["--extractor-args", "youtube:player_client=web_embedded,web"],
        [],
    ]
    proxy_url = os.getenv("YTDLP_PROXY", "").strip()
    last_err = ""

    for extra_args in strategies:
        for f in out_base.parent.glob(f"{out_base.name}*"):
            try:
                f.unlink()
            except Exception:
                pass

        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--geo-bypass",
            "--force-ipv4",
            "--retries", "3",
            "--fragment-retries", "3",
            "--sleep-requests", "1",
            "--merge-output-format", "mp4",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "-o", f"{out_base}.%(ext)s",
        ]
        cmd.extend(extra_args)

        if proxy_url:
            cmd.extend(["--proxy", proxy_url])

        if start_sec is not None and end_sec is not None and float(end_sec) > float(start_sec):
            s = max(0.0, float(start_sec))
            e = max(s + 1.0, float(end_sec))
            cmd.extend(["--download-sections", f"*{s}-{e}"])

        cmd.append(url)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
            if proc.returncode == 0:
                matches = sorted(
                    [p for p in out_base.parent.glob(f"{out_base.name}*") if p.is_file() and p.stat().st_size > 0],
                    key=lambda p: p.stat().st_size,
                    reverse=True,
                )
                if matches:
                    return matches[0]
            last_err = (proc.stderr or proc.stdout or "").strip()
        except subprocess.TimeoutExpired:
            last_err = "Tempo limite excedido ao processar o vídeo."
        except Exception as exc:
            last_err = str(exc)

    raise RuntimeError(last_err or "Falha ao extrair mídia com yt-dlp.")


def _remove_file(path_str: str) -> None:
    try:
        p = Path(path_str)
        if p.exists():
            p.unlink()
    except Exception:
        pass


@app.get("/")
@app.get("/health")
def health():
    yt_ver = "unknown"
    try:
        r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            yt_ver = r.stdout.strip()
    except Exception:
        pass
    return {"status": "ok", "version": "0.031", "yt_dlp": yt_ver}


@app.api_route("/download", methods=["GET", "POST"])
@app.api_route("/baixar", methods=["GET", "POST"])
@app.api_route("/api/download", methods=["GET", "POST"])
@app.api_route("/api/baixar", methods=["GET", "POST"])
@app.api_route("/video/download", methods=["GET", "POST"])
@app.api_route("/{full_path:path}", methods=["GET", "POST"])
async def download_video(
    request: Request,
    background_tasks: BackgroundTasks,
    full_path: str = "",
    req: Optional[DownloadRequest] = None,
