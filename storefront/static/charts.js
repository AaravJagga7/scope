/* Minimal SVG charts: line chart with crosshair tooltip, optional log-y, reference lines. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceTicks(min, max, count) {
    const span = max - min || 1;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(10));
    return ticks;
  }

  function fmt(v) {
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(v >= 10000 ? 0 : 1) + "k";
    if (Math.abs(v) >= 100) return v.toFixed(0);
    if (Math.abs(v) >= 10) return v.toFixed(0);
    return +v.toFixed(2) + "";
  }

  /**
   * opts: {series:[{name,color,points:[[x,y],...],dash,area}], xLabel, yLabel, logY,
   *        hLines:[{y,label,color}], vLines:[{x,label,color}], height, xFmt, yFmt, yMin}
   */
  function lineChart(container, opts) {
    container.innerHTML = "";
    container.classList.add("chart");
    const W = 720, H = opts.height || 280, m = { t: 14, r: 18, b: 40, l: 54 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.title || "chart" }, container);
    const tip = document.createElement("div");
    tip.className = "tip";
    container.appendChild(tip);

    const all = opts.series.flatMap((s) => s.points);
    const extraY = (opts.hLines || []).map((h) => h.y);
    if (!all.length) {
      el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", fill: css("--ink-3"), "font-size": 13 }, svg).textContent = opts.empty || "No data yet";
      return;
    }
    const xs = all.map((p) => p[0]);
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    (opts.vLines || []).forEach((v) => { x0 = Math.min(x0, v.x); x1 = Math.max(x1, v.x); });
    if (opts.xMin !== undefined) x0 = Math.min(x0, opts.xMin);
    if (opts.xMax !== undefined) x1 = Math.max(x1, opts.xMax);
    if (x0 === x1) x1 = x0 + 1;
    const ysRaw = all.map((p) => p[1]).concat(extraY).filter((v) => isFinite(v));
    let y0 = opts.yMin !== undefined ? opts.yMin : Math.min(0, ...ysRaw);
    let y1 = Math.max(...ysRaw) * 1.08 || 1;
    const logY = !!opts.logY;
    if (logY) { y0 = Math.max(1, Math.min(...ysRaw.filter((v) => v > 0)) * 0.8); y1 = Math.max(...ysRaw) * 1.4; }
    const ly = (v) => Math.log10(Math.max(v, 1e-9));
    const sx = (x) => m.l + ((x - x0) / (x1 - x0)) * (W - m.l - m.r);
    const sy = (y) => logY
      ? H - m.b - ((ly(y) - ly(y0)) / (ly(y1) - ly(y0))) * (H - m.t - m.b)
      : H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);

    const ink2 = css("--ink-2"), grid = css("--grid"), surface = css("--surface");
    const yTicks = logY
      ? [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000].filter((v) => v >= y0 && v <= y1)
      : niceTicks(y0, y1, 5);
    yTicks.forEach((t) => {
      el("line", { x1: m.l, x2: W - m.r, y1: sy(t), y2: sy(t), stroke: grid, "stroke-width": 1 }, svg);
      el("text", { x: m.l - 8, y: sy(t) + 4, "text-anchor": "end", fill: ink2, "font-size": 11 }, svg).textContent = (opts.yFmt || fmt)(t);
    });
    niceTicks(x0, x1, 7).forEach((t) => {
      el("text", { x: sx(t), y: H - m.b + 18, "text-anchor": "middle", fill: ink2, "font-size": 11 }, svg).textContent = (opts.xFmt || fmt)(t);
    });
    el("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: css("--border") }, svg);
    if (opts.xLabel) el("text", { x: (m.l + W - m.r) / 2, y: H - 6, "text-anchor": "middle", fill: ink2, "font-size": 12 }, svg).textContent = opts.xLabel;
    if (opts.yLabel) el("text", { x: 12, y: (m.t + H - m.b) / 2, transform: `rotate(-90 12 ${(m.t + H - m.b) / 2})`, "text-anchor": "middle", fill: ink2, "font-size": 12 }, svg).textContent = opts.yLabel;

    (opts.hLines || []).forEach((h) => {
      if (h.y < y0 || h.y > y1) return;
      el("line", { x1: m.l, x2: W - m.r, y1: sy(h.y), y2: sy(h.y), stroke: h.color || css("--bad"), "stroke-width": 1.5, "stroke-dasharray": "5 4" }, svg);
      el("text", { x: W - m.r, y: sy(h.y) - 6, "text-anchor": "end", fill: ink2, "font-size": 11 }, svg).textContent = h.label;
    });
    (opts.vLines || []).forEach((v) => {
      el("line", { x1: sx(v.x), x2: sx(v.x), y1: m.t, y2: H - m.b, stroke: v.color || ink2, "stroke-width": 1.5, "stroke-dasharray": "3 3" }, svg);
      el("text", { x: sx(v.x) + 5, y: m.t + 10, fill: ink2, "font-size": 11 }, svg).textContent = v.label;
    });

    opts.series.forEach((s) => {
      if (!s.points.length) return;
      const d = s.points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(Math.max(logY ? y0 : -Infinity, p[1])).toFixed(1)}`).join("");
      el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round", "stroke-dasharray": s.dash ? "6 4" : "" }, svg);
      if (s.markers) s.points.forEach((p) => el("circle", { cx: sx(p[0]), cy: sy(p[1]), r: 4.5, fill: s.color, stroke: surface, "stroke-width": 2 }, svg));
    });

    // crosshair + tooltip
    const cross = el("line", { y1: m.t, y2: H - m.b, stroke: ink2, "stroke-width": 1, opacity: 0 }, svg);
    const dots = opts.series.map((s) => el("circle", { r: 5, fill: s.color, stroke: surface, "stroke-width": 2, opacity: 0 }, svg));
    const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (ev) => {
      const rect = svg.getBoundingClientRect();
      const px = ((ev.clientX - rect.left) / rect.width) * W;
      const xv = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
      let rows = [];
      opts.series.forEach((s, i) => {
        if (!s.points.length) return;
        const p = s.points.reduce((a, b) => (Math.abs(b[0] - xv) < Math.abs(a[0] - xv) ? b : a));
        dots[i].setAttribute("cx", sx(p[0]));
        dots[i].setAttribute("cy", sy(Math.max(logY ? y0 : -Infinity, p[1])));
        dots[i].setAttribute("opacity", 1);
        rows.push({ s, p });
      });
      if (!rows.length) return;
      const xr = rows[0].p[0];
      cross.setAttribute("x1", sx(xr)); cross.setAttribute("x2", sx(xr)); cross.setAttribute("opacity", 0.4);
      tip.innerHTML = `<b>${opts.xLabel || "x"}: ${(opts.xFmt || fmt)(xr)}</b><br>` +
        rows.map((r) => `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${r.s.color};margin-right:6px"></span>${r.s.name}: ${(opts.yFmt || fmt)(r.p[1])}${opts.yUnit || ""}`).join("<br>");
      tip.style.display = "block";
      const left = (sx(xr) / W) * rect.width;
      tip.style.left = Math.min(rect.width - tip.offsetWidth - 4, left + 12) + "px";
      tip.style.top = "8px";
    });
    hit.addEventListener("mouseleave", () => {
      tip.style.display = "none"; cross.setAttribute("opacity", 0); dots.forEach((d) => d.setAttribute("opacity", 0));
    });
  }

  function legend(container, items) {
    container.className = "legend";
    container.innerHTML = items.map((it) => `<span><i class="${it.dash ? "dash" : ""}" style="background:${it.color}"></i>${it.name}</span>`).join("");
  }

  window.BZCharts = { lineChart, legend, css };
})();
