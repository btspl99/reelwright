(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  let token = localStorage.getItem("rw_token") || "";
  let registering = false;
  const streams = new Map();

  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const kid of kids) n.append(kid);
    return n;
  };

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (token) headers.Authorization = "Bearer " + token;
    if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
    const res = await fetch("/api" + path, { method: opts.method || "GET", headers, body: opts.body });
    if (res.status === 401 && token) { signOut(); throw new Error("Session expired. Please sign in again."); }
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : d && d.message ? d.message : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : "Request failed (" + res.status + ")");
    }
    return data;
  }

  function signOut() {
    token = ""; localStorage.removeItem("rw_token");
    for (const es of streams.values()) es.close();
    streams.clear(); render();
  }

  async function render() {
    $("#auth").hidden = !!token; $("#app").hidden = !token; $("#nav").hidden = !token;
    $("#auth-title").textContent = registering ? "Create account" : "Sign in";
    $("#auth-toggle").textContent = registering ? "I already have an account" : "Create an account instead";
    if (!token) return;
    try {
      const me = await api("/me");
      $("#who").textContent = me.user.email;
      const u = me.usage;
      $("#usage").textContent = u && u.minutes_limit > 0 ? `${u.minutes_used.toFixed(0)}/${u.minutes_limit} min` : "";
      await loadJobs(); loadKeys().catch(() => {});
    } catch (e) { $("#job-error").textContent = e.message; }
  }

  async function loadJobs() {
    const data = await api("/jobs?limit=20");
    const box = $("#jobs"); box.replaceChildren();
    if (!data.items.length) box.append(el("p", { class: "meta" }, "No jobs yet."));
    for (const j of data.items) box.append(jobCard(j));
    for (const j of data.items) if (["queued", "running"].includes(j.status)) follow(j.id);
  }

  function jobCard(j) {
    const card = el("div", { class: "job", id: "job-" + j.id });
    card.append(el("h3", {}, j.title || j.source));
    card.append(el("span", { class: "badge " + j.status }, j.status + (j.stage && j.status === "running" ? " · " + j.stage : "")));
    const bar = el("div", { class: "bar" }, el("i", { style: "width:" + Math.round((j.progress || 0) * 100) + "%" }));
    if (["queued", "running"].includes(j.status)) card.append(bar);
    if (j.error) card.append(el("p", { class: "error" }, j.error));
    const row = el("div", { class: "row" });
    if (["queued", "running"].includes(j.status)) row.append(el("button", { class: "ghost", onclick: () => act(() => api(`/jobs/${j.id}/cancel`, { method: "POST" })) }, "Cancel"));
    else row.append(el("button", { class: "ghost", onclick: () => act(() => api(`/jobs/${j.id}`, { method: "DELETE" })) }, "Delete"));
    if (j.status === "succeeded") row.append(el("button", { class: "ghost", onclick: () => open(j.id, card) }, "Show clips"));
    card.append(row);
    return card;
  }

  async function open(id, card) {
    const job = await api("/jobs/" + id);
    let grid = card.querySelector(".clips"); if (grid) grid.remove();
    grid = el("div", { class: "clips" });
    for (const c of job.clips || []) {
      if (c.status !== "ready") continue;
      const v = el("video", { controls: "", preload: "none", src: c.video_url, poster: c.thumb_url || "" });
      grid.append(el("div", { class: "clip" }, v, el("p", {}, c.title || "Untitled"),
        el("p", { class: "meta" }, `score ${Math.round(((c.scores && c.scores.overall) || 0) * 100)} · ${c.duration.toFixed(0)}s`),
        el("a", { href: c.video_url, download: "" }, "Download")));
    }
    card.append(grid);
  }

  function follow(id) {
    if (streams.has(id)) return;
    // EventSource cannot send headers, so stream with fetch.
    const ctrl = new AbortController(); streams.set(id, { close: () => ctrl.abort() });
    fetch(`/api/jobs/${id}/events`, { headers: { Authorization: "Bearer " + token }, signal: ctrl.signal }).then(async (res) => {
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i; while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const j = JSON.parse(line.slice(6));
          const old = $("#job-" + id); if (old) old.replaceWith(jobCard(j));
          if (["succeeded", "failed", "canceled"].includes(j.status)) { streams.delete(id); render(); return; }
        }
      }
    }).catch(() => {}).finally(() => streams.delete(id));
  }

  async function act(fn) { try { await fn(); await loadJobs(); } catch (e) { $("#job-error").textContent = e.message; } }

  $("#auth-toggle").addEventListener("click", () => { registering = !registering; render(); });
  $("#logout").addEventListener("click", signOut);
  $("#auth-form").addEventListener("submit", async (ev) => {
    ev.preventDefault(); const f = new FormData(ev.target); $("#auth-error").textContent = "";
    try {
      const data = await api(registering ? "/auth/register" : "/auth/login", { method: "POST", json: { email: f.get("email"), password: f.get("password") } });
      token = data.token; localStorage.setItem("rw_token", token); ev.target.reset(); render();
    } catch (e) { $("#auth-error").textContent = e.message; }
  });
  $("#job-form").addEventListener("submit", async (ev) => {
    ev.preventDefault(); const f = new FormData(ev.target); $("#job-error").textContent = "";
    const options = {
      max_clips: +f.get("max_clips"), min_seconds: +f.get("min_seconds"), max_seconds: +f.get("max_seconds"),
      caption_style: f.get("caption_style"), layout: f.get("layout"), platform: f.get("platform"), use_llm: f.get("use_llm") === "on",
    };
    try {
      const file = f.get("file");
      if (file && file.size) {
        const body = new FormData(); body.append("file", file); body.append("options", JSON.stringify(options));
        await api("/jobs/upload", { method: "POST", body });
      } else if (f.get("url")) await api("/jobs", { method: "POST", json: { url: f.get("url"), options } });
      else throw new Error("Paste a video URL or choose a file.");
      ev.target.reset(); await loadJobs();
    } catch (e) { $("#job-error").textContent = e.message; }
  });

  async function loadKeys() {
    const keys = await api("/keys");
    const box = $("#keys"); box.replaceChildren();
    for (const k of keys) {
      if (k.revoked_at) continue;
      box.append(el("div", { class: "row" }, el("span", { class: "meta" }, `${k.name} (${k.prefix}…)`),
        el("button", { type: "button", class: "ghost", onclick: async () => { await api("/keys/" + k.id, { method: "DELETE" }); loadKeys(); } }, "Revoke")));
    }
  }
  $("#key-form").addEventListener("submit", async (ev) => {
    ev.preventDefault(); $("#key-error").textContent = "";
    try {
      const data = await api("/keys", { method: "POST", json: { name: new FormData(ev.target).get("name") } });
      $("#key-value").textContent = data.key; $("#key-new").hidden = false; ev.target.reset(); loadKeys();
    } catch (e) { $("#key-error").textContent = e.message; }
  });
  render();
})();
