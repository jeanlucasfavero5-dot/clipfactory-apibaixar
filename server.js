      const potBase = String(process.env.YTDLP_POT_BASE_URL || "").trim();
      if (potBase) {
        args.push("--extractor-args", `youtubepot-bgutilhttp:base_url=${potBase}`);
      }
