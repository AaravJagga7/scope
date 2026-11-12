/* Bazaaro storefront + SCOPE-X Constraint Lab (vanilla JS SPA). */
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const view = $("#view");
  const C = window.BZCharts;
  const state = { categories: [], health: null, timers: [], lab: { tab: "live", test: null, poll: null, whatif: null, whatifTimer: null, shieldPoll: null, drop: null } };
  const S = window.BZShield;
  /** Interval that is cleared automatically when the shopper navigates away. */
  const every = (fn, ms) => { const id = setInterval(fn, ms); state.timers.push(id); return id; };
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (v) => "$" + Number(v).toFixed(2);
  const stars = (r) => "★".repeat(Math.round(r)) + "☆".repeat(5 - Math.round(r));
  const tile = (p, cls = "") =>
    `<div class="thumb ${cls}" style="background:linear-gradient(135deg,hsl(${p.hue} 70% 88%),hsl(${(p.hue + 40) % 360} 60% 76%))">${p.discount_pct ? `<span class="badge">-${p.discount_pct}%</span>` : ""}<span>${p.glyph}</span></div>`;

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "content-type": "application/json", "x-bz-signals": S.signals() }, credentials: "same-origin", ...opts });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
    if (!res.ok) {
      const d = data.detail;
      const msg = typeof d === "string" ? d : d && d.message ? d.message : JSON.stringify(d);
      const err = new Error(msg || res.statusText);
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  }

  // ------------------------------------------------------------------ shell
  async function initShell() {
    state.categories = await api("/api/categories");
    $("#search-cat").innerHTML = `<option value="">All</option>` + state.categories.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    $("#catbar").innerHTML = `<a href="#/s">All products</a><a href="#/s?sort=discount">Today's deals</a>` +
      state.categories.map((c) => `<a href="#/c/${c.id}" data-cat="${c.id}">${esc(c.name)}</a>`).join("");
    $("#search-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = $("#search-q").value.trim(), cat = $("#search-cat").value;
      location.hash = `#/s?q=${encodeURIComponent(q)}${cat ? "&category=" + cat : ""}`;
    });
    $("#health").addEventListener("click", () => { location.hash = "#/lab"; });
    refreshCartCount();
    pollHealth();
    setInterval(pollHealth, 3000);
  }

  async function refreshCartCount() {
    try {
      const cart = await api("/api/cart");
      $("#cart-count").textContent = cart.items.reduce((n, i) => n + i.quantity, 0);
    } catch { $("#cart-count").textContent = "–"; }
  }

  async function pollHealth() {
    try {
      const h = await api("/api/health");
      state.health = h;
      const dot = $("#health-dot");
      dot.className = "dot " + h.level;
      const p95 = Math.max(h.store_p95_ms || 0, h.load_test_p95_ms || 0);
      $("#health-text").textContent = h.testbed !== "running" ? `testbed ${h.testbed}` : `${h.level} · p95 ${p95 ? Math.round(p95) + " ms" : "–"}`;
      const banner = $("#banner");
      if (h.testbed === "running" && h.shield && h.shield.room_open) {
        banner.className = "banner show drop";
        $("#banner-text").innerHTML = `🎟️ <b>Flash sale in progress.</b> The waiting room is open${h.shield.queue_length ? ` · ${h.shield.queue_length} shoppers in line` : ""}. Checkout stays fast because shoppers are let in at a steady pace.`;
      } else if (h.testbed === "running" && h.level !== "healthy") {
        banner.className = "banner show " + h.level;
        $("#banner-text").innerHTML = h.level === "degraded"
          ? `⚠️ <b>Checkout is under pressure.</b> Live p95 ${Math.round(p95)} ms vs SLO ${h.slo.p95_ms} ms${h.error_pct > h.slo.error_pct ? `, errors ${h.error_pct}%` : ""}. Orders may take longer — this is a constraint being hit in real time.`
          : `High demand right now (p95 ${Math.round(p95)} ms). Checkout is still within SLO.`;
      } else if (h.testbed !== "running") {
        banner.className = "banner show busy";
        $("#banner-text").textContent = h.testbed === "starting" ? "Starting the store's services…" : `Store services are ${h.testbed}. Open the Constraint Lab to apply a config.`;
      } else {
        banner.className = "banner";
      }
    } catch { /* ignore */ }
  }

  function setActive(hash) {
    document.querySelectorAll(".catbar a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === hash));
    $("#nav-lab").classList.toggle("active", hash.startsWith("#/lab"));
    $("#nav-cart").classList.toggle("active", hash.startsWith("#/cart"));
  }

  // ------------------------------------------------------------------ store views
  function productCard(p) {
    return `<div class="card">
      <a href="#/p/${p.id}">${tile(p)}</a>
      <div class="card-body">
        <a href="#/p/${p.id}" class="card-title">${esc(p.name)}</a>
        <div class="stars" aria-label="${p.rating} out of 5">${stars(p.rating)} <span class="tiny">${p.reviews.toLocaleString()}</span></div>
        <div class="price">${money(p.price)}${p.list_price > p.price ? `<small>${money(p.list_price)}</small>` : ""}</div>
        ${p.fast_delivery ? `<div class="fast">⚡ FastLane delivery</div>` : `<div class="tiny">Standard delivery</div>`}
        <button class="btn" data-add="${p.id}">Add to cart</button>
      </div></div>`;
  }

  const addItem = (id, quantity) =>
    S.guarded(api, () => api("/api/cart/items", { method: "POST", body: JSON.stringify({ product_id: id, quantity }) }));

  function bindAdd(root) {
    root.querySelectorAll("[data-add]").forEach((b) =>
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { await addItem(b.dataset.add, 1); b.textContent = "Added ✓"; refreshCartCount(); }
        catch (e) { b.textContent = "Unavailable"; if (e.message !== "left the waiting room") alert(e.message); }
        setTimeout(() => { b.disabled = false; b.textContent = "Add to cart"; }, 1200);
      }));
  }

  async function home() {
    const [deals, top] = await Promise.all([api("/api/products?sort=discount"), api("/api/products?sort=rating")]);
    const glyphs = { electronics: "🎧", home: "🍲", books: "📘", fashion: "👟", sports: "⛺", toys: "🧩" };
    view.innerHTML = `
      <section class="hero">
        <div>
          <h1>Everything you need, delivered fast.</h1>
          <p>Shop thousands of everyday essentials from independent sellers. Every order on Bazaaro runs through the live SCOPE-X testbed, so you can watch its operational constraints change in the Constraint Lab.</p>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px">
            <a class="btn light" href="#/s?sort=discount">Shop today's deals</a>
            <a class="btn ghost" style="color:#fff;border-color:rgba(255,255,255,.5)" href="#/lab">Open the Constraint Lab</a>
          </div>
        </div>
        <div class="glyphs">${deals.slice(0, 6).map((p) => `<span>${p.glyph}</span>`).join("")}</div>
      </section>
      <a class="drop-strip" href="#/p/bz-701"><span class="g">💿</span><div><b>Drop #1 is live: Lumen One Limited Turntable</b>
        <div class="small">50 numbered units at $149 (list $299), limit 2 per customer. Protected by the flash-sale shield: waiting room, 5-minute cart holds and bot checks.</div></div><span class="btn light">See the drop</span></a>
      <h2>Shop by category</h2>
      <div class="cats">${state.categories.map((c) => `<a class="cat-tile" href="#/c/${c.id}"><span class="g">${glyphs[c.id] || "🛍️"}</span>${esc(c.name)}</a>`).join("")}</div>
      <h2>Today's deals</h2>
      <div class="grid">${deals.slice(0, 8).map(productCard).join("")}</div>
      <h2>Top rated</h2>
      <div class="grid">${top.slice(0, 4).map(productCard).join("")}</div>`;
    bindAdd(view);
  }

  async function listing(params, category) {
    const q = params.get("q") || "", sort = params.get("sort") || "featured";
    const cat = category || params.get("category") || "";
    const maxPrice = params.get("max_price") || "", fast = params.get("fast") === "1";
    const qs = new URLSearchParams({ q, sort, category: cat, fast: fast ? "true" : "false" });
    if (maxPrice) qs.set("max_price", maxPrice);
    const items = await api("/api/products?" + qs);
    const catName = (state.categories.find((c) => c.id === cat) || {}).name;
    const go = (patch) => {
      const p = new URLSearchParams({ q, sort, ...(cat && !category ? { category: cat } : {}), ...(maxPrice ? { max_price: maxPrice } : {}), ...(fast ? { fast: "1" } : {}), ...patch });
      [...p.keys()].forEach((k) => { if (p.get(k) === "") p.delete(k); });
      location.hash = (category ? `#/c/${category}` : "#/s") + "?" + p;
    };
    view.innerHTML = `
      <div class="listing">
        <aside class="filters">
          <h3>Price</h3>
          ${["", "50", "100", "200"].map((v) => `<label><input type="radio" name="mp" value="${v}" ${maxPrice === v ? "checked" : ""}/> ${v ? "Up to $" + v : "Any price"}</label>`).join("")}
          <h3 style="margin-top:14px">Delivery</h3>
          <label><input type="checkbox" id="f-fast" ${fast ? "checked" : ""}/> ⚡ FastLane only</label>
        </aside>
        <section>
          <div class="toolbar">
            <div><h1 style="font-size:22px">${q ? `Results for “${esc(q)}”` : esc(catName || "All products")}</h1><span class="muted small">${items.length} item${items.length === 1 ? "" : "s"}</span></div>
            <label class="small">Sort by
              <select id="f-sort" style="padding:6px 10px;border-radius:8px;border:1px solid var(--border);background:var(--surface)">
                ${[["featured", "Featured"], ["price_asc", "Price: low to high"], ["price_desc", "Price: high to low"], ["rating", "Avg. rating"], ["discount", "Biggest discount"]].map(([v, l]) => `<option value="${v}" ${sort === v ? "selected" : ""}>${l}</option>`).join("")}
              </select></label>
          </div>
          ${items.length ? `<div class="grid">${items.map(productCard).join("")}</div>` : `<div class="panel">No products match. Try a broader search.</div>`}
        </section>
      </div>`;
    view.querySelectorAll('input[name="mp"]').forEach((r) => r.addEventListener("change", () => go({ max_price: r.value })));
    $("#f-fast").addEventListener("change", (e) => go({ fast: e.target.checked ? "1" : "" }));
    $("#f-sort").addEventListener("change", (e) => go({ sort: e.target.value }));
    bindAdd(view);
  }

  async function productPage(id) {
    const p = await api(`/api/products/${id}`);
    const related = (await api(`/api/products?category=${p.category}`)).filter((x) => x.id !== p.id).slice(0, 4);
    const stock = p.live_stock;
    const degraded = state.health && state.health.level === "degraded";
    const eta = new Date(Date.now() + (p.fast_delivery ? 1 : 4) * 864e5).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
    view.innerHTML = `
      <div class="pdp">
        ${tile(p, "big")}
        <div>
          <div class="tiny">Sold by ${esc(p.seller)}</div>
          <h1>${esc(p.name)}</h1>
          <div class="stars">${stars(p.rating)} <span class="muted small">${p.rating} · ${p.reviews.toLocaleString()} ratings</span></div>
          <hr style="border:0;border-top:1px solid var(--border);margin:14px 0">
          <div class="price" style="font-size:28px">${money(p.price)}${p.list_price > p.price ? `<small>List ${money(p.list_price)}</small>` : ""}</div>
          ${p.discount_pct ? `<div style="color:var(--deal);font-weight:600">You save ${money(p.list_price - p.price)} (${p.discount_pct}%)</div>` : ""}
          <p>${esc(p.blurb)}</p>
          ${p.limited ? `<div class="drop-panel"><div class="label-chip drop">limited drop</div>
            <div class="drop-meter" id="drop-meter"></div>
            <div class="tiny">Adding to cart reserves your unit${p.max_per_customer > 1 ? "s" : ""} for ${state.health && state.health.shield ? mmss(state.health.shield.hold_ttl_s) : "a few minutes"} when reservations are on. Unpaid holds expire and go back on sale. Limit ${p.max_per_customer} per customer.</div></div>`
          : `<div class="notice small">📦 Stock is read live from the inventory service: ${stock === null ? "unavailable right now" : `<b>${stock}</b> available`}.</div>`}
        </div>
        <aside class="buybox">
          <div class="price">${money(p.price)}</div>
          <div class="row"><span>Delivery</span><b>${eta}</b></div>
          <div class="row"><span>Ships with</span><span>${p.fast_delivery ? "⚡ FastLane" : "Standard"}</span></div>
          <div style="margin:8px 0;font-weight:600;color:${stock > 10 ? "var(--good)" : stock > 0 ? "var(--deal)" : "var(--bad)"}">${stock === null ? "Stock unknown" : stock > 10 ? "In stock" : stock > 0 ? `Only ${stock} left` : "Out of stock"}</div>
          ${degraded ? `<div class="notice bad small">Checkout is slower than usual right now (live p95 above SLO).</div>` : ""}
          <div class="qty">Qty <select id="qty">${[1, 2, 3, 4, 5].slice(0, p.max_per_customer || 5).map((n) => `<option>${n}</option>`).join("")}</select></div>
          <button class="btn" style="width:100%" id="add" ${stock === 0 ? "disabled" : ""}>Add to cart</button>
          <button class="btn secondary" style="width:100%;margin-top:8px" id="buy" ${stock === 0 ? "disabled" : ""}>Buy now</button>
          <div id="buy-msg"></div>
        </aside>
      </div>
      ${related.length ? `<h2>More in this category</h2><div class="grid">${related.map(productCard).join("")}</div>` : ""}`;
    const add = async () => { await addItem(p.id, +$("#qty").value); refreshCartCount(); };
    const fail = (err) => { if (err.message !== "left the waiting room") $("#buy-msg").innerHTML = `<div class="notice bad small">${esc(err.message)}</div>`; };
    $("#add").addEventListener("click", async (e) => { try { await add(); e.target.textContent = "Added ✓"; } catch (err) { fail(err); } });
    $("#buy").addEventListener("click", async () => { try { await add(); location.hash = "#/checkout"; } catch (err) { fail(err); } });
    if (p.limited) {
      const meter = async () => {
        const el = $("#drop-meter");
        if (!el) return;
        let q;
        try { q = await api(`/api/products/${p.id}`); } catch { return; }
        const total = Math.max(1, (q.live_stock || 0) + (q.reserved || 0));
        el.innerHTML = `<div class="dm-bar"><i class="avail" style="width:${100 * (q.live_stock || 0) / Math.max(total, 50)}%"></i><i class="held" style="width:${100 * (q.reserved || 0) / Math.max(total, 50)}%"></i></div>
          <div class="dm-legend"><span><i class="avail"></i><b>${q.live_stock ?? "–"}</b> available</span><span><i class="held"></i><b>${q.reserved ?? "–"}</b> reserved in carts or checking out</span></div>`;
      };
      meter(); every(meter, 2000);
    }
    bindAdd(view);
  }

  async function cartPage() {
    const cart = await api("/api/cart");
    const byId = Object.fromEntries((await api("/api/products")).map((p) => [p.id, p]));
    if (!cart.items.length) {
      view.innerHTML = `<div class="panel"><h1>Your cart is empty</h1><p class="muted">Find something you like in today's deals.</p><a class="btn" href="#/s?sort=discount">Shop deals</a></div>`;
      return;
    }
    view.innerHTML = `<div class="two">
      <div class="panel"><h1>Shopping cart</h1>
        ${cart.items.map((i) => `<div class="cart-line">${byId[i.product_id] ? tile(byId[i.product_id]) : "<div></div>"}<div><a href="#/p/${i.product_id}"><b>${esc(i.product_name)}</b></a><div class="tiny">Qty ${i.quantity} × ${money(i.unit_price)}</div>${cart.reservations ? `<div class="hold" data-hold="${i.product_id}"></div>` : ""}</div><b>${money(i.quantity * i.unit_price)}</b></div>`).join("")}
        <div id="hold-msg"></div>
        <button class="btn ghost" id="clear" style="margin-top:12px">Empty cart</button>
      </div>
      <aside class="panel"><div class="row" style="display:flex;justify-content:space-between"><span>Subtotal</span><b>${money(cart.total_amount)}</b></div>
        <a class="btn" href="#/checkout" style="width:100%;margin-top:12px">Proceed to checkout</a></aside></div>`;
    $("#clear").addEventListener("click", async () => { await api("/api/cart", { method: "DELETE" }); refreshCartCount(); cartPage(); });
    if (cart.reservations) holdCountdown(cart);
  }

  /** Live countdown per reserved line; offers to re-reserve once a hold expires. */
  function holdCountdown(cart) {
    const deadline = {};
    const loaded = performance.now();
    for (const [pid, h] of Object.entries(cart.holds || {})) deadline[pid] = h.seconds_left;
    const draw = () => {
      const elapsed = (performance.now() - loaded) / 1000;
      document.querySelectorAll("[data-hold]").forEach((el) => {
        const pid = el.dataset.hold, h = (cart.holds || {})[pid];
        const left = h ? deadline[pid] - elapsed : 0;
        if (h && left > 0 && h.status !== "expired") {
          el.className = "hold ok" + (left < 60 ? " soon" : "");
          el.innerHTML = `⏳ Reserved for you · <b>${mmss(left)}</b>`;
        } else {
          el.className = "hold gone";
          el.innerHTML = `Reservation ${h ? "expired, stock returned to the sale" : "missing"} · <button class="linkbtn" data-rehold>Reserve again</button>`;
        }
      });
      document.querySelectorAll("[data-rehold]").forEach((b) => (b.onclick = async () => {
        b.disabled = true;
        try {
          const r = await S.guarded(api, () => api("/api/cart/rehold", { method: "POST" }));
          if (r.failed.length) $("#hold-msg").innerHTML = `<div class="notice bad small">Could not re-reserve: ${esc(r.failed.map((f) => f.reason).join("; "))}</div>`;
          route();
        } catch (err) { $("#hold-msg").innerHTML = `<div class="notice bad small">${esc(err.message)}</div>`; }
      }));
    };
    draw(); every(draw, 1000);
  }

  async function checkoutPage() {
    const cart = await api("/api/cart");
    if (!cart.items.length) { location.hash = "#/cart"; return; }
    view.innerHTML = `<div class="two">
      <div class="panel"><h1>Checkout</h1>
        <div class="field"><label for="c-name">Full name</label><input id="c-name" value="Guest Shopper"/></div>
        <div class="field"><label for="c-addr">Delivery address</label><input id="c-addr" value="221 Market Street"/></div>
        <div class="field"><label for="c-pay">Payment method</label><select id="c-pay"><option value="credit_card">Card (simulated)</option><option value="wallet">Wallet (simulated)</option></select></div>
        <div class="tiny">The order runs cart validation → inventory reservation → payment → order creation on the live services. No real payment is taken.</div>
        ${cart.reservations ? `<div class="notice small" id="co-hold">Your items are reserved while you check out.</div>` : ""}
        <div id="c-msg"></div>
      </div>
      <aside class="panel"><h3>Order summary</h3>
        ${cart.items.map((i) => `<div style="display:flex;justify-content:space-between" class="small"><span>${i.quantity} × ${esc(i.product_name)}</span><span>${money(i.quantity * i.unit_price)}</span></div>`).join("")}
        <hr style="border:0;border-top:1px solid var(--border)"><div style="display:flex;justify-content:space-between"><b>Total</b><b>${money(cart.total_amount)}</b></div>
        <button class="btn" id="place" style="width:100%;margin-top:14px">Place order</button></aside></div>`;
    $("#place").addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Placing order…";
      try {
        const o = await S.guarded(api, () => api("/api/checkout", { method: "POST", body: JSON.stringify({ name: $("#c-name").value, payment_method: $("#c-pay").value }) }));
        sessionStorage.setItem("bz_last_order", JSON.stringify(o));
        refreshCartCount();
        location.hash = `#/order/${o.order_id}`;
      } catch (err) {
        const expired = /expired or missing/.test(err.message);
        $("#c-msg").innerHTML = `<div class="notice bad">Order failed (${err.status || "network"}): ${esc(err.message)}${expired ? ` <a href="#/cart"><b>Review your cart</b></a> to reserve again.` : ""}</div>`;
        e.target.disabled = false; e.target.textContent = "Try again";
      }
    });
    if (cart.reservations) {
      const lefts = Object.values(cart.holds || {}).filter((h) => h.seconds_left > 0).map((h) => h.seconds_left);
      const loaded = performance.now();
      const draw = () => {
        const el = $("#co-hold");
        if (!el) return;
        const left = lefts.length ? Math.min(...lefts) - (performance.now() - loaded) / 1000 : 0;
        el.className = "notice small " + (left > 0 ? (left < 60 ? "bad" : "good") : "bad");
        el.innerHTML = left > 0 ? `⏳ Your items are reserved for <b>${mmss(left)}</b>. Pay before the timer ends or they go back on sale.`
          : `Your reservation has expired. <a href="#/cart"><b>Go to cart</b></a> to reserve again.`;
      };
      draw(); every(draw, 1000);
    }
  }

  function orderPage(id) {
    let o = null;
    try { o = JSON.parse(sessionStorage.getItem("bz_last_order") || "null"); } catch { o = null; }
    view.innerHTML = `<div class="panel" style="max-width:640px;margin:0 auto">
      <h1>✅ Order placed</h1><p class="muted">Order <b>${esc(id)}</b>${o ? ` · total ${money(o.total_amount)}` : ""}</p>
      ${o ? `<div class="notice good small">Checkout took <b>${o.latency_ms} ms</b> end to end across 4 services (transaction ${esc(o.transaction_id)}, trace ${esc(o.trace_id.slice(0, 12))}…).</div>` : ""}
      <a class="btn" href="#/">Continue shopping</a> <a class="btn ghost" href="#/lab">See how load changes this</a></div>`;
  }

  // ------------------------------------------------------------------ lab
  const clsPill = (c) => {
    const key = (c || "untested").replace(/\s*\(.*\)/, "").replace(/\s+/g, "-").replace("violated-by-autoscaler", "violated");
    return `<span class="pill ${key}">${esc(c || "untested")}</span>`;
  };

  async function labPage() {
    view.innerHTML = `
      <div class="lab-head">
        <div><h1>Constraint Lab</h1>
          <div class="muted">Test the store's operational constraints and watch them move as the infrastructure changes.</div></div>
        <div class="tabs"><button data-tab="live">Live tests</button><button data-tab="model">Model what-if</button><button data-tab="shield">Flash-sale shield</button><button data-tab="repo">Constraint repository</button></div>
      </div>
      <div id="lab-body"></div>`;
    view.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { state.lab.tab = b.dataset.tab; renderLabTab(); }));
    renderLabTab();
  }

  function renderLabTab() {
    view.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.lab.tab));
    clearInterval(state.lab.shieldPoll);
    if (state.lab.tab === "live") return liveTab();
    if (state.lab.tab === "model") return modelTab();
    if (state.lab.tab === "shield") return shieldTab();
    return repoTab();
  }

  // ---- live
  async function liveTab() {
    const body = $("#lab-body");
    const tb = await api("/api/testbed");
    const cfg = tb.config;
    body.innerHTML = `
      <div class="lab-grid">
        <div class="stack">
          <div class="panel controls">
            <h3>Testbed config <span class="label-chip live">validated on local testbed</span></h3>
            <div class="tiny" style="margin-bottom:10px">Apply = reset to a clean baseline and relaunch all six services with these settings. ${esc(tb.storage)}.</div>
            ${slider("checkout_workers", "Checkout workers (replicas)", 1, 8, 1, cfg.checkout_workers)}
            ${slider("inventory_workers", "Inventory workers", 1, 4, 1, cfg.inventory_workers)}
            ${slider("payment_latency_ms", "Payment latency (ms)", 0, 1000, 25, cfg.payment_latency_ms)}
            ${slider("payment_failure_rate", "Payment failure rate", 0, 0.2, 0.01, cfg.payment_failure_rate)}
            ${slider("order_delay_ms", "Order processing delay (ms)", 0, 500, 10, cfg.order_delay_ms)}
            <label class="check"><input type="checkbox" id="cache_enabled" ${cfg.cache_enabled ? "checked" : ""}/> Product cache enabled</label>
            <button class="btn" id="apply" style="width:100%">Apply & reset baseline</button>
            <div id="tb-status"></div>
          </div>
          <div class="panel controls">
            <h3>SLO (configurable defaults)</h3>
            <div style="display:flex;gap:8px"><div class="field" style="flex:1"><label>p95 ≤ (ms)</label><input id="slo-p95" type="number" value="${state.health ? state.health.slo.p95_ms : 500}"/></div>
            <div class="field" style="flex:1"><label>errors ≤ (%)</label><input id="slo-err" type="number" step="0.1" value="${state.health ? state.health.slo.error_pct : 1}"/></div></div>
            <button class="btn secondary" id="slo-save" style="width:100%">Save SLO</button>
          </div>
          <div class="panel controls">
            <h3>Run a constraint test</h3>
            <div class="field"><label>Test</label><select id="t-kind">
              <option value="sweep">Boundary sweep (find the SLO bound)</option>
              <option value="flash">Flash sale (spike, then recovery)</option>
              <option value="steady">Steady load</option></select></div>
            <div id="t-params"></div>
            ${slider("checkout_share", "Share of checkouts (rest = browsing)", 0, 1, 0.05, 0.5)}
            <div style="display:flex;gap:8px"><button class="btn" id="t-run" style="flex:1">Run test</button><button class="btn danger" id="t-cancel" disabled>Stop</button></div>
          </div>
        </div>
        <div class="stack">
          <div class="kpis" id="t-kpis">${kpi("–", "checkout p95 (last s)")}${kpi("–", "error rate")}${kpi("–", "offered rps")}${kpi("–", "in-flight")}</div>
          <div class="panel"><h3>Live per-second telemetry</h3><div id="t-legend"></div><div id="t-chart"></div></div>
          <div class="panel"><h3>Result</h3><div id="t-result" class="small muted">Run a test to see the observed constraint.</div></div>
          <div class="panel"><h3>How the constraint changes across configs</h3>
            <div class="tiny" style="margin-bottom:8px">Every finished sweep on this server. Re-run the sweep after changing workers, payment latency or cache to see the bound shift.</div>
            <div id="t-history" class="scroll-x"></div></div>
        </div>
      </div>`;
    bindSliders(body);
    renderParams();
    $("#t-kind").addEventListener("change", renderParams);
    renderTbStatus(tb);
    $("#apply").addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Resetting & relaunching…";
      const conf = Object.fromEntries(["checkout_workers", "inventory_workers", "payment_latency_ms", "payment_failure_rate", "order_delay_ms"].map((k) => [k, +$("#" + k).value]));
      conf.cache_enabled = $("#cache_enabled").checked;
      try { renderTbStatus(await api("/api/testbed/apply", { method: "POST", body: JSON.stringify(conf) })); }
      catch (err) { $("#tb-status").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
      e.target.disabled = false; e.target.textContent = "Apply & reset baseline";
    });
    $("#slo-save").addEventListener("click", async () => {
      await api("/api/lab/slo", { method: "POST", body: JSON.stringify({ p95_ms: +$("#slo-p95").value, error_pct: +$("#slo-err").value }) });
      pollHealth();
    });
    $("#t-run").addEventListener("click", startTest);
    $("#t-cancel").addEventListener("click", async () => { if (state.lab.test) await api(`/api/lab/tests/${state.lab.test.id}/cancel`, { method: "POST" }); });
    renderHistory();
    if (state.lab.test) { renderTest(state.lab.test); if (state.lab.test.status === "running") pollTest(); }
    else drawLive({ seconds: [] });
  }

  function slider(id, label, min, max, step, value) {
    return `<div class="field"><span><label for="${id}">${label}</label><b id="${id}-v">${value}</b></span>
      <input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${value}"/></div>`;
  }
  function bindSliders(root, onChange) {
    root.querySelectorAll('input[type="range"]').forEach((r) => r.addEventListener("input", () => {
      $("#" + r.id + "-v").textContent = r.value;
      if (onChange) onChange();
    }));
  }
  const kpi = (v, k, color) => `<div class="kpi"><div class="v" style="${color ? "color:" + color : ""}">${v}</div><div class="k">${k}</div></div>`;

  function renderParams() {
    const kind = $("#t-kind").value;
    const p = $("#t-params");
    if (kind === "sweep") p.innerHTML = `<div class="field"><label>Offered rps levels</label><input id="p-levels" value="25, 50, 100, 200, 300, 450"/></div>
      ${slider("p-step", "Seconds per level", 3, 20, 1, 5)}`;
    else if (kind === "flash") p.innerHTML = `${slider("p-base", "Baseline rps", 1, 100, 1, 10)}${slider("p-peak", "Spike rps", 10, 800, 10, 300)}${slider("p-dur", "Spike seconds", 3, 30, 1, 8)}`;
    else p.innerHTML = `${slider("p-rps", "Offered rps", 1, 800, 1, 50)}${slider("p-dur", "Duration (s)", 3, 60, 1, 15)}`;
    bindSliders(p);
  }

  function renderTbStatus(tb) {
    $("#tb-status").innerHTML = `<div class="notice small ${tb.state === "running" ? "good" : tb.state === "error" ? "bad" : ""}">Testbed <b>${tb.state}</b>${tb.error ? ": " + esc(tb.error) : ""}</div>
      <div class="svc">${tb.services.map((s) => `<div>${s.alive ? "🟢" : "⚪"} ${s.service} ×${s.workers}<br><span class="tiny">${s.ready_seconds !== null ? "ready in " + s.ready_seconds + "s" : ":" + s.port}</span></div>`).join("")}</div>`;
  }

  async function startTest() {
    const kind = $("#t-kind").value;
    const share = +$("#checkout_share").value;
    let params;
    if (kind === "sweep") params = { levels: $("#p-levels").value.split(/[,\s]+/).filter(Boolean).map(Number), step_seconds: +$("#p-step").value };
    else if (kind === "flash") params = { base_rps: +$("#p-base").value, peak_rps: +$("#p-peak").value, duration: +$("#p-dur").value };
    else params = { rps: +$("#p-rps").value, duration: +$("#p-dur").value };
    params.checkout_share = share;
    try {
      state.lab.test = await api("/api/lab/tests", { method: "POST", body: JSON.stringify({ kind, params }) });
      $("#t-run").disabled = true; $("#t-cancel").disabled = false;
      $("#t-result").innerHTML = `<span class="muted">Running…</span>`;
      pollTest();
    } catch (err) { $("#t-result").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
  }

  function pollTest() {
    clearInterval(state.lab.poll);
    state.lab.poll = setInterval(async () => {
      if (!state.lab.test) return;
      const t = await api(`/api/lab/tests/${state.lab.test.id}`);
      state.lab.test = t;
      if (!$("#t-chart")) { if (t.status !== "running") clearInterval(state.lab.poll); return; }
      renderTest(t);
      if (t.status !== "running") { clearInterval(state.lab.poll); renderHistory(); pollHealth(); }
    }, 1000);
  }

  function drawLive(t) {
    const slo = state.health ? state.health.slo.p95_ms : 500;
    const s = t.seconds || [];
    const series = [
      { name: "checkout p95 (ms)", color: C.css("--s1"), points: s.filter((x) => x.checkout_p95_ms > 0).map((x) => [x.t, x.checkout_p95_ms]) },
      { name: "browse p95 (ms)", color: C.css("--s3"), points: s.filter((x) => x.browse_p95_ms > 0).map((x) => [x.t, x.browse_p95_ms]) },
      { name: "offered rps", color: C.css("--s2"), points: s.map((x) => [x.t, x.offered_rps]), dash: true },
    ];
    C.legend($("#t-legend"), [...series.map((x) => ({ name: x.name, color: x.color, dash: x.dash })), { name: `SLO p95 ${slo} ms`, color: C.css("--bad"), dash: true }]);
    C.lineChart($("#t-chart"), { series, xLabel: "seconds", yLabel: "ms · rps (log)", logY: true, hLines: [{ y: slo, label: "SLO" }], empty: "Run a test to stream per-second telemetry" });
  }

  function renderTest(t) {
    $("#t-run").disabled = t.status === "running"; $("#t-cancel").disabled = t.status !== "running";
    const last = t.seconds[t.seconds.length - 1];
    const slo = state.health ? state.health.slo : { p95_ms: 500, error_pct: 1 };
    if (last) {
      $("#t-kpis").innerHTML = kpi(Math.round(last.checkout_p95_ms) + " ms", "checkout p95 (last s)", last.checkout_p95_ms > slo.p95_ms ? "var(--bad)" : "") +
        kpi(last.error_pct + "%", "error rate", last.error_pct > slo.error_pct ? "var(--bad)" : "") + kpi(last.offered_rps, "offered rps") + kpi(last.inflight, "in-flight");
    }
    drawLive(t);
    if (t.status === "running") return;
    if (t.status === "error") { $("#t-result").innerHTML = `<div class="notice bad">${esc(t.error)}</div>`; return; }
    const r = t.result;
    const rows = t.steps.map((s) => `<tr><td>${s.phase || s.offered_rps}</td><td>${s.offered_rps}</td><td>${s.achieved_rps}</td><td>${s.checkout_p50_ms}</td><td><b>${s.checkout_p95_ms}</b></td><td>${s.error_pct}%</td>
      <td>${s.client_saturated ? "⚠️ client" : s.server_throughput_below_offered ? "server < offered" : "ok"}</td><td>${s.meets_slo === undefined ? "" : s.meets_slo ? clsPill("valid") : '<span class="pill fires">violates</span>'}</td></tr>`).join("");
    let head = "";
    if (r.observed_bound) {
      head = `<div class="notice ${r.observed_bound.first_rps_violating_slo ? "" : "good"}"><span class="label-chip live">validated on local testbed</span><br><b>Observed constraint:</b> <code>${esc(r.observed_bound.constraint)}</code>
        <div class="tiny">Tested region: ${esc(JSON.stringify(r.observed_bound.tested_region))}${r.aborted ? ` · abort condition: ${esc(r.aborted)}` : ""}</div></div>`;
    } else if (r.recovered !== undefined) {
      head = `<div class="notice ${r.recovered ? "good" : "bad"}">Recovery after spike: <b>${r.recovered ? "recovered to baseline" : "did not recover within 8 s"}</b></div>`;
    } else if (r.meets_slo !== undefined) {
      head = `<div class="notice ${r.meets_slo ? "good" : "bad"}">Steady load ${r.meets_slo ? "meets" : "violates"} the SLO.</div>`;
    }
    $("#t-result").innerHTML = head + `<div class="scroll-x"><table class="t"><tr><th>step</th><th>offered</th><th>achieved</th><th>p50 ms</th><th>p95 ms</th><th>errors</th><th>validity</th><th>SLO</th></tr>${rows}</table></div>
      <div class="tiny" style="margin-top:6px">“⚠️ client” = the load generator could not send at the requested rate, so that step measures the generator as much as the store.</div>`;
  }

  async function renderHistory() {
    const el = $("#t-history");
    if (!el) return;
    const h = (await api("/api/lab/history")).filter((x) => x.kind === "sweep" && x.result.observed_bound);
    if (!h.length) { el.innerHTML = `<span class="small muted">No sweeps yet.</span>`; return; }
    el.innerHTML = `<table class="t"><tr><th>checkout ×</th><th>inventory ×</th><th>payment ms</th><th>fail rate</th><th>cache</th><th>last rps OK</th><th>first rps ✗</th></tr>
      ${h.map((x) => { const c = x.config, b = x.result.observed_bound; return `<tr><td>${c.checkout_workers}</td><td>${c.inventory_workers}</td><td>${c.payment_latency_ms}</td><td>${c.payment_failure_rate}</td><td>${c.cache_enabled ? "on" : "off"}</td><td><b>${b.last_rps_meeting_slo ?? "–"}</b></td><td>${b.first_rps_violating_slo ?? "none seen"}</td></tr>`; }).join("")}</table>`;
  }

  // ---- model
  async function modelTab() {
    const body = $("#lab-body");
    const w = state.lab.whatifCfg || { replicas: 4, cpu_cores: 0.5, memory_mib: 1024, db_pool_size: 50, cache_enabled: true, target_rps: 300, workload: "flash_sale", mode: "static" };
    state.lab.whatifCfg = w;
    body.innerHTML = `
      <div class="lab-grid">
        <div class="panel controls">
          <h3>Cluster config <span class="label-chip model">model (simulated)</span></h3>
          <div class="tiny" style="margin-bottom:10px">SCOPE-X queuing model + the Task 14D CPU capacity model. Shows how stored constraints apply to a configuration you can't run on one laptop.</div>
          ${slider("w-replicas", "Checkout replicas", 1, 10, 1, w.replicas)}
          <div class="field"><label>CPU limit per replica</label><select id="w-cpu">${[0.5, 1, 2, 4].map((c) => `<option value="${c}" ${+w.cpu_cores === c ? "selected" : ""}>${c} core${c === 1 ? "" : "s"}</option>`).join("")}</select></div>
          <div class="field"><label>Memory limit</label><select id="w-mem">${[512, 1024, 2048].map((m) => `<option value="${m}" ${+w.memory_mib === m ? "selected" : ""}>${m >= 1024 ? m / 1024 + " GiB" : m + " MiB"}</option>`).join("")}</select></div>
          ${slider("w-pool", "DB pool size", 5, 150, 5, w.db_pool_size)}
          ${slider("w-rps", "Offered load (rps)", 10, 1500, 10, w.target_rps)}
          <label class="check"><input type="checkbox" id="w-cache" ${w.cache_enabled ? "checked" : ""}/> Cache enabled</label>
          <div class="field"><label>Workload mix</label><select id="w-work">${["flash_sale", "normal", "ramp", "viral_product", "slow_payment_api"].map((x) => `<option ${w.workload === x ? "selected" : ""}>${x}</option>`).join("")}</select></div>
          <div class="field"><label>Scaling mode (which validity applies)</label><select id="w-mode">${["static", "hpa", "vertical"].map((x) => `<option ${w.mode === x ? "selected" : ""}>${x}</option>`).join("")}</select></div>
        </div>
        <div class="stack">
          <div class="kpis" id="w-kpis"></div>
          <div id="w-warn"></div>
          <div class="panel"><h3>Predicted p95 vs offered load</h3><div id="w-legend1"></div><div id="w-curve"></div></div>
          <div class="panel"><h3>How the SLO boundary moves with scale</h3>
            <div class="tiny">Max rps the model keeps within SLO. Horizontal: vs replicas at the current CPU limit. Vertical: vs CPU limit at the current replica count. The dashed line is the DB-pool connection cap (K01: replicas ≤ pool / 6).</div>
            <div id="w-legend2"></div><div id="w-bound"></div><div id="w-bound-cpu"></div></div>
          <div class="panel"><h3>Stored constraints at this config</h3><div class="tiny" style="margin-bottom:8px">“Fires” = the rule's condition holds here (it predicts an SLO violation or a broken invariant). Validity = what the scaling study found for the selected mode.</div><div id="w-cons" class="scroll-x"></div></div>
        </div>
      </div>`;
    const read = () => ({
      replicas: +$("#w-replicas").value, cpu_cores: +$("#w-cpu").value, memory_mib: +$("#w-mem").value, db_pool_size: +$("#w-pool").value,
      target_rps: +$("#w-rps").value, cache_enabled: $("#w-cache").checked, workload: $("#w-work").value, mode: $("#w-mode").value,
    });
    const update = () => {
      state.lab.whatifCfg = read();
      clearTimeout(state.lab.whatifTimer);
      state.lab.whatifTimer = setTimeout(runWhatIf, 180);
    };
    bindSliders(body, update);
    body.querySelectorAll("select, input[type=checkbox]").forEach((x) => x.addEventListener("change", update));
    runWhatIf();
  }

  async function runWhatIf() {
    const cfg = state.lab.whatifCfg;
    const r = await api("/api/lab/whatif", { method: "POST", body: JSON.stringify(cfg) });
    if (!$("#w-kpis")) return;
    const slo = r.slo, cap = r.capacity;
    $("#w-kpis").innerHTML = kpi(Math.round(r.prediction.p95_ms) + " ms", "predicted p95", r.prediction.p95_ms > slo.p95_ms ? "var(--bad)" : "var(--good)") +
      kpi(r.prediction.error_pct + "%", "predicted errors", r.prediction.error_pct > slo.error_pct ? "var(--bad)" : "") +
      kpi(Math.round(cap.max_safe_rps), "max rps within SLO") + kpi(cap.bottleneck.split(" ")[0], `bottleneck · ${cap.per_replica_rps} rps/replica`);
    $("#w-warn").innerHTML = r.warnings.map((w) => `<div class="notice bad small">⚠️ ${esc(w)}</div>`).join("") +
      `<div class="notice small">Frontend capacity <b>${cap.frontend_rps}</b> rps vs database <b>${cap.database_rps}</b> rps → <b>${esc(cap.bottleneck)}</b> binds first. Headroom at ${cfg.target_rps} rps: <b>${cap.headroom_pct}%</b>.</div>`;
    const curve = [
      { name: "predicted p95", color: C.css("--s1"), points: r.curve.map((p) => [p.rps, p.p95_ms]) },
    ];
    C.legend($("#w-legend1"), [{ name: "predicted p95 (ms)", color: C.css("--s1") }, { name: `SLO ${slo.p95_ms} ms`, color: C.css("--bad"), dash: true }, { name: "your load", color: C.css("--ink-2"), dash: true }]);
    C.lineChart($("#w-curve"), { series: curve, xLabel: "offered rps", yLabel: "p95 ms", hLines: [{ y: slo.p95_ms, label: "SLO" }], vLines: [{ x: cfg.target_rps, label: `${cfg.target_rps} rps` }], yUnit: " ms", height: 240 });
    C.legend($("#w-legend2"), [{ name: "max safe rps vs replicas", color: C.css("--s2") }, { name: "max safe rps vs CPU limit", color: C.css("--s3") }]);
    C.lineChart($("#w-bound"), {
      series: [{ name: "max safe rps", color: C.css("--s2"), points: r.boundary_by_replicas.map((b) => [b.replicas, b.max_safe_rps]), markers: true }],
      xLabel: "checkout replicas", yLabel: "rps", vLines: [{ x: cap.max_replicas_by_pool, label: `pool cap: ${cap.max_replicas_by_pool} replicas` }, { x: cfg.replicas, label: "now", color: C.css("--s1") }], height: 220, yMin: 0,
    });
    C.lineChart($("#w-bound-cpu"), {
      series: [{ name: "max safe rps", color: C.css("--s3"), points: r.boundary_by_cpu.map((b) => [b.cpu_cores, b.max_safe_rps]), markers: true }],
      xLabel: "CPU limit (cores)", yLabel: "rps", height: 200, yMin: 0, xFmt: (v) => +v.toFixed(2) + "",
    });
    $("#w-cons").innerHTML = `<table class="t"><tr><th>ID</th><th>rule (condition ⇒ violation)</th><th>fires here?</th><th>validity in ${esc(cfg.mode)} mode</th><th>static</th><th>HPA</th><th>vertical</th></tr>
      ${r.constraints.map((c) => `<tr><td><b>${c.id}</b><div class="tiny">${esc(c.kind)}</div></td><td><code>${esc(c.rule)}</code></td><td>${c.fires ? '<span class="pill fires">fires</span>' : '<span class="pill">no</span>'}</td>
        <td>${clsPill(c.validity)}</td><td>${clsPill(c.classes.static)}</td><td>${clsPill(c.classes.hpa)}</td><td>${clsPill(c.classes.vertical)}</td></tr>`).join("")}</table>`;
  }


  // ---- flash-sale shield
  const toggle = (id, label, on, hint) => `<label class="check"><input type="checkbox" id="${id}" ${on ? "checked" : ""}/> <span><b>${label}</b>${hint ? `<br><span class="tiny">${hint}</span>` : ""}</span></label>`;

  async function shieldTab() {
    const body = $("#lab-body");
    const st = await api("/api/lab/shield");
    const c = st.config;
    body.innerHTML = `
      <div class="lab-grid">
        <div class="stack">
          <div class="panel controls">
            <h3>Shield layers <span class="label-chip live">live on this store</span></h3>
            <div class="tiny" style="margin-bottom:8px">Changes apply instantly to real shoppers (no restart). The shield guards add-to-cart and checkout; browsing stays open.</div>
            ${toggle("sh-waiting_room", "Virtual waiting room", c.waiting_room, "Queue new shoppers when traffic passes a threshold")}
            ${toggle("sh-force_queue", "Drop mode: always queue", c.force_queue, "Queue everyone, regardless of load")}
            ${toggle("sh-reservations", "Stock reservation with TTL", c.reservations, "Add-to-cart holds stock; unpaid holds expire")}
            ${toggle("sh-bot_defense", "Bot defence at the edge", c.bot_defense, "Rate limits, behaviour score, proof-of-work")}
            <h3 style="margin-top:12px">Waiting room</h3>
            ${slider("sh-admit_per_minute", "Admit shoppers / minute", 10, 3000, 10, c.admit_per_minute)}
            ${slider("sh-activate_rps", "Open when purchase rps ≥", 1, 400, 1, c.activate_rps)}
            ${slider("sh-max_active_shoppers", "…or active shoppers ≥", 5, 2000, 5, c.max_active_shoppers)}
            <h3 style="margin-top:12px">Reservations</h3>
            ${slider("sh-hold_ttl_s", "Hold TTL (s)", 5, 900, 5, c.hold_ttl_s)}
            ${slider("sh-max_per_customer", "Limited items: max per customer", 1, 10, 1, c.max_per_customer)}
            <h3 style="margin-top:12px">Bot defence</h3>
            ${slider("sh-ip_rate_per_s", "Per-IP rate (req/s)", 1, 50, 1, c.ip_rate_per_s)}
            ${slider("sh-device_rate_per_s", "Per-device rate (req/s)", 1, 50, 1, c.device_rate_per_s)}
            ${slider("sh-pow_bits", "Proof-of-work difficulty (bits)", 8, 20, 1, c.pow_bits)}
            ${slider("sh-challenge_score", "Challenge at score ≥", 0, 100, 5, c.challenge_score)}
            ${slider("sh-block_score", "Block at score ≥", 0, 100, 5, c.block_score)}
            <div style="display:flex;gap:8px"><button class="btn" id="sh-save" style="flex:1">Apply shield settings</button><button class="btn ghost" id="sh-reset" title="Clears queue, passes and counters">Clear state</button></div>
            <div id="sh-msg"></div>
          </div>
          <div class="panel controls">
            <h3>Drop test: shield off vs on</h3>
            <div class="tiny" style="margin-bottom:8px">Simulated shoppers and bots buy the limited turntable through the store's real API. Each arm resets its stock to a known baseline first.</div>
            ${slider("d-stock", "Units in the drop", 5, 200, 5, 50)}
            ${slider("d-humans", "Human shoppers", 20, 600, 10, 200)}
            ${slider("d-bots", "Bots", 0, 40, 1, 10)}
            ${slider("d-bot_rps", "Attempts / s per bot", 1, 30, 1, 8)}
            ${slider("d-arrival_s", "Humans arrive over (s)", 2, 30, 1, 10)}
            ${slider("d-abandon_pct", "Share who abandon their cart", 0, 0.8, 0.05, 0.25)}
            ${slider("d-hold_ttl_s", "Hold TTL during test (s)", 5, 120, 5, 20)}
            ${slider("d-patience_s", "Human patience in queue (s)", 10, 120, 5, 45)}
            <label class="check"><input type="checkbox" id="d-bots_solve_pow"/> Bots also solve proof-of-work</label>
            <div class="field"><label>Arms</label><select id="d-arms"><option value="both">Both: off, then on</option><option value="off">Shield off only</option><option value="on">Shield on only</option></select></div>
            <div style="display:flex;gap:8px"><button class="btn" id="d-run" style="flex:1">Run drop test</button><button class="btn danger" id="d-cancel" disabled>Stop</button></div>
          </div>
        </div>
        <div class="stack">
          <div class="kpis" id="sh-kpis"></div>
          <div class="panel"><h3>Shield right now</h3><div id="sh-legend"></div><div id="sh-chart"></div><div id="sh-counters" class="tiny"></div></div>
          <div class="panel"><h3>Drop test: stock over time</h3><div id="d-legend1"></div><div id="d-stock-chart"></div>
            <h3 style="margin-top:14px">Queue and human checkout latency</h3><div id="d-legend2"></div><div id="d-queue-chart"></div><div id="d-lat-chart"></div></div>
          <div class="panel"><h3>Drop test result</h3><div id="d-result" class="small muted">Run the drop test to compare the store with and without the shield.</div></div>
        </div>
      </div>`;
    bindSliders(body);
    const keys = ["admit_per_minute", "activate_rps", "max_active_shoppers", "hold_ttl_s", "max_per_customer", "ip_rate_per_s", "device_rate_per_s", "pow_bits", "challenge_score", "block_score"];
    $("#sh-save").addEventListener("click", async () => {
      const patch = Object.fromEntries(keys.map((k) => [k, +$("#sh-" + k).value]));
      ["waiting_room", "force_queue", "reservations", "bot_defense"].forEach((k) => (patch[k] = $("#sh-" + k).checked));
      try { renderShield(await api("/api/lab/shield/config", { method: "POST", body: JSON.stringify(patch) })); $("#sh-msg").innerHTML = `<div class="notice good small">Applied.</div>`; pollHealth(); }
      catch (err) { $("#sh-msg").innerHTML = `<div class="notice bad small">${esc(err.message)}</div>`; }
    });
    $("#sh-reset").addEventListener("click", async () => renderShield(await api("/api/lab/shield/reset", { method: "POST" })));
    $("#d-run").addEventListener("click", startDrop);
    $("#d-cancel").addEventListener("click", async () => { if (state.lab.drop) await api(`/api/lab/tests/${state.lab.drop.id}/cancel`, { method: "POST" }); });
    renderShield(st);
    state.lab.shieldPoll = setInterval(async () => {
      if (!$("#sh-kpis")) return clearInterval(state.lab.shieldPoll);
      try { renderShield(await api("/api/lab/shield")); } catch { /* ignore */ }
      if (state.lab.drop && state.lab.drop.status === "running") {
        state.lab.drop = await api(`/api/lab/tests/${state.lab.drop.id}`);
        renderDrop(state.lab.drop);
      }
    }, 1500);
    if (!state.lab.drop) {
      const last = (await api("/api/lab/history")).find((x) => x.kind === "drop");
      if (last) { try { state.lab.drop = await api(`/api/lab/tests/${last.id}`); } catch { /* server restarted */ } }
    }
    renderDrop(state.lab.drop || { seconds: [], status: "idle" });
  }

  function renderShield(st) {
    if (!$("#sh-kpis")) return;
    const c = st.counters || {};
    const layers = ["waiting_room", "reservations", "bot_defense"].filter((k) => st.config[k]).length;
    $("#sh-kpis").innerHTML = kpi(st.room_open ? "open" : "closed", `waiting room · ${layers}/3 layers on`, st.room_open ? "var(--s2)" : "") +
      kpi(st.queue_length, `in line (max ${st.max_queue})`) + kpi(st.active_shoppers, "active shoppers") + kpi(st.purchase_rps, "purchase-path rps (5 s)");
    const tl = st.timeline || [];
    const t0 = tl.length ? tl[0].ts : 0;
    const series = [
      { name: "in line", color: C.css("--s1"), points: tl.map((x) => [x.ts - t0, x.queue]) },
      { name: "active shoppers", color: C.css("--s3"), points: tl.map((x) => [x.ts - t0, x.active_shoppers]) },
      { name: "blocked + rate-limited / s", color: C.css("--s2"), points: tl.map((x) => [x.ts - t0, (x.blocked || 0) + (x.rate_limited_ip || 0) + (x.rate_limited_device || 0)]) },
    ];
    C.legend($("#sh-legend"), series.map((x) => ({ name: x.name, color: x.color })));
    C.lineChart($("#sh-chart"), { series, xLabel: "seconds (last 3 min)", yLabel: "count", height: 200, yMin: 0, empty: "No purchase-path traffic yet" });
    const show = ["requests", "queue_required", "joined_queue", "challenged", "pow_solved", "pow_failed", "blocked", "rate_limited_ip", "rate_limited_device"];
    $("#sh-counters").innerHTML = show.map((k) => `${k.replace(/_/g, " ")}: <b>${c[k] || 0}</b>`).join(" · ") +
      (st.wait_p95_s != null ? ` · queue wait p95: <b>${st.wait_p95_s}s</b>` : "") + ` · admitted: <b>${st.admitted_total}</b>`;
  }

  async function startDrop() {
    const ids = ["stock", "humans", "bots", "bot_rps", "arrival_s", "abandon_pct", "hold_ttl_s", "patience_s"];
    const params = Object.fromEntries(ids.map((k) => [k, +$("#d-" + k).value]));
    params.bots_solve_pow = $("#d-bots_solve_pow").checked;
    params.arms = $("#d-arms").value;
    try {
      state.lab.drop = await api("/api/lab/tests", { method: "POST", body: JSON.stringify({ kind: "drop", params }) });
      $("#d-result").innerHTML = `<span class="muted">Running… each arm takes roughly arrival + checkout time + the hold TTL.</span>`;
      renderDrop(state.lab.drop);
    } catch (err) { $("#d-result").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
  }

  function renderDrop(t) {
    if (!$("#d-run")) return;
    const running = t.status === "running";
    $("#d-run").disabled = running; $("#d-cancel").disabled = !running;
    const s = t.seconds || [];
    const arms = [...new Set(s.map((x) => x.arm))];
    const starts = arms.map((a) => ({ x: s.find((x) => x.arm === a).t, label: `shield ${a}` }));
    const span = s.length ? { xMin: s[0].t, xMax: s[s.length - 1].t } : {};
    const lines = (key, name, color) => arms.map((a) => ({ name: arms.length > 1 ? `${name} (${a})` : name, color, dash: a === "off", points: s.filter((x) => x.arm === a && x[key] != null).map((x) => [x.t, x[key]]) }));
    const stockSeries = [...lines("available", "available", C.css("--s1")), ...lines("held", "reserved", C.css("--s4")), ...lines("sold", "sold", C.css("--s3"))];
    C.legend($("#d-legend1"), [{ name: "available", color: C.css("--s1") }, { name: "reserved (held / checking out)", color: C.css("--s4") }, { name: "units sold", color: C.css("--s3") }, { name: "stock", color: C.css("--bad"), dash: true }, { name: "dashed segment = shield off", color: C.css("--ink-2"), dash: true }]);
    const stock = t.params && t.params.stock;
    C.lineChart($("#d-stock-chart"), { ...span, series: stockSeries, xLabel: "seconds", yLabel: "units", height: 240, yMin: 0, vLines: starts, hLines: stock ? [{ y: stock, label: "stock" }] : [], empty: "Run the drop test" });
    C.legend($("#d-legend2"), [{ name: "shoppers in line", color: C.css("--s2") }, { name: "human checkout p95 (ms)", color: C.css("--s1") }, { name: `SLO ${state.health ? state.health.slo.p95_ms : 500} ms`, color: C.css("--bad"), dash: true }]);
    C.lineChart($("#d-queue-chart"), { ...span, series: lines("queue", "in line", C.css("--s2")), xLabel: "seconds", yLabel: "in line", height: 170, yMin: 0, vLines: starts, empty: "" });
    C.lineChart($("#d-lat-chart"), { ...span, series: lines("checkout_p95_ms", "checkout p95", C.css("--s1")).map((x) => ({ ...x, points: x.points.filter((p) => p[1] > 0) })), xLabel: "seconds", yLabel: "ms", height: 170, yMin: 0, yUnit: " ms", vLines: starts, hLines: [{ y: state.health ? state.health.slo.p95_ms : 500, label: "SLO" }], empty: "" });
    if (running || t.status === "idle") return;
    if (t.status === "error") { $("#d-result").innerHTML = `<div class="notice bad">${esc(t.error)}</div>`; return; }
    const r = t.result || {};
    const A = r.arms || [];
    if (!A.length) { $("#d-result").innerHTML = `<span class="muted">Cancelled before an arm finished.</span>`; return; }
    const fmtObj = (o) => Object.entries(o || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") || "–";
    const row = (label, f, good) => `<tr><td>${label}</td>${A.map((a) => { const v = f(a); const g = good && v != null ? good(a) : null; return `<td>${g === null ? "" : g ? "🟢 " : "🔴 "}<b>${v ?? "–"}</b></td>`; }).join("")}</tr>`;
    $("#d-result").innerHTML = `<div class="notice"><span class="label-chip live">validated on local testbed</span> Drop of <b>${r.params.stock}</b> units, ${r.params.humans} humans, ${r.params.bots} bots at ${r.params.bot_rps}/s, ${Math.round(r.params.abandon_pct * 100)}% abandon, hold TTL ${r.params.hold_ttl_s}s. <span class="tiny">${esc(r.caveat)}</span></div>
      <div class="scroll-x"><table class="t"><tr><th>metric</th>${A.map((a) => `<th>shield ${a.arm}</th>`).join("")}</tr>
      ${row("units sold / stock", (a) => `${a.units_sold} / ${a.stock}`)}
      ${row("oversold units", (a) => a.oversold_units, (a) => a.oversold_units === 0)}
      ${row("units bought by bots", (a) => `${a.units_to_bots} (${a.bot_share_pct}%)`, (a) => a.bot_share_pct < 5)}
      ${row("units bought by humans", (a) => a.units_to_humans)}
      ${row("humans who tried and got one", (a) => a.human_success_pct + "%")}
      ${row("human checkout p95", (a) => a.human_checkout_p95_ms == null ? null : a.human_checkout_p95_ms + " ms", (a) => a.human_checkout_p95_ms != null && a.human_checkout_p95_ms <= (state.health ? state.health.slo.p95_ms : 500))}
      ${row("server errors (5xx / timeouts)", (a) => a.server_error_pct + "%", (a) => a.server_error_pct <= 1)}
      ${row("max queue · wait p50 / p95", (a) => `${a.max_queue} · ${a.queue_wait_p50_s ?? "–"}s / ${a.queue_wait_p95_s ?? "–"}s`)}
      ${row("expired holds returned to sale", (a) => a.expired_hold_units_returned + " units")}
      ${row("sold out after", (a) => a.sold_out_after_s == null ? "not sold out" : a.sold_out_after_s + " s")}
      ${row("human outcomes", (a) => fmtObj(a.humans))}
      ${row("bot attempts → outcomes", (a) => `${a.bot_attempts}: ${fmtObj(a.bot_outcomes)}`)}
      ${row("inventory at end (available / reserved)", (a) => `${a.inventory_available_end} / ${a.inventory_reserved_end}`)}
      </table></div>
      <div class="tiny" style="margin-top:6px">Oversold = units confirmed to buyers minus units in stock. A human who “tried” added to cart and didn't abandon.</div>`;
  }

  // ---- repository
  async function repoTab() {
    const body = $("#lab-body");
    const reg = await api("/api/lab/constraints");
    const alpha = reg.length ? reg[0].alpha : null;
    body.innerHTML = `<div class="panel" style="margin-top:16px">
      <h3>Constraint repository: validity by scaling mode</h3>
      <div class="tiny" style="margin-bottom:10px">Merged from <code>data/constraints_summary.json</code> (static, Task 12), <code>hpa_transfer_summary.json</code> (HPA, 14C) and <code>vertical_transfer_summary.json</code> (vertical, 14D). Simulated studies; “disappeared” means not observed within the tested region.${alpha ? ` Fitted CPU scaling exponent α = ${alpha}.` : ""}</div>
      <div class="scroll-x"><table class="t"><tr><th>ID</th><th>rule</th><th>static</th><th>HPA</th><th>vertical</th><th>evidence</th></tr>
      ${reg.map((c) => `<tr><td><b>${c.id}</b><div class="tiny">${esc(c.kind)}</div></td><td><code>${esc(c.rule)}</code></td><td>${clsPill(c.classes.static)}</td><td>${clsPill(c.classes.hpa)}</td><td>${clsPill(c.classes.vertical)}</td>
        <td class="tiny">${esc([c.evidence.hpa && "HPA: " + c.evidence.hpa, c.evidence.vertical && "Vertical: " + c.evidence.vertical].filter(Boolean).join(" · ").slice(0, 320))}</td></tr>`).join("")}</table></div></div>`;
  }

  // ------------------------------------------------------------------ router
  async function route() {
    const hash = location.hash || "#/";
    const [path, query] = hash.slice(1).split("?");
    const params = new URLSearchParams(query || "");
    const parts = path.split("/").filter(Boolean);
    setActive(hash);
    if (parts[0] !== "lab") { clearInterval(state.lab.poll); clearInterval(state.lab.shieldPoll); }
    state.timers.forEach(clearInterval); state.timers = [];
    window.scrollTo(0, 0);
    try {
      if (!parts.length) await home();
      else if (parts[0] === "s") await listing(params);
      else if (parts[0] === "c") await listing(params, parts[1]);
      else if (parts[0] === "p") await productPage(parts[1]);
      else if (parts[0] === "cart") await cartPage();
      else if (parts[0] === "checkout") await checkoutPage();
      else if (parts[0] === "order") orderPage(parts[1]);
      else if (parts[0] === "lab") {
        if (params.get("tab")) state.lab.tab = params.get("tab");
        await labPage();
      }
      else view.innerHTML = `<div class="panel">Page not found. <a href="#/">Go home</a></div>`;
    } catch (err) {
      view.innerHTML = `<div class="panel"><h1>Something went wrong</h1><div class="notice bad">${esc(err.message)}</div><p class="muted small">If the store just started, its services may still be launching.</p></div>`;
    }
  }

  window.addEventListener("hashchange", route);
  initShell().then(route);
})();
