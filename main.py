import os
import re
import subprocess
import tempfile
import shutil
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(title="ClipFactory API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


def has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def is_valid_http_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def safe_filename(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "clipfactory_video").strip("._")
    if not clean:
        clean = "clipfactory_video"
    if not clean.lower().endswith(".mp4"):
        clean += ".mp4"
    return clean


def format_hhmmss(total_sec: int) -> str:
    sec = max(0, int(total_sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "clipfactory-api",
        "yt_dlp": has_binary("yt-dlp"),
        "ffmpeg": has_binary("ffmpeg"),
    }


@app.get("/api/download")
def download_video(
    url: str = Query(..., description="URL do vídeo ou live"),
    start_sec: int = Query(0, ge=0, le=7200),
    end_sec: int = Query(1800, ge=1, le=7200),
    full_if_shorter: bool = Query(True),
):
    if not is_valid_http_url(url):
        raise HTTPException(status_code=400, detail="URL inválida.")
    if not has_binary("yt-dlp"):
        raise HTTPException(status_code=500, detail="yt-dlp não encontrado no servidor.")

    clean_url = url.strip()
    safe_start = max(0, int(start_sec))
    safe_end = max(safe_start + 5, int(end_sec))
    span_sec = min(1800, max(5, safe_end - safe_start))

    tmp_dir = tempfile.mkdtemp(prefix="cf_dl_")
    out_template = os.path.join(tmp_dir, "raw_video.%(ext)s")
    final_mp4 = os.path.join(tmp_dir, "clipfactory_download.mp4")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--live-from-start",
        "-f",
        "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "--merge-output-format",
        "mp4",
        "-o",
        out_template,
    ]

    should_trim = not (full_if_shorter and safe_start == 0 and safe_end >= 1800)
    if should_trim and has_binary("ffmpeg"):
        section_spec = f"*{format_hhmmss(safe_start)}-{format_hhmmss(safe_start + span_sec)}"
        cmd.extend(["--download-sections", section_spec, "--force-keyframes-at-cuts"])

    cookies_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies_file and os.path.exists(cookies_file):
        cmd.extend(["--cookies", cookies_file])

    cmd.append(clean_url)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=260)
        if proc.returncode != 0 and "--download-sections" in cmd:
            # Fallback sem --download-sections para lives/plataformas que não aceitam recorte remoto
            for f in os.listdir(tmp_dir):
                try:
                    os.remove(os.path.join(tmp_dir, f))
                except Exception:
                    pass
            fallback_cmd = [
                "yt-dlp",
                "--no-playlist",
                "--no-warnings",
                "-f",
                "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
                "--merge-output-format",
                "mp4",
                "-o",
                out_template,
            ]
            if cookies_file and os.path.exists(cookies_file):
                fallback_cmd.extend(["--cookies", cookies_file])
            fallback_cmd.append(clean_url)
            proc = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=260)

        if proc.returncode != 0:
            lines = (proc.stderr or proc.stdout or "Falha ao baixar o vídeo.").strip().splitlines()
            err_msg = lines[-1] if lines else "Falha ao baixar o vídeo."
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"yt-dlp: {err_msg}")

        downloaded_files = [
            os.path.join(tmp_dir, f)
            for f in os.listdir(tmp_dir)
            if os.path.isfile(os.path.join(tmp_dir, f)) and not f.startswith("clipfactory_download")
        ]
        if not downloaded_files:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=404, detail="Nenhum arquivo de vídeo foi gerado.")

        src_file = downloaded_files[0]
        target_file = src_file

        if has_binary("ffmpeg"):
            trim_cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                "0" if "--download-sections" in cmd else str(safe_start),
                "-t",
                str(span_sec),
                "-i",
                src_file,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                final_mp4,
            ]
            t_proc = subprocess.run(trim_cmd, capture_output=True, text=True, timeout=120)
            if t_proc.returncode == 0 and os.path.exists(final_mp4) and os.path.getsize(final_mp4) > 0:
                target_file = final_mp4

        parsed_host = urlparse(clean_url).hostname or "video"
        dl_name = safe_filename(f"clipfactory_{parsed_host}_{span_sec // 60}m")

        return FileResponse(
            path=target_file,
            media_type="video/mp4",
            filename=dl_name,
            headers={
                "Content-Disposition": f'attachment; filename="{dl_name}"'
            },
            background=BackgroundTask(shutil.rmtree, tmp_dir, True),
        )
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="Tempo limite excedido ao baixar o vídeo.")
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
