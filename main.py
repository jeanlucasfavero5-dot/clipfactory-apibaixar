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
                    [p for p in out_base.parent.glob(f"{out_base.name}*") if p.is_file() and p.stat().st_size > 0],
                    key=lambda p: p.stat().st_size,
                    reverse=True,
                )
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
