app.get("/api/download", async (req, res) => {
  try {
    const rawUrl = String(req.query.url || "").trim();
    if (!rawUrl) {
      return res.status(400).json({ detail: "Parâmetro 'url' é obrigatório." });
    }

    let parsed;
    try {
      parsed = new URL(rawUrl);
      if (!/^https?:$/i.test(parsed.protocol)) {
        return res.status(400).json({ detail: "URL deve usar http ou https." });
      }
    } catch (_) {
      return res.status(400).json({ detail: "URL inválida." });
    }

    const startSec = Math.max(0, Math.min(1800, Number(req.query.start_sec) || 0));
    const endSec = Math.max(startSec + 5, Math.min(1800, Number(req.query.end_sec) || 1800));
    const isLive = String(req.query.live || "").toLowerCase() === "true" || /\/live\//i.test(rawUrl);

    // Cookies do YouTube via env (YTDLP_COOKIES = conteúdo cookies.txt ou base64)
    let cookiesFile = null;
    const cookieContent = String(process.env.YTDLP_COOKIES || "").trim();
    if (cookieContent) {
      const tmp = path.join(os.tmpdir(), "cf_cookies.txt");
      let content = cookieContent;
      if (!content.startsWith("# Netscape")) {
        try { content = Buffer.from(content, "base64").toString("utf-8"); } catch (_) {}
      }
      if (content.startsWith("# Netscape")) {
        await fs.writeFile(tmp, content, "utf-8");
        cookiesFile = tmp;
      }
    }

    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "cf-dl-"));
    const outFile = path.join(tempDir, "output.mp4");

    try {
      const args = [
        "--no-playlist",
        "--geo-bypass",
        "--force-ipv4",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format", "mp4",
        "-o", outFile
      ];
      if (cookiesFile) args.push("--cookies", cookiesFile);
      if (isLive) {
        args.push("--live-from-start");
      } else {
        args.push("--download-sections", `*${formatSecToTimestamp(startSec)}-${formatSecToTimestamp(endSec)}`);
      }
      args.push("--force-keyframes-at-cuts", parsed.toString());

      await execFileAsync("yt-dlp", args, { timeout: isLive ? 900000 : 240000 });

      const stat = await fs.stat(outFile).catch(() => null);
      if (!stat || stat.size === 0) {
        return res.status(502).json({ detail: "Não foi possível gerar o arquivo de vídeo para o link informado." });
      }

      res.setHeader("Content-Type", "video/mp4");
      res.setHeader("Content-Disposition", 'attachment; filename="clipfactory_download.mp4"');
      res.setHeader("Content-Length", String(stat.size));
      return res.status(200).sendFile(outFile, async () => {
        await fs.rm(tempDir, { recursive: true, force: true }).catch(() => {});
        if (cookiesFile) await fs.rm(cookiesFile, { force: true }).catch(() => {});
      });
    } catch (dlErr) {
      await fs.rm(tempDir, { recursive: true, force: true }).catch(() => {});
      const rawMsg = dlErr?.stderr || dlErr?.message || "Falha ao baixar o link solicitado.";
      let msg = rawMsg;
      if (/sign in to confirm|not a bot/i.test(rawMsg)) {
        msg = "YouTube bloqueou o IP deste servidor (datacenter). Configure YTDLP_COOKIES no Render ou use uma API self-hosted via Cloudflare Tunnel.";
      }
      return res.status(502).json({ detail: String(msg).slice(0, 400) });
    }
  } catch (err) {
    return res.status(500).json({ detail: err?.message || "Erro interno no endpoint /api/download." });
  }
});
