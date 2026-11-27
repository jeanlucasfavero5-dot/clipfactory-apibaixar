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

app = FastAPI(title="ClipFactory API", version="0.032")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "clipfactory_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Cookies do YouTube (contorna "Sign in to confirm you're not a bot")
COOKIES_TEMP_FILE = Path(tempfile.gettempdir()) / "clipfactory_cookies.txt"


def _ensure_cookies_file() -> Optional[Path]:
    """Lê os cookies de YTDLP_COOKIES (conteúdo, plain ou base64) ou YTDLP_COOKIES_FILE."""
    content = os.getenv("YTDLP_COOKIES", "").strip()
    path_env = os.getenv("YTDLP_COOKIES_FILE", "").strip()

    if path_env and Path(path_env).is_file() and Path(path_env).stat().st_size > 0:
        return Path(path_env)

    if not content:
        return None

    # Conteúdo enviado em base64 (recomendado no Render, evita problema de quebra de linha)
    if not content.startswith("# Netscape"):
        try:
            content = base64.b64decode(content).decode("utf-8", "replace").strip()
        except Exception:
            pass

    if not content.startswith("# Netscape"):
        return None

    try:
        COOKIES_TEMP_FILE.write_text(content, encoding="utf-8")
        return COOKIES_TEMP_FILE
    except Exception:
        return None


def _friendly_error(stderr: str) -> str:
    s = (stderr or "").lower()
    if "sign in to confirm" in s or "not a bot" in s:
        return (
            "O YouTube bloqueou o IP deste servidor (datacenter). "
            "Configure a variável YTDLP_COOKIES no Render com cookies de uma conta do YouTube "
            "(formato cookies.txt, base64) ou use uma API self-hosted em IP residencial "
            "(Cloudflare Tunnel) configurada no PWA."
        )
    if "po token" in s or "gvs po" in s:
        return "O YouTube exigiu PO Token para este formato. Atualize a imagem (yt-dlp -U) ou use cookies de conta logada."
    if "is live" in s and "live-from-start" not in s:
        return "A live ainda não tem VOD disponível. Para lives em andamento use a opção de live (live=true)."
    return ""


def _is_live_url(url: str) -> bool:
    u = url.lower()
    return "/live/" in u or "youtube.com/live" in u or "&live=" in u or "/videos/live" in u


class DownloadRequest(BaseModel):
    url: Optional[str] = None
    video_url: Optional[str] = None
    link: Optional[str] = None
    start_sec: Optional[float] = 0
    end_sec: Optional[float] = 1800
    start: Optional[float] = None
    end: Optional[float] = None
    full_if_shorter: Optional[bool] = True
    live: Optional[bool] = None


def _clean_youtube_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        raise ValueError("URL vazia.")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url

def _run_yt_dlp(
    url: str,
    out_base: Path,
    start_sec: Optional[float],
    end_sec: Optional[float],
    is_live: bool,
) -> Path:
    # 1ª tentativa usa os clientes padrão do yt-dlp (os que não exigem PO token).
    # As demais são fallback quando o YouTube recusa (SABR/403).
    strategies = [
        [],
        ["--extractor-args", "youtube:player_client=mweb"],
        ["--extractor-args", "youtube:player_client=web_safari"],
        ["--extractor-args", "youtube:player_client=tv_embedded"],
        ["--extractor-args", "youtube:player_client=ios"],
        ["--extractor-args", "youtube:player_client=android_vr"],
        ["--extractor-args", "youtube:player_client=web_embedded"],
    ]
    proxy_url = os.getenv("YTDLP_PROXY", "").strip()
    pot_base = os.getenv("YTDLP_POT_BASE_URL", "").strip()
    cookies_file = _ensure_cookies_file()  # segue opcional; público não usa
    timeout_cfg = int(os.getenv("YTDLP_TIMEOUT", "300") or 300)
    if is_live:
        timeout_cfg = max(timeout_cfg, 900)

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

        if cookies_file:
            cmd.extend(["--cookies", str(cookies_file)])

        if pot_base:
            # Aponta para um provedor PO Token externo (ex.: container bgutil HTTP)
            cmd.extend(["--extractor-args", f"youtubepot-bgutilhttp:base_url={pot_base}"])

        if is_live:
            cmd.append("--live-from-start")

        if start_sec is not None and end_sec is not None and float(end_sec) > float(start_sec) and not is_live:
            s = max(0.0, float(start_sec))
            e = max(s + 1.0, float(end_sec))
            cmd.extend(["--download-sections", f"*{s}-{e}"])

        cmd.extend(extra_args)

        if proxy_url:
            cmd.extend(["--proxy", proxy_url])

        cmd.append(url)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_cfg)
            if proc.returncode == 0:
                matches = sorted(
def _friendly_error(stderr: str) -> str:
    s = (stderr or "").lower()
    if "sign in to confirm" in s or "not a bot" in s:
        return (
            "O YouTube bloqueou o IP deste servidor. Isso é bloqueio de IP de datacenter, "
            "não do app. Soluções: (1) use o PWA apontando para a API self-hosted "
            "(?api=https://SEU-TUNNEL.trycloudflare.com) ou (2) configure YTDLP_PROXY no "
            "Render com um proxy residencial."
        )
    if "po token" in s or "gvs" in s or "sabr" in s:
        return "O YouTube exigiu token de origem para este vídeo. Tente novamente — o plugin PO Token foi acionado; se persistir, atualize a imagem (yt-dlp -U)."
    if "live event" in s or "is live" in s:
        return "A live ainda não tem VOD. Para lives em andamento, cole o link da live (o app usa gravação desde o início)."
    return ""

def _run_yt_dlp(
    url: str,
    out_base: Path,
    start_sec: Optional[float],
    end_sec: Optional[float],
    is_live: bool,
) -> Path:
    # 1ª tentativa usa os clientes padrão do yt-dlp (os que não exigem PO token).
    # As demais são fallback quando o YouTube recusa (SABR/403).
    strategies = [
        [],  # clientes padrão do yt-dlp (sem PO token na maioria dos casos)
        [],
        ["--extractor-args", "youtube:player_client=mweb"],
        ["--extractor-args", "youtube:player_client=web_safari"],
        ["--extractor-args", "youtube:player_client=tv_embedded"],
@@ -116,7 +17,8 @@ def _run_yt_dlp(
        ["--extractor-args", "youtube:player_client=web_embedded"],
    ]
    proxy_url = os.getenv("YTDLP_PROXY", "").strip()
    cookies_file = _ensure_cookies_file()
    pot_base = os.getenv("YTDLP_POT_BASE_URL", "").strip()
    cookies_file = _ensure_cookies_file()  # segue opcional; público não usa
    timeout_cfg = int(os.getenv("YTDLP_TIMEOUT", "300") or 300)
    if is_live:
        timeout_cfg = max(timeout_cfg, 900)
@@ -146,8 +48,11 @@ def _run_yt_dlp(
        if cookies_file:
            cmd.extend(["--cookies", str(cookies_file)])

        if pot_base:
            # Aponta para um provedor PO Token externo (ex.: container bgutil HTTP)
            cmd.extend(["--extractor-args", f"youtubepot-bgutilhttp:base_url={pot_base}"])

        if is_live:
            # Grava a live desde o início (yt-dlp Experimental, funciona no YouTube)
            cmd.append("--live-from-start")

        if start_sec is not None and end_sec is not None and float(end_sec) > float(start_sec) and not is_live:
@@ -173,105 +78,11 @@ def _run_yt_dlp(
                if matches:
                    return matches[0]
            last_err = (proc.stderr or proc.stdout or "").strip()
            # Se ainda funciona sem erro mas sem arquivo, tenta próxima estratégia
        except subprocess.TimeoutExpired:
            last_err = "Tempo limite excedido ao processar o vídeo."
        except Exception as exc:
            last_err = str(exc)

    friendly = _friendly_error(last_err)
    raise RuntimeError(friendly or (last_err or "Falha ao extrair mídia com yt-dlp."))


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
    return {"status": "ok", "version": "0.032", "yt_dlp": yt_ver}


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
):
    try:
        if request.method == "GET":
            params = dict(request.query_params)
            raw_url = str(params.get("url") or params.get("video_url") or params.get("link") or "").strip()
            start_sec = float(params.get("start_sec") or params.get("start") or 0)
            end_sec = float(params.get("end_sec") or params.get("end") or 1800)
            full_if_shorter = str(params.get("full_if_shorter", "true")).lower() != "false"
            live_param = str(params.get("live", "")).strip().lower()
        else:
            body = req.dict() if req else {}
            raw_url = str(body.get("url") or body.get("video_url") or body.get("link") or "").strip()
            start_sec = float(body.get("start_sec") if body.get("start_sec") is not None else (body.get("start") or 0))
            end_sec = float(body.get("end_sec") if body.get("end_sec") is not None else (body.get("end") or 1800))
            full_if_shorter = bool(body.get("full_if_shorter", True))
            live_param = str(body.get("live") or "").strip().lower()

        if not raw_url:
            raise HTTPException(status_code=400, detail="Parâmetro 'url' é obrigatório.")

        try:
            clean_url = _clean_youtube_url(raw_url)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        parsed = urlparse(clean_url)
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(status_code=400, detail="URL inválida.")

        is_live = live_param in ("1", "true", "yes") or (live_param == "" and _is_live_url(clean_url))

        if is_live:
            # Live: baixa desde o início da transmissão até um teto de minutos
            end_sec = min(max(end_sec, start_sec + 30), 10800)
        else:
            start_sec = max(0.0, start_sec)
            end_sec = max(start_sec + 5.0, min(end_sec, 10800))

        job_id = uuid.uuid4().hex[:12]
        out_base = DOWNLOAD_DIR / f"cf_{job_id}"
        background_tasks.add_task(_remove_file, str(out_base))

        try:
            out_file = _run_yt_dlp(clean_url, out_base, start_sec, end_sec, is_live)
        except RuntimeError as re_err:
            raise HTTPException(status_code=502, detail=str(re_err)[:600])

        media_type = "video/mp4" if out_file.suffix.lower() == ".mp4" else "video/webm"
        download_name = f"clipfactory_{job_id}{out_file.suffix.lower()}"

        return FileResponse(
            path=out_file,
            media_type=media_type,
            filename=download_name,
            background=background_tasks,
        )
    except HTTPException:
        raise
    except Exception as err:
        return JSONResponse(status_code=500, content={"detail": str(err) or "Erro interno no download."})
