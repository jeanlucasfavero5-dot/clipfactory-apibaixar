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
