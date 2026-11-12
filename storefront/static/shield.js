/* Bazaaro flash-sale shield client: interaction signals, proof-of-work, waiting room. */
(function () {
  const born = performance.now();
  const sig = { p: 0, k: 0 };
  addEventListener("pointermove", () => { sig.p++; }, { passive: true });
  addEventListener("pointerdown", () => { sig.p += 5; }, { passive: true });
  addEventListener("touchstart", () => { sig.p += 5; }, { passive: true });
  addEventListener("keydown", () => { sig.k++; });

  /** Header value the edge uses for behaviour scoring (counts only, no content). */
  const signals = () => JSON.stringify({ p: sig.p, k: sig.k, t: Math.round(performance.now() - born) });

  function zeroBits(buf) {
    const b = new Uint8Array(buf);
    let bits = 0;
    for (const byte of b) {
      if (byte === 0) { bits += 8; continue; }
      bits += Math.clz32(byte) - 24;
      break;
    }
    return bits;
  }

  /** Finds a nonce so sha256(`${challenge}:${nonce}`) has `bits` leading zero bits. */
  async function solve(challenge, bits, onProgress) {
    const enc = new TextEncoder();
    const batch = 256;
    for (let base = 0; base < 1e8; base += batch) {
      const tries = [];
      for (let n = base; n < base + batch; n++) tries.push(crypto.subtle.digest("SHA-256", enc.encode(`${challenge}:${n}`)));
      const digests = await Promise.all(tries);
      for (let i = 0; i < batch; i++) if (zeroBits(digests[i]) >= bits) return base + i;
      if (onProgress && base % 4096 === 0) onProgress(base);
    }
    throw new Error("could not solve the challenge");
  }

  // ------------------------------------------------------------------ overlay
  let overlay = null;
  function show(html) {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "wr-overlay";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="wr-card">${html}</div>`;
    return overlay;
  }
  function hide() { if (overlay) { overlay.remove(); overlay = null; } }

  async function verify(api, ch) {
    show(`<div class="wr-icon">🛡️</div><h2>Checking your browser…</h2>
      <p class="muted">A quick proof-of-work keeps scalpers' scripts out. Real browsers solve it in about a second.</p>
      <div class="wr-bar"><i style="width:30%" class="indet"></i></div><div class="tiny" id="wr-pow">difficulty ${ch.bits} bits</div>`);
    const t0 = performance.now();
    const nonce = await solve(ch.challenge, ch.bits, (n) => { const e = document.getElementById("wr-pow"); if (e) e.textContent = `difficulty ${ch.bits} bits · ${n.toLocaleString()} hashes`; });
    await api("/api/shield/solve", { method: "POST", body: JSON.stringify({ challenge: ch.challenge, nonce: String(nonce) }) });
    return performance.now() - t0;
  }

  /** Joins the queue and resolves once admitted (rejects if the shopper leaves). */
  async function waitingRoom(api) {
    let status;
    for (let i = 0; i < 4; i++) {
      try { status = await api("/api/shield/join", { method: "POST" }); break; }
      catch (err) {
        const d = err.data && err.data.detail;
        if (d && d.code === "challenge_required") { await verify(api, d); continue; }
        throw err;
      }
    }
    if (!status) throw new Error("could not join the waiting room");
    const firstPos = status.position || 1;
    return new Promise((resolve, reject) => {
      let stopped = false;
      const render = (s) => {
        const pos = s.position || 0;
        const done = Math.max(0, Math.min(100, 100 * (1 - (pos - 1) / Math.max(1, firstPos))));
        const eta = s.eta_s == null ? "–" : s.eta_s < 60 ? `${Math.ceil(s.eta_s)} s` : `${Math.ceil(s.eta_s / 60)} min`;
        show(`<div class="wr-icon">🎟️</div><h2>You're in line for the drop</h2>
          <p class="muted">Demand is higher than checkout can handle right now, so shoppers are let in at a steady pace. Your place is saved — keep this tab open.</p>
          <div class="wr-pos"><div><div class="v">${pos ? "#" + pos : "…"}</div><div class="k">your place</div></div>
            <div><div class="v">${s.ahead ?? "–"}</div><div class="k">ahead of you</div></div>
            <div><div class="v">${eta}</div><div class="k">estimated wait</div></div></div>
          <div class="wr-bar" aria-label="queue progress"><i style="width:${done}%"></i></div>
          <div class="tiny">Admitting ${s.admit_per_minute} shoppers/minute · ${s.queue_length} in line</div>
          <button class="btn ghost" id="wr-leave" style="margin-top:14px">Leave the line</button>`);
        document.getElementById("wr-leave").onclick = () => { stopped = true; hide(); reject(new Error("left the waiting room")); };
      };
      render(status);
      const tick = async () => {
        if (stopped) return;
        try {
          const s = await api("/api/shield/queue");
          if (s.state === "admitted") {
            show(`<div class="wr-icon">✅</div><h2>You're in!</h2><p class="muted">Continuing to your item…</p>`);
            setTimeout(() => { hide(); resolve(); }, 700);
            return;
          }
          if (s.state === "not_queued") await api("/api/shield/join", { method: "POST" });
          else render(s);
        } catch { /* keep polling */ }
        setTimeout(tick, 1500);
      };
      setTimeout(tick, 1500);
    });
  }

  /**
   * Runs a purchase-path action, handling the shield's replies:
   * queue -> waiting room, challenge -> proof-of-work, rate limit -> wait and retry.
   */
  async function guarded(api, fn) {
    for (let attempt = 0; attempt < 5; attempt++) {
      try { return await fn(); }
      catch (err) {
        const d = (err.data && err.data.detail) || {};
        if (d.code === "queue_required") { await waitingRoom(api); continue; }
        if (d.code === "challenge_required") { await verify(api, d); hide(); continue; }
        if (err.status === 429) { await new Promise((r) => setTimeout(r, 1500)); continue; }
        if (d.code === "blocked") {
          const e = new Error(`Blocked by bot protection (score ${d.score}: ${(d.reasons || []).join(", ")}).`);
          e.status = 403; throw e;
        }
        throw err;
      }
    }
    throw new Error("Please try again in a moment.");
  }

  window.BZShield = { signals, solve, guarded, waitingRoom, hide };
})();
