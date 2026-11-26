import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
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
    url: str
    start_sec: Optional[float] = 0
    end_sec: Optional[float] = 1800
    full_if_shorter: Optional[bool] = True


def _clean_youtube_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        raise ValueError("URL vazia.")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def _run_yt_dlp(url: str, out_base: Path, start_sec: Optional[float], end_sec: Optional[float], full_if_shorter: bool) -> Path:
    clients_to_try = [
        "android_vr,web_safari,web",
        "ios,android,web",
        "tv_embedded,web",
        "default",
    ]
    last_err = ""

    for client_list in clients_to_try:
        for f in out_base.parent.glob(f"{out_base.name}*"):
            try:
                f.unlink()
            except Exception:
                pass

        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--retries", "3",
            "--fragment-retries", "3",
            "--force-ipv4",
            "--merge-output-format", "mp4",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "-o", f"{out_base}.%(ext)s",
        ]

        if client_list != "default":
            cmd.extend(["--extractor-args", f"youtube:player_client={client_list}"])

        cookies_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
        cookies_b64 = os.getenv("YTDLP_COOKIES_B64", "").strip()
        temp_cookie_path = None
        if cookies_b64 and not cookies_file:
            import base64
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
            tf.write(base64.b64decode(cookies_b64))
            tf.close()
            temp_cookie_path = tf.name
            cookies_file = temp_cookie_path
        if cookies_file and os.path.isfile(cookies_file):
            cmd.extend(["--cookies", cookies_file])

        if start_sec is not None and end_sec is not None and end_sec > start_sec:
            s = max(0.0, float(start_sec))
            e = max(s + 1.0, float(end_sec))
            cmd.extend(["--download-sections", f"*{s}-{e}"])

        cmd.append(url)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
            if temp_cookie_path and os.path.exists(temp_cookie_path):
                try:
                    os.unlink(temp_cookie_path)
                except Exception:
                    pass

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


@app.post("/download")
@app.get("/download")
async def download_video(
    background_tasks: BackgroundTasks,
    req: Optional[DownloadRequest] = None,
    url: Optional[str] = Query(default=None),
    start_sec: Optional[float] = Query(default=0),
    end_sec: Optional[float] = Query(default=1800),
    full_if_shorter: Optional[bool] = Query(default=True),
):
    target_url = req.url if req and req.url else url
    s_sec = req.start_sec if req and req.start_sec is not None else start_sec
    e_sec = req.end_sec if req and req.end_sec is not None else end_sec
    full_short = req.full_if_shorter if req and req.full_if_shorter is not None else bool(full_if_shorter)

    if not target_url:
        raise HTTPException(status_code=400, detail="Informe a URL do vídeo.")

    try:
        clean_url = _clean_youtube_url(target_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_id = uuid.uuid4().hex[:12]
    out_base = DOWNLOAD_DIR / f"clip_{job_id}"

    try:
        file_path = _run_yt_dlp(clean_url, out_base, s_sec, e_sec, full_short)
    except RuntimeError as exc:
        return JSONResponse(
            status_code=502,
            content={"error": f"Falha ao baixar vídeo pela API: yt-dlp: {exc}"},
        )

    background_tasks.add_task(_remove_file, str(file_path))
    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=f"clipfactory_{job_id}.mp4",
        background=background_tasks,
    )
