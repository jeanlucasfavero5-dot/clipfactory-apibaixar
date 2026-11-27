import base64
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
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
            tf.write(base64.b64decode(cookies_b64))
            tf.close()
            temp_cookie_path = tf.name
            cookies_file = temp_cookie_path
        if cookies_file and os.path.isfile(cookies_file):
            cmd.extend(["--cookies", cookies_file])

        if start_sec is not None and end_sec is not None and float(end_sec) > float(start_sec):
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
    url: Optional[str] = Query(default=None),
    start_sec: Optional[float] = Query(default=None),
    end_sec: Optional[float] = Query(default=None),
):
    body_json = {}
    if request.method == "POST":
        try:
            body_json = await request.json()
            if not isinstance(body_json, dict):
                body_json = {}
        except Exception:
            body_json = {}

    q = request.query_params
    target_url = (
        url
        or q.get("video_url")
        or q.get("link")
        or body_json.get("url")
        or body_json.get("video_url")
        or body_json.get("link")
        or (req.url if req else None)
        or (req.video_url if req else None)
        or (req.link if req else None)
    )

    s_raw = (
        start_sec
        if start_sec is not None
        else (
            q.get("start_sec")
            or q.get("start")
            or body_json.get("start_sec")
            or body_json.get("start")
            or (req.start_sec if req and req.start_sec is not None else None)
            or (req.start if req and req.start is not None else 0)
        )
    )
    e_raw = (
        end_sec
        if end_sec is not None
        else (
            q.get("end_sec")
            or q.get("end")
            or body_json.get("end_sec")
            or body_json.get("end")
            or (req.end_sec if req and req.end_sec is not None else None)
            or (req.end if req and req.end is not None else 1800)
        )
    )

    if not target_url:
        raise HTTPException(status_code=404, detail=f"Rota ou URL não encontrada: /{full_path}")

    try:
        clean_url = _clean_youtube_url(str(target_url))
        s_sec = float(s_raw) if s_raw is not None else 0.0
        e_sec = float(e_raw) if e_raw is not None else 1800.0
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_id = uuid.uuid4().hex[:12]
    out_base = DOWNLOAD_DIR / f"clip_{job_id}"

    try:
        file_path = _run_yt_dlp(clean_url, out_base, s_sec, e_sec)
    except RuntimeError as exc:
        msg = f"Falha ao baixar vídeo pela API: yt-dlp: {exc}"
        return JSONResponse(status_code=502, content={"error": msg, "detail": msg})

    background_tasks.add_task(_remove_file, str(file_path))
    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=f"clipfactory_{job_id}.mp4",
        background=background_tasks,
    )
