(() => {
  "use strict";

  const DATA = window.SONOS_ARCHIVE_DATA;
  const app = document.querySelector("#app");
  const globalSearch = document.querySelector("#global-search");
  const toastEl = document.querySelector("#toast");
  const recordMap = new Map((DATA?.records || []).map(record => [record.id, record]));
  let comparePins = readPins();
  const smartDiffCache = new Map();

  if (!DATA) {
    app.innerHTML = '<div class="empty">archive-data.js is missing. Run <code>python scripts/build_site.py</code> first.</div>';
    return;
  }

  const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const attr = value => esc(value).replaceAll("`", "&#96;");
  const fmtNumber = value => Number(value || 0).toLocaleString();
  const recordHref = id => `#/record/${encodeURIComponent(id)}`;
  const recordLink = (record, label = null) => `<a href="${recordHref(record.id)}">${esc(label || record.title)}</a>`;
  const externalLink = (url, label) => url ? `<a href="${attr(url)}" target="_blank" rel="noreferrer">${esc(label || url)}</a>` : "";

  function fmtBytes(value) {
    if (value === null || value === undefined || value === "") return "—";
    const n = Number(value);
    if (!Number.isFinite(n)) return esc(value);
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let v = n, u = 0;
    while (Math.abs(v) >= 1024 && u < units.length - 1) { v /= 1024; u += 1; }
    return `${v.toLocaleString(undefined, { maximumFractionDigits: u ? 2 : 0 })} ${units[u]}`;
  }

  function fmtDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return esc(value);
    return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  function monthKey(value) {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value || "undated").slice(0, 7);
    return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}`;
  }

  function fmtMonth(key) {
    const match = /^(\d{4})-(\d{2})$/.exec(key || "");
    if (!match) return key || "undated";
    const parsed = new Date(Number(match[1]), Number(match[2]) - 1, 1);
    return parsed.toLocaleDateString(undefined, { year: "numeric", month: "long" });
  }

  function statusBadge(status) {
    const safe = String(status || "unknown").replace(/[^a-z0-9-]/gi, "-").toLowerCase();
    return `<span class="status status-${safe}">${esc(status || "unknown")}</span>`;
  }

  const firmwareStateLabels = {
    "decrypted": "decrypted",
    "plaintext-extracted": "plaintext extracted",
    "decryptable": "decryptable",
    "encrypted": "encrypted",
    "blocked-model-key": "blocked key",
    "plaintext-unextracted": "plaintext",
    "not-applicable": "n/a",
    "not-preserved": "not preserved",
    "unknown": "unknown",
  };

  function firmwareStateBadge(record) {
    if (record.kind !== "firmware-package") return "—";
    const state = record.decryption_state || "unknown";
    const safe = state.replace(/[^a-z0-9-]/gi, "-").toLowerCase();
    const fs = record.filesystem_indexed ? '<span class="fs-flag" title="filesystem manifest indexed">FS</span>' : "";
    return `<span class="crypto crypto-${safe}">${esc(firmwareStateLabels[state] || state)}</span>${fs}`;
  }

  function toast(message) {
    toastEl.textContent = message;
    toastEl.hidden = false;
    clearTimeout(toastEl._timer);
    toastEl._timer = setTimeout(() => { toastEl.hidden = true; }, 2200);
  }

  function readPins() {
    try {
      const parsed = JSON.parse(localStorage.getItem("sonos-archive-compare") || "[]");
      return Array.isArray(parsed) ? parsed.filter(id => typeof id === "string").slice(0, 2) : [];
    } catch { return []; }
  }

  function savePins() {
    localStorage.setItem("sonos-archive-compare", JSON.stringify(comparePins));
    renderCompareTray();
  }

  function togglePin(id) {
    if (comparePins.includes(id)) comparePins = comparePins.filter(value => value !== id);
    else { if (comparePins.length >= 2) comparePins.shift(); comparePins.push(id); }
    savePins();
    toast(comparePins.includes(id) ? "Added to comparison" : "Removed from comparison");
  }

  function renderCompareTray() {
    const tray = document.querySelector("#compare-tray");
    if (!comparePins.length) {
      tray.innerHTML = '<div class="muted">Compare tray<br><span class="small">Add any two records.</span></div>';
      return;
    }
    tray.innerHTML = `<strong>Compare tray</strong>${comparePins.map(id => {
      const r = recordMap.get(id);
      return r ? `<div class="tray-item">${recordLink(r)}<br><button class="small" data-unpin="${attr(id)}">remove</button></div>` : "";
    }).join("")}${comparePins.length === 2 ? '<a class="button" href="#/compare">Compare these</a>' : '<span class="muted small">Add one more.</span>'}`;
    tray.querySelectorAll("[data-unpin]").forEach(button => button.addEventListener("click", () => togglePin(button.dataset.unpin)));
  }

  function parseRoute() {
    const raw = location.hash.startsWith("#") ? location.hash.slice(1) : location.hash;
    const [pathRaw, queryRaw = ""] = (raw || "/overview").split("?");
    const path = pathRaw.replace(/^\/+/, "").split("/").filter(Boolean);
    return { route: path[0] || "overview", tail: path.slice(1), params: new URLSearchParams(queryRaw) };
  }

  function makeHash(route, params = new URLSearchParams()) {
    const query = params.toString();
    return `#/${route}${query ? "?" + query : ""}`;
  }

  function updateParam(name, value) {
    const current = parseRoute();
    if (value === null || value === undefined || value === "") current.params.delete(name);
    else current.params.set(name, value);
    current.params.delete("page");
    location.hash = makeHash(current.route, current.params);
  }

  function pageHeader(title, description, actions = "") {
    return `<div class="page-header"><div><h1>${esc(title)}</h1><p>${description || ""}</p></div><div class="page-actions">${actions}</div></div>`;
  }

  function uniqueValues(records, key) {
    return [...new Set(records.map(r => r[key]).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }));
  }

  function selectOptions(values, selected, allLabel) {
    return `<option value="">${esc(allLabel)}</option>` + values.map(value => `<option value="${attr(value)}" ${String(value) === String(selected || "") ? "selected" : ""}>${esc(value)}</option>`).join("");
  }

  function filterRecords(input, params) {
    const q = (params.get("q") || "").trim().toLowerCase();
    const tokens = q.split(/\s+/).filter(Boolean);
    return input.filter(record => {
      for (const key of ["category", "platform", "status", "kind", "family", "decryption_state"]) if (params.get(key) && record[key] !== params.get(key)) return false;
      return tokens.every(token => record.search.includes(token));
    });
  }

  function compareVersions(left, right) {
    const tokenize = value => String(value || "").match(/\d+|[A-Za-z]+/g) || [];
    const a = tokenize(left), b = tokenize(right);
    const count = Math.max(a.length, b.length);
    for (let i = 0; i < count; i += 1) {
      if (i >= a.length) return -1;
      if (i >= b.length) return 1;
      const an = /^\d+$/.test(a[i]), bn = /^\d+$/.test(b[i]);
      if (an && bn) {
        const av = Number(a[i]), bv = Number(b[i]);
        if (av !== bv) return av < bv ? -1 : 1;
        continue;
      }
      if (an !== bn) return an ? -1 : 1;
      const text = a[i].localeCompare(b[i], undefined, { sensitivity: "base" });
      if (text) return text;
    }
    return String(left || "").localeCompare(String(right || ""), undefined, { numeric: true, sensitivity: "base" });
  }

  function sortRecords(input, params) {
    const sort = params.get("sort") || "date";
    const direction = params.get("order") === "asc" ? 1 : -1;
    return [...input].sort((a, b) => {
      if (sort === "bytes") return (Number(a.bytes || -1) - Number(b.bytes || -1)) * direction;
      if (sort === "version") {
        const versionOrder = compareVersions(a.version, b.version);
        if (versionOrder) return versionOrder * direction;
        return String(a.title || "").localeCompare(String(b.title || ""), undefined, { numeric: true, sensitivity: "base" }) * direction;
      }
      if (sort === "date") {
        const av = a.date ? new Date(a.date).getTime() : Number.NaN;
        const bv = b.date ? new Date(b.date).getTime() : Number.NaN;
        if (Number.isFinite(av) && Number.isFinite(bv)) return (av - bv) * direction;
        if (Number.isFinite(av)) return -1 * direction;
        if (Number.isFinite(bv)) return 1 * direction;
      }
      return String(a[sort] || "").localeCompare(String(b[sort] || ""), undefined, { numeric: true, sensitivity: "base" }) * direction;
    });
  }

  function filterControls(base, params) {
    return `<div class="filters">
      <input type="search" data-filter="q" value="${attr(params.get("q") || "")}" placeholder="Search title, version, model, hash, status…">
      <select data-filter="category">${selectOptions(uniqueValues(base, "category"), params.get("category"), "All categories")}</select>
      <select data-filter="platform">${selectOptions(uniqueValues(base, "platform"), params.get("platform"), "All platforms")}</select>
      <select data-filter="status">${selectOptions(uniqueValues(base, "status"), params.get("status"), "All statuses")}</select>
      <select data-filter="kind">${selectOptions(uniqueValues(base, "kind"), params.get("kind"), "All types")}</select>
      <select data-filter="decryption_state">${selectOptions(uniqueValues(base, "decryption_state"), params.get("decryption_state"), "All firmware crypto")}</select>
    </div><div class="filter-row"><label>Rows <select data-filter="limit">${[25, 50, 100, 250, 500].map(n => `<option value="${n}" ${String(n) === (params.get("limit") || "100") ? "selected" : ""}>${n}</option>`).join("")}</select></label><button type="button" data-clear-filters>Clear filters</button></div>`;
  }

  function bindFilters(container = app) {
    container.querySelectorAll("[data-filter]").forEach(control => {
      const event = control.matches('input[type="search"]') ? "input" : "change";
      let timer;
      control.addEventListener(event, () => {
        const apply = () => updateParam(control.dataset.filter, control.value);
        if (event === "input") { clearTimeout(timer); timer = setTimeout(apply, 180); } else apply();
      });
    });
    container.querySelectorAll("[data-clear-filters]").forEach(button => button.addEventListener("click", () => { location.hash = `#/${parseRoute().route}`; }));
  }

  function sortHeader(label, key, params) {
    const active = (params.get("sort") || "date") === key;
    const marker = active ? ((params.get("order") || "desc") === "asc" ? " ▲" : " ▼") : "";
    return `<th class="sortable" data-sort="${key}">${esc(label)}${marker}</th>`;
  }

  function recordTable(records, params = new URLSearchParams(), options = {}) {
    const sorted = sortRecords(records, params);
    const limit = Number(params.get("limit") || options.limit || 100);
    const pages = Math.max(1, Math.ceil(sorted.length / limit));
    const page = Math.min(Math.max(1, Number(params.get("page") || 1)), pages);
    const slice = sorted.slice((page - 1) * limit, page * limit);
    const body = slice.map(record => `<tr>
      <td class="nowrap">${statusBadge(record.status)}</td>
      <td>${recordLink(record)}${record.subtitle ? `<div class="muted small break">${esc(record.subtitle)}</div>` : ""}</td>
      <td>${esc(record.category)}</td><td>${esc(record.platform)}</td><td class="mono nowrap">${esc(record.version || "—")}</td><td class="mono nowrap">${esc(record.model || "—")}</td>
      <td class="nowrap">${firmwareStateBadge(record)}</td><td class="right nowrap">${fmtBytes(record.bytes)}</td><td class="nowrap">${fmtDate(record.date)}</td>
      <td>${options.compare === false ? "" : `<button type="button" data-pin="${attr(record.id)}">${comparePins.includes(record.id) ? "Unpin" : "Compare"}</button>`}</td></tr>`).join("");
    const pagination = options.pagination === false ? "" : (pages > 1 ? `<div class="pagination"><span>Page ${page} of ${pages}</span><div class="pages"><button type="button" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button><button type="button" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button></div></div>` : "");
    const header = (label, key) => options.sortable === false ? `<th>${esc(label)}</th>` : sortHeader(label, key, params);
    return { html: `<div class="table-wrap"><table><thead><tr>${header("Status", "status")}${header("Artifact", "title")}${header("Category", "category")}${header("Platform", "platform")}${header("Version", "version")}${header("Model", "model")}<th>Firmware state</th>${header("Size", "bytes")}${header("Date", "date")}<th>Diff</th></tr></thead><tbody>${body || '<tr><td colspan="10" class="center muted">No matching records.</td></tr>'}</tbody></table></div>${pagination}`, total: sorted.length };
  }

  function bindTableActions(params) {
    app.querySelectorAll("[data-sort]").forEach(th => th.addEventListener("click", () => {
      const next = new URLSearchParams(params), current = next.get("sort") || "date", order = next.get("order") || "desc";
      next.set("sort", th.dataset.sort); next.set("order", current === th.dataset.sort && order === "desc" ? "asc" : "desc"); next.delete("page"); location.hash = makeHash(parseRoute().route, next);
    }));
    app.querySelectorAll("[data-pin]").forEach(button => button.addEventListener("click", () => { togglePin(button.dataset.pin); render(); }));
    app.querySelectorAll("[data-page]").forEach(button => button.addEventListener("click", () => { if (!button.disabled) { const next = new URLSearchParams(params); next.set("page", button.dataset.page); location.hash = makeHash(parseRoute().route, next); } }));
  }

  function barList(items) {
    const max = Math.max(1, ...items.map(([, n]) => n));
    return `<div class="bar-list">${items.map(([label, n]) => `<div class="bar-row"><span class="break">${esc(label)}</span><div class="bar"><span style="width:${Math.max(1, n / max * 100)}%"></span></div><span class="mono right">${fmtNumber(n)}</span></div>`).join("")}</div>`;
  }

  function overviewView() {
    const s = DATA.summary;
    const statusEntries = Object.entries(s.statuses).sort((a, b) => b[1] - a[1]);
    const categoryEntries = Object.entries(s.categories).sort((a, b) => b[1] - a[1]);
    const recent = DATA.timeline.slice(0, 12).map(item => recordMap.get(item.id)).filter(Boolean);
    const gaps = DATA.gaps.map(id => recordMap.get(id)).filter(Boolean);
    const gapKinds = Object.entries(gaps.reduce((acc, r) => ((acc[r.kind] = (acc[r.kind] || 0) + 1), acc), {})).sort((a,b)=>b[1]-a[1]).slice(0,12);
    app.innerHTML = `${pageHeader("Overview", "One index for preserved binaries, metadata-only observations, explicit gaps, source provenance, and structural diffs.")}
      <div class="stats-grid"><div class="stat"><strong>${fmtNumber(s.record_total)}</strong><span>indexed records</span></div><div class="stat"><strong>${fmtNumber(s.unique_preserved_blobs)}</strong><span>unique preserved blobs</span></div><div class="stat"><strong>${fmtBytes(s.unique_preserved_bytes)}</strong><span>deduplicated preserved bytes</span></div><div class="stat"><strong>${fmtNumber(s.gap_total)}</strong><span>known gaps / incomplete</span></div><div class="stat"><strong>${fmtNumber(s.firmware_preserved_packages)}/${fmtNumber(s.firmware_preserved_packages + s.firmware_exact_gaps)}</strong><span>known exact firmware packages preserved</span></div><div class="stat"><strong>${fmtNumber(s.firmware_decrypted_packages)}</strong><span>encrypted firmware packages decrypted</span></div></div>
      <div class="grid-2"><section class="panel"><div class="panel-header"><h2>Coverage status</h2><a href="#/gaps">open gaps</a></div><div class="panel-body">${barList(statusEntries)}</div></section><section class="panel"><div class="panel-header"><h2>Firmware analysis</h2><a href="#/firmware">open matrix</a></div><div class="panel-body">${barList(Object.entries(DATA.firmware_matrix.crypto_counts).sort((a,b)=>b[1]-a[1]))}<div class="small muted" style="margin-top:8px">${fmtNumber(s.firmware_negative_probes)} failed CDN/model probes are tracked separately and are not counted as archive gaps.</div></div></section></div>
      <section class="panel"><div class="panel-header"><h2>Archive categories</h2><a href="#/artifacts">browse all</a></div><div class="panel-body">${barList(categoryEntries)}</div></section>
      <section class="panel"><div class="panel-header"><h2>Recent dated records</h2><span><a href="#/updates">friendly updates</a> · <a href="#/timeline">full timeline</a></span></div><div class="panel-body flush">${recordTable(recent, new URLSearchParams("limit=25&sort=date&order=desc"), {limit:25, sortable:false, compare:false, pagination:false}).html}</div></section>
      <div class="grid-2"><section class="panel"><div class="panel-header"><h2>Largest gap groups</h2></div><div class="panel-body">${barList(gapKinds)}</div></section><section class="panel"><div class="panel-header"><h2>Dataset freshness</h2></div><div class="panel-body">${Object.entries(DATA.generated_from).map(([k,v])=>`<dl class="kv"><dt>${esc(k.replaceAll("_"," "))}</dt><dd class="mono">${esc(v || "not recorded")}</dd></dl>`).join("")}</div></section></div>`;
  }

  function platformLabel(platform) {
    return ({macos:"macOS",windows:"Windows",ios:"iOS / iPadOS",android:"Android",fireos:"Fire OS",web:"Web",firmware:"Speakers"})[platform] || platform;
  }

  function updateDiffHref(track, item) {
    return makeHash("update-diff", new URLSearchParams({track: track.id, version: item.version}));
  }

  function updateItemDetailsHref(track, item) {
    if (track.kind === "firmware") return makeHash("firmware", new URLSearchParams({q:item.version}));
    return recordHref(item.record_id);
  }

  function changedCount(counts) {
    return counts ? (counts.added||0)+(counts.removed||0)+(counts.changed||0) : 0;
  }

  function compactSmartSummary(diff) {
    if (!diff) return "";
    const parts=[], h=diff.highlights||{};
    if (h.translations) { const n=changedCount(h.translations); parts.push(n?`${fmtNumber(n)} translation changes`:`${fmtNumber(h.translations.same||0)} translations unchanged`); }
    if (h.images && changedCount(h.images)) parts.push(`${fmtNumber(changedCount(h.images))} image changes`);
    if (h.json_resources && changedCount(h.json_resources)) parts.push(`${fmtNumber(changedCount(h.json_resources))} JSON resource changes`);
    if (h.localized_strings?.files) parts.push(`${fmtNumber(h.localized_strings.keys_changed||0)} localized string keys`);
    if (h.mach_o?.files) parts.push(`${fmtNumber(h.mach_o.files)} native binaries analyzed`);
    if (h.mach_o && ((h.mach_o.symbols_added||0)+(h.mach_o.symbols_removed||0))) parts.push(`${fmtNumber((h.mach_o.symbols_added||0)+(h.mach_o.symbols_removed||0))} exported symbol changes`);
    if (h.plist?.files) parts.push(`${fmtNumber(h.plist.fields_changed||0)} plist fields`);
    if (h.compiled_ui?.files) parts.push(`${fmtNumber(h.compiled_ui.files)} compiled UI archives analyzed`);
    return parts.join(" · ");
  }

  function updatesView() {
    const {params}=parseRoute(), selected=params.get("platform")||"all";
    const tracks=DATA.updates?.tracks||[], platforms=[...new Set(tracks.map(t=>t.platform))];
    const visible=selected==="all"?tracks:tracks.filter(t=>t.platform===selected);
    const platformNav=["all",...platforms].map(platform=>{
      const next=new URLSearchParams(); if(platform!=="all")next.set("platform",platform);
      const count=(platform==="all"?tracks:tracks.filter(t=>t.platform===platform)).reduce((sum,t)=>sum+t.items.length,0);
      return `<a class="update-platform ${selected===platform?"active":""}" href="${makeHash("updates",next)}">${esc(platform==="all"?"All platforms":platformLabel(platform))} <span>${fmtNumber(count)}</span></a>`;
    }).join("");
    const total=visible.reduce((sum,t)=>sum+t.items.length,0);
    app.innerHTML=`${pageHeader("Updates","Browse actual release sequences by platform. Diff previous automatically chooses the adjacent release and uses semantic bundle analysis when available.",'<a class="button" href="#/timeline">Raw timeline</a>')}
      <div class="update-platforms">${platformNav}</div>
      <div class="filter-summary"><span>${fmtNumber(total)} releases across ${fmtNumber(visible.length)} tracks</span><span>Newest first.</span></div>
      ${visible.map(track=>`<section class="panel update-track"><div class="panel-header"><div><h2>${esc(track.label)}</h2><span class="muted small">${fmtNumber(track.items.length)} releases${track.items[0]?` · latest ${esc(track.items[0].version)}`:""}</span></div></div><div class="panel-body flush"><div class="table-wrap"><table class="updates-table"><thead><tr><th>Version</th><th>Status</th><th>Date</th><th>Coverage / size</th><th>What changed</th><th>Actions</th></tr></thead><tbody>${track.items.map(item=>{
        const smart=item.smart_diff_key?DATA.updates.smart_diffs[item.smart_diff_key]:null;
        let coverage=item.bytes!=null?fmtBytes(item.bytes):"—";
        if(track.kind==="firmware"){coverage=`${fmtNumber(item.preserved_count)}/${fmtNumber(item.package_count)} packages`;if(item.decrypted_count)coverage+=` · ${fmtNumber(item.decrypted_count)} decrypted`;if(item.filesystem_count)coverage+=` · ${fmtNumber(item.filesystem_count)} FS`;}
        const what=smart?`<span class="smart-mark">semantic</span> ${esc(compactSmartSummary(smart)||`${smart.summary.changed} files changed`)}`:item.previous_version?`<span class="muted">from ${esc(item.previous_version)}</span>`:'<span class="muted">first known release</span>';
        const diff=item.compare_left&&item.compare_right?`<a class="button" href="${updateDiffHref(track,item)}">Diff previous</a>`:"";
        return `<tr><td class="mono"><strong>${esc(item.version)}</strong>${item.previous_version?`<div class="muted small">← ${esc(item.previous_version)}</div>`:""}</td><td>${statusBadge(item.status)}</td><td class="nowrap">${fmtDate(item.date)}</td><td>${coverage}</td><td class="break">${what}</td><td class="nowrap"><a class="button" href="${updateItemDetailsHref(track,item)}">Open</a> ${diff}</td></tr>`;
      }).join("")}</tbody></table></div></div></section>`).join("")||'<div class="empty">No release tracks for this platform.</div>'}`;
  }

  function semanticCountsLine(counts) {
    if (!counts) return "no data";
    const n=changedCount(counts);
    if(!n) return `${fmtNumber(counts.same||0)} unchanged`;
    return [counts.added?`+${fmtNumber(counts.added)} added`:"",counts.removed?`−${fmtNumber(counts.removed)} removed`:"",counts.changed?`~${fmtNumber(counts.changed)} changed`:"",counts.same?`${fmtNumber(counts.same)} unchanged`:""].filter(Boolean).join(" · ");
  }

  function resourceKeyLabel(key) {
    if(!key||typeof key!=="object") return String(key??"");
    if("language" in key && "name" in key) return `${key.language||"?"} · ${key.name||"#"+key.index}`;
    return Object.values(key).filter(v=>v!==""&&v!=null).join(" · ");
  }

  function resourceChangesTable(section,label) {
    const rows=(section?.changes||[]).slice(0,250);
    if(!rows.length) return '<div class="notice smart-success">No semantic changes.</div>';
    return `<div class="table-wrap"><table><thead><tr><th>Resource</th><th>State</th><th>Old ${esc(label)}</th><th>New ${esc(label)}</th></tr></thead><tbody>${rows.map(row=>`<tr><td class="break">${esc(resourceKeyLabel(row.key))}</td><td class="diff-${row.status}">${esc(row.status)}</td><td class="diff-old break smart-value">${esc(valuePreview(row.old))}</td><td class="diff-new break smart-value">${esc(valuePreview(row.new))}</td></tr>`).join("")}</tbody></table></div>${section?.truncated?'<div class="notice warn">Committed analysis contains more changes than this table shows.</div>':""}`;
  }

  function smartAppDiffHtml(diff) {
    const changed=diff.files?.changed||[], summary=diff.summary||{};
    const resource=changed.find(row=>row.semantic?.type==="sonos-sclib-resource-db");
    const machos=changed.filter(row=>row.semantic?.type==="mach-o");
    const structured=changed.filter(row=>["plist","json"].includes(row.semantic?.type));
    const localized=changed.filter(row=>row.semantic?.type==="strings-table");
    const texts=changed.filter(row=>row.semantic?.type==="text");
    const databases=changed.filter(row=>row.semantic?.type==="sqlite");
    const archives=changed.filter(row=>row.semantic?.type==="zip");
    const nibs=changed.filter(row=>row.semantic?.type==="nibarchive");

    let resourceHtml="";
    if(resource){
      const sem=resource.semantic, semanticChanges=changedCount(sem.strings?.counts)+changedCount(sem.images?.counts)+changedCount(sem.jsons?.counts);
      resourceHtml=`<section class="panel smart-section"><div class="panel-header"><div><h2>Packed application resources</h2><span class="muted small">${esc(resource.path)}</span></div><span class="smart-mark">decrypted SQLite</span></div><div class="panel-body"><div class="notice ${semanticChanges?"":"smart-success"}"><strong>Detected by contents, not filename.</strong> This file is an AES-encrypted SQLite database containing translations, images, and JSON. Both versions are decrypted before comparison.${semanticChanges?"":"<br><strong>The ciphertext changed, but the decrypted semantic contents are identical.</strong>"}</div><div class="stats-grid smart-stats"><div class="stat"><strong>${semanticCountsLine(sem.strings?.counts)}</strong><span>translations</span></div><div class="stat"><strong>${semanticCountsLine(sem.images?.counts)}</strong><span>images</span></div><div class="stat"><strong>${semanticCountsLine(sem.jsons?.counts)}</strong><span>JSON resources</span></div></div>${changedCount(sem.strings?.counts)?`<details open><summary>Translation changes</summary>${resourceChangesTable(sem.strings,"text")}</details>`:""}${changedCount(sem.images?.counts)?`<details><summary>Image resource changes</summary>${resourceChangesTable(sem.images,"blob")}</details>`:""}${changedCount(sem.jsons?.counts)?`<details><summary>JSON resource changes</summary>${resourceChangesTable(sem.jsons,"content")}</details>`:""}</div></section>`;
    }

    const machoHtml=machos.length?`<section class="panel smart-section"><div class="panel-header"><h2>Native code / frameworks</h2><span>${fmtNumber(machos.length)} changed binaries</span></div><div class="panel-body flush"><div class="table-wrap"><table><thead><tr><th>Binary</th><th>Architectures</th><th>Dependencies added</th><th>Dependencies removed</th><th>Symbols added</th><th>Symbols removed</th></tr></thead><tbody>${machos.map(row=>`<tr><td class="mono break">${esc(row.path)}</td><td>${esc((row.semantic.new?.architectures||[]).join(", ")||"—")}</td><td class="diff-added break">${(row.semantic.dependencies_added||[]).map(esc).join("<br>")||"—"}</td><td class="diff-removed break">${(row.semantic.dependencies_removed||[]).map(esc).join("<br>")||"—"}</td><td class="diff-added break">${(row.semantic.symbols_added||[]).slice(0,80).map(esc).join("<br>")||"—"}</td><td class="diff-removed break">${(row.semantic.symbols_removed||[]).slice(0,80).map(esc).join("<br>")||"—"}</td></tr>`).join("")}</tbody></table></div></div></section>`:"";

    const structuredHtml=structured.length?`<section class="panel smart-section"><div class="panel-header"><h2>Structured resources / compiled UI</h2><span>${fmtNumber(structured.length)} files</span></div><div class="panel-body">${structured.map(row=>`<details><summary><code>${esc(row.path)}</code> · ${esc(row.semantic.type.toUpperCase())} · ${fmtNumber(changedCount(row.semantic.counts))} fields</summary><div class="table-wrap"><table><thead><tr><th>Field</th><th>State</th><th>Old</th><th>New</th></tr></thead><tbody>${(row.semantic.changes||[]).slice(0,250).map(c=>`<tr><td class="mono break">${esc(c.path)}</td><td class="diff-${c.status}">${c.status}</td><td class="break smart-value">${esc(valuePreview(c.old))}</td><td class="break smart-value">${esc(valuePreview(c.new))}</td></tr>`).join("")}</tbody></table></div></details>`).join("")}</div></section>`:"";

    const databaseHtml=databases.length?`<section class="panel smart-section"><div class="panel-header"><h2>SQLite databases</h2><span>${fmtNumber(databases.length)} files</span></div><div class="panel-body">${databases.map(row=>`<details><summary><code>${esc(row.path)}</code> · ${semanticCountsLine(row.semantic.counts)}</summary>${(row.semantic.tables||[]).map(table=>`<div class="smart-subsection"><h3>${esc(table.table)}</h3><div class="small muted">${fmtNumber(table.old_rows)} → ${fmtNumber(table.new_rows)} rows${table.primary_key?.length?` · key: ${esc(table.primary_key.join(", "))}`:" · no stable primary key; schema/count comparison only"}</div>${table.rows&&changedCount(table.rows.counts)?resourceChangesTable(table.rows,"row"):`<div class="notice ${table.status==="same"?"smart-success":""}">${table.status==="same"?"No keyed row changes.":table.schema_changed?"Schema changed.":"No keyed row detail available."}</div>`}</div>`).join("")}</details>`).join("")}</div></section>`:"";

    const archiveHtml=archives.length?`<section class="panel smart-section"><div class="panel-header"><h2>Archives / packages</h2><span>${fmtNumber(archives.length)} files</span></div><div class="panel-body">${archives.map(row=>`<details><summary><code>${esc(row.path)}</code> · ${semanticCountsLine(row.semantic.counts)}</summary>${resourceChangesTable(row.semantic,"member")}</details>`).join("")}</div></section>`:"";

    const nibHtml=nibs.length?`<section class="panel smart-section"><div class="panel-header"><h2>Compiled Interface Builder archives</h2><span>${fmtNumber(nibs.length)} files</span></div><div class="panel-body"><div class="notice"><strong>NIBArchive decoded semantically.</strong> Offset/record churn is ignored; the diff compares embedded UI class names, keys, selectors, and human-readable values.</div>${nibs.map(row=>`<details><summary><code>${esc(row.path)}</code> · ${semanticCountsLine(row.semantic.tokens?.counts)} tokens · ${semanticCountsLine(row.semantic.classes?.counts)} classes</summary><div class="grid-2"><div><h3>UI tokens / values</h3>${resourceChangesTable(row.semantic.tokens,"value")}</div><div><h3>Classes</h3>${resourceChangesTable(row.semantic.classes,"class")}</div></div></details>`).join("")}</div></section>`:"";

    const localizedHtml=localized.length?`<section class="panel smart-section"><div class="panel-header"><h2>Localized .strings tables</h2><span>${fmtNumber(localized.length)} files</span></div><div class="panel-body">${localized.map(row=>`<details><summary><code>${esc(row.path)}</code> · ${fmtNumber(changedCount(row.semantic.counts))} keys</summary><div class="table-wrap"><table><thead><tr><th>Key</th><th>State</th><th>Old translation</th><th>New translation</th></tr></thead><tbody>${(row.semantic.changes||[]).slice(0,250).map(c=>`<tr><td class="mono break">${esc(c.path)}</td><td class="diff-${c.status}">${c.status}</td><td class="break smart-value">${esc(valuePreview(c.old))}</td><td class="break smart-value">${esc(valuePreview(c.new))}</td></tr>`).join("")}</tbody></table></div></details>`).join("")}</div></section>`:"";

    const textHtml=texts.length?`<section class="panel smart-section"><div class="panel-header"><h2>Text resources</h2><span>${fmtNumber(texts.length)} files</span></div><div class="panel-body">${texts.map(row=>`<details><summary><code>${esc(row.path)}</code></summary><pre class="json smart-text-diff">${esc((row.semantic.diff||[]).join("\n"))}</pre></details>`).join("")}</div></section>`:"";

    const fileRows=[...(diff.files?.added||[]).map(row=>({...row,state:"added"})),...(diff.files?.removed||[]).map(row=>({...row,state:"removed"})),...changed.map(row=>({...row.new,path:row.path,state:"changed",semantic:row.semantic}))];
    const filesHtml=`<section class="panel smart-section"><div class="panel-header"><h2>Bundle files</h2><span>+${fmtNumber(summary.added)} −${fmtNumber(summary.removed)} ~${fmtNumber(summary.changed)}</span></div><div class="panel-body flush"><div class="table-wrap"><table><thead><tr><th>Path</th><th>State</th><th>Role</th><th>Detected content</th><th>Handling</th></tr></thead><tbody>${fileRows.map(row=>`<tr><td class="mono break">${esc(row.path)}</td><td class="diff-${row.state}">${row.state}</td><td>${esc(row.role||"—")}</td><td>${esc(row.type?.description||row.kind||"—")}</td><td>${row.semantic?`<span class="smart-mark">${esc(row.semantic.type)}</span>`:'<span class="muted">hash / metadata only</span>'}</td></tr>`).join("")}</tbody></table></div></div></section>`;
    return `<div class="smart-diff"><div class="stats-grid"><div class="stat"><strong>+${fmtNumber(summary.added)}</strong><span>files added</span></div><div class="stat"><strong>−${fmtNumber(summary.removed)}</strong><span>files removed</span></div><div class="stat"><strong>~${fmtNumber(summary.changed)}</strong><span>files changed</span></div><div class="stat"><strong>${fmtNumber(summary.semantically_analyzed_changed_files)}</strong><span>changed files decoded</span></div><div class="stat"><strong>${fmtNumber(summary.unchanged)}</strong><span>files unchanged</span></div></div>${resourceHtml}${machoHtml}${structuredHtml}${nibHtml}${databaseHtml}${archiveHtml}${localizedHtml}${textHtml}${filesHtml}</div>`;
  }

  async function loadSmartDiff(index) {
    if (!index?.asset) return null;
    if (smartDiffCache.has(index.asset)) return smartDiffCache.get(index.asset);
    const response = await fetch(index.asset, { cache: "force-cache" });
    if (!response.ok) throw new Error(`semantic report HTTP ${response.status}`);
    const report = await response.json();
    smartDiffCache.set(index.asset, report);
    return report;
  }

  async function updateDiffView() {
    const {params}=parseRoute();
    const track=(DATA.updates?.tracks||[]).find(t=>t.id===params.get("track"));
    const item=track?.items.find(i=>i.version===params.get("version"));
    if(!track||!item||!item.compare_left||!item.compare_right){app.innerHTML=`${pageHeader("Update diff","The requested adjacent release pair could not be resolved.")}<a href="#/updates">Back to Updates</a>`;return;}
    const left=recordMap.get(item.compare_left), right=recordMap.get(item.compare_right);
    if(!left||!right){app.innerHTML=`${pageHeader("Update diff","One of the normalized records for this release pair is missing.")}<a href="#/updates">Back to Updates</a>`;return;}
    const smartIndex=item.smart_diff_key?DATA.updates.smart_diffs[item.smart_diff_key]:null;
    const back=new URLSearchParams(); if(track.platform)back.set("platform",track.platform);
    const header=pageHeader(`${track.label}: ${item.previous_version} → ${item.version}`,track.kind==="firmware"&&item.compare_model?`Best common package model ${item.compare_model} selected automatically.`:smartIndex?"Semantic application diff: structured resources are decoded before comparison.":"Adjacent release pair selected automatically.",`<a class="button" href="${makeHash("updates",back)}">Back to updates</a><a class="button" href="#/compare?left=${encodeURIComponent(left.id)}&right=${encodeURIComponent(right.id)}">Raw compare</a>`);

    if(smartIndex){
      app.innerHTML=`${header}<div class="notice">Loading semantic application diff…</div>`;
      try{
        const smart=await loadSmartDiff(smartIndex);
        const current=parseRoute();
        if(current.route!=="update-diff"||current.params.get("track")!==params.get("track")||current.params.get("version")!==params.get("version")) return;
        app.innerHTML=`${header}${smartAppDiffHtml(smart)}<details><summary>Normalized archive metadata diff</summary>${metadataDiff(left,right)}</details>`;
        return;
      }catch(error){
        app.innerHTML=`${header}<div class="notice warn"><strong>Semantic report could not be loaded:</strong> ${esc(error.message)}. Falling back to archive metadata.</div>${metadataDiff(left,right)}`;
        return;
      }
    }

    app.innerHTML=`${header}<div class="notice">${track.platform==="macos"?"Semantic bundle analysis has not been generated for this historical pair yet; showing the best structured archive diff available.":"Showing structured archive metadata for this platform."}</div>${firmwareStructuralDiff(left,right)}${webDiff(left,right)}${androidDiff(left,right)}${metadataDiff(left,right)}`;
  }

  function explorerView(gapsOnly = false) {
    const { params } = parseRoute();
    if (gapsOnly && !params.has("sort")) {
      params.set("sort", "version");
      params.set("order", "desc");
    }
    const base = gapsOnly ? DATA.gaps.map(id => recordMap.get(id)).filter(Boolean) : DATA.records;
    const filtered = filterRecords(base, params), table = recordTable(filtered, params);
    app.innerHTML = `${pageHeader(gapsOnly ? "Missing / gaps" : "Artifacts", gapsOnly ? "Every explicitly missing, blocked, partial, or metadata-only record. Nothing is silently treated as complete." : "Every normalized record in the archive. Search and filter across firmware, apps, web captures, source archives, evidence, and recovery state.", '<button type="button" data-export="json">Export JSON</button><button type="button" data-export="csv">Export CSV</button>')}${filterControls(base, params)}<div class="filter-summary"><span>${fmtNumber(filtered.length)} matching / ${fmtNumber(base.length)} total</span><span>Sort by clicking a column heading.</span></div>${table.html}`;
    bindFilters(); bindTableActions(params); app.querySelectorAll("[data-export]").forEach(button => button.addEventListener("click", () => exportRecords(filtered, button.dataset.export)));
  }

  function timelineView() {
    const { params } = parseRoute(), base = DATA.timeline.map(item => recordMap.get(item.id)).filter(Boolean), filtered = filterRecords(base, params), grouped = new Map();
    sortRecords(filtered, new URLSearchParams("sort=date&order=desc")).forEach(record => { const key = monthKey(record.date); if (!grouped.has(key)) grouped.set(key, []); grouped.get(key).push(record); });
    app.innerHTML = `${pageHeader("Timeline", "Dated observations and captures across firmware evidence, apps, web deployments, Wayback recovery, and store metadata.")}${filterControls(base, params)}<div class="filter-summary"><span>${fmtNumber(filtered.length)} dated records</span><span>Dates are evidence/capture dates when known, not inferred release dates.</span></div><div class="timeline">${[...grouped.entries()].map(([month,items])=>`<section class="timeline-group"><div class="timeline-date">${esc(fmtMonth(month))}</div>${items.map(record=>`<div class="timeline-item"><span class="mono">${fmtDate(record.date)}</span><span>${recordLink(record)} <span class="muted">· ${esc(record.kind)} · ${esc(record.version || "")}</span></span><span>${statusBadge(record.status)}</span></div>`).join("")}</section>`).join("") || '<div class="empty">No matching timeline entries.</div>'}</div>`;
    bindFilters();
  }

  function firmwareView() {
    const { params } = parseRoute();
    const query = (params.get("q") || "").toLowerCase();
    const crypto = params.get("crypto") || "";
    const showProbes = params.get("probes") === "1";
    const matrix = DATA.firmware_matrix;
    const versions = (showProbes ? matrix.all_versions : matrix.versions).filter(v => !query || v.toLowerCase().includes(query));
    const models = showProbes ? matrix.all_models : matrix.models;

    const cellInfo = cell => {
      if (!cell) return { label: "", cls: "empty", title: "no known exact package" };
      if (cell.availability === "negative-probe") return { label: "×", cls: "probe", title: "negative CDN/model probe; not a known archive gap" };
      if (cell.availability === "exact-missing") return { label: "!", cls: "missing", title: "known exact package missing" };
      const map = {
        "decrypted": ["D", "decrypted", "encrypted source successfully decrypted and raw components archived"],
        "plaintext-extracted": ["X", "extracted", "plaintext package components extracted"],
        "decryptable": ["K", "decryptable", "required recipient key is available; package can be decrypted"],
        "blocked-model-key": ["B", "blocked", "decryption explicitly blocked on a model-specific key"],
        "encrypted": ["E", "encrypted", "encrypted package preserved; matching key not recovered"],
        "plaintext-unextracted": ["P", "preserved", "plaintext package preserved but components not extracted"],
        "not-applicable": ["P", "preserved", "preserved non-UPD firmware artifact"],
        "unknown": ["P", "preserved", "preserved package; extraction state unknown"],
      };
      const [label, cls, title] = map[cell.decryption_state] || ["P", "preserved", cell.decryption_state || "preserved"];
      return { label, cls, title };
    };

    const rows = versions.map(version => {
      const cells = matrix.cells[version] || {};
      return `<tr><td class="mono"><strong>${esc(version)}</strong></td>${models.map(model => {
        const cell = cells[String(model)];
        if (!cell) return '<td class="cell-empty"></td>';
        if (!showProbes && cell.availability === "negative-probe") return '<td class="cell-empty"></td>';
        if (crypto && cell.decryption_state !== crypto) return '<td class="cell-empty"></td>';
        const info = cellInfo(cell);
        const fs = cell.filesystem_indexed ? '<span class="matrix-fs" title="filesystem manifest indexed">F</span>' : "";
        const title = `${info.title}${cell.raw_status ? " / raw: " + cell.raw_status : ""}${cell.filesystem_indexed ? " / filesystem indexed" : ""}`;
        return `<td class="cell-${info.cls}"><a class="matrix-cell" href="${recordHref(cell.id)}" title="${attr(title)}">${info.label}${fs}</a></td>`;
      }).join("")}</tr>`;
    }).join("");

    const exactTotal = DATA.summary.firmware_preserved_packages + DATA.summary.firmware_exact_gaps;
    const preservationPct = exactTotal ? (DATA.summary.firmware_preserved_packages / exactTotal * 100).toFixed(1) : "—";

    app.innerHTML = `${pageHeader("Firmware matrix", "Preservation and decryption state for exact firmware packages. Failed speculative CDN/model probes are hidden by default because they are not archive holes.", '<a class="button" href="#/compare">Compare firmware</a>')}
      <div class="stats-grid">
        <div class="stat"><strong>${fmtNumber(DATA.summary.firmware_preserved_packages)}/${fmtNumber(exactTotal)}</strong><span>known exact packages preserved (${preservationPct}%)</span></div>
        <div class="stat"><strong>${fmtNumber(DATA.summary.firmware_decrypted_packages)}</strong><span>encrypted packages decrypted</span></div>
        <div class="stat"><strong>${fmtNumber(DATA.summary.firmware_plaintext_extracted_packages)}</strong><span>plaintext packages extracted</span></div>
        <div class="stat"><strong>${fmtNumber(DATA.summary.firmware_encrypted_packages)}</strong><span>encrypted packages awaiting keys</span></div>
        <div class="stat"><strong>${fmtNumber(DATA.summary.firmware_filesystem_indexed_packages)}</strong><span>root filesystems indexed</span></div>
        <div class="stat"><strong>${fmtNumber(DATA.summary.firmware_negative_probes)}</strong><span>negative probes, not gaps</span></div>
      </div>
      <div class="notice"><strong>Coverage semantics:</strong> ${fmtNumber(DATA.summary.firmware_negative_probes)} failed model/CDN probes are tracked as negative evidence, not missing packages. The default matrix shows preserved exact artifacts plus proven exact gaps only.</div>
      <div class="filter-row">
        <input type="search" data-fw-q value="${attr(params.get("q") || "")}" placeholder="Filter version…">
        <select data-fw-crypto>${selectOptions(["decrypted","plaintext-extracted","decryptable","encrypted","blocked-model-key","plaintext-unextracted","not-applicable","unknown"], crypto, "All firmware crypto states")}</select>
        <label><input type="checkbox" data-fw-probes ${showProbes ? "checked" : ""}> show negative CDN/model probes</label>
      </div>
      <div class="matrix-legend">
        <span><b>D</b> decrypted</span>
        <span><b>X</b> plaintext extracted</span>
        <span><b>K</b> key available</span>
        <span><b>E</b> encrypted / key unavailable</span>
        <span><b>B</b> explicitly blocked key</span>
        <span><b>P</b> preserved</span>
        <span><b>!</b> exact known gap</span>
        ${showProbes ? '<span><b>×</b> negative probe</span>' : ""}
        <span><b>F</b> corner = filesystem indexed</span>
      </div>
      <div class="matrix-wrap"><table class="matrix"><thead><tr><th>Version</th>${models.map(model => `<th title="package model ${model}">${model}</th>`).join("")}</tr></thead><tbody>${rows || '<tr><td>No versions match.</td></tr>'}</tbody></table></div>`;

    let timer;
    app.querySelector("[data-fw-q]").addEventListener("input", event => {
      clearTimeout(timer);
      timer = setTimeout(() => updateParam("q", event.target.value), 180);
    });
    app.querySelector("[data-fw-crypto]").addEventListener("change", event => updateParam("crypto", event.target.value));
    app.querySelector("[data-fw-probes]").addEventListener("change", event => updateParam("probes", event.target.checked ? "1" : ""));
  }

  function flatten(value, prefix = "", result = {}) { if (value === null || value === undefined || typeof value !== "object") { result[prefix || "(value)"] = value; return result; } if (Array.isArray(value)) { value.forEach((item,i)=>flatten(item,`${prefix}[${i}]`,result)); if(!value.length) result[prefix]=[]; return result; } const keys=Object.keys(value); if(!keys.length) result[prefix]={}; keys.forEach(key=>flatten(value[key],prefix?`${prefix}.${key}`:key,result)); return result; }
  const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
  function diffFields(left,right) { const a=flatten(left), b=flatten(right); return [...new Set([...Object.keys(a),...Object.keys(b)])].sort().map(key=>({key,old:a[key],new:b[key],status:!(key in a)?"added":!(key in b)?"removed":same(a[key],b[key])?"same":"changed"})); }
  function keyedDiff(left,right,keyFn,equalFn=same) { const a=new Map((left||[]).map(item=>[keyFn(item),item])), b=new Map((right||[]).map(item=>[keyFn(item),item])); return [...new Set([...a.keys(),...b.keys()])].sort().map(key=>({key,old:a.get(key),new:b.get(key),status:!a.has(key)?"added":!b.has(key)?"removed":equalFn(a.get(key),b.get(key))?"same":"changed"})); }
  function diffSummary(rows) { const c=rows.reduce((a,r)=>((a[r.status]=(a[r.status]||0)+1),a),{}); return `<div class="diff-summary"><span class="diff-count diff-added">+${c.added||0} added</span><span class="diff-count diff-removed">−${c.removed||0} removed</span><span class="diff-count diff-changed">~${c.changed||0} changed</span><span class="diff-count diff-same">=${c.same||0} same</span></div>`; }
  const valuePreview = v => v === undefined ? "—" : v === null ? "null" : typeof v === "object" ? JSON.stringify(v) : String(v);

  function metadataDiff(left,right) { const rows=diffFields({record:left,detail:DATA.details[left.id]||{}},{record:right,detail:DATA.details[right.id]||{}}).filter(r=>r.key!=="record.search"); return `<section class="panel"><div class="panel-header"><h2>Metadata / provenance</h2></div><div class="panel-body flush">${diffSummary(rows)}<div class="table-wrap"><table><thead><tr><th>Field</th><th>Old</th><th>New</th><th>State</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="mono break">${esc(r.key)}</td><td class="${r.status!=="same"?"diff-old":""} break">${esc(valuePreview(r.old))}</td><td class="${r.status!=="same"?"diff-new":""} break">${esc(valuePreview(r.new))}</td><td class="diff-${r.status}">${r.status}</td></tr>`).join("")}</tbody></table></div></div></section>`; }

  function firmwareStructuralDiff(left,right) {
    if(left.kind!=="firmware-package"||right.kind!=="firmware-package") return "";
    const lp=DATA.details[left.id]?.id||left.id.replace(/^firmware:/,""), rp=DATA.details[right.id]?.id||right.id.replace(/^firmware:/,""), a=DATA.compare.firmware_sections[lp], b=DATA.compare.firmware_sections[rp]; let html="";
    if(a&&b){const rows=keyedDiff(a.sections,b.sections,s=>`${s.index}:${s.section_type}:${s.name}`); html+=`<section class="panel"><div class="panel-header"><h2>UPD sections</h2><span class="muted">${esc(lp)} → ${esc(rp)}</span></div><div class="panel-body flush">${diffSummary(rows)}<div class="table-wrap"><table><thead><tr><th>Section</th><th>State</th><th>Encrypted</th><th>Old bytes</th><th>New bytes</th><th>Recipient</th><th>Hash</th></tr></thead><tbody>${rows.map(r=>{const o=r.old||{},n=r.new||{};return `<tr><td class="mono">${esc(r.key)}</td><td class="diff-${r.status}">${r.status}</td><td>${esc(String(n.encrypted??o.encrypted??"—"))}</td><td class="right">${fmtBytes(o.payload_length)}</td><td class="right">${fmtBytes(n.payload_length)}</td><td class="mono break">${esc(n.recipient_id||o.recipient_id||"—")}</td><td class="mono">${o.sha256&&n.sha256?(o.sha256===n.sha256?"same":"changed"):"—"}</td></tr>`;}).join("")}</tbody></table></div></div></section>`;}
    const fa=DATA.compare.filesystems[lp], fb=DATA.compare.filesystems[rp];
    if(fa&&fb){const rows=keyedDiff(fa.entries,fb.entries,e=>e.path); html+=`<section class="panel"><div class="panel-header"><h2>Filesystem paths</h2><span class="muted">extracted rootfs manifests</span></div><div class="panel-body flush">${diffSummary(rows)}<div class="table-wrap"><table><thead><tr><th>Path</th><th>State</th><th>Old type / size</th><th>New type / size</th><th>Hash / target</th></tr></thead><tbody>${rows.map(r=>{const o=r.old||{},n=r.new||{},h=o.sha256&&n.sha256?(o.sha256===n.sha256?"same hash":"hash changed"):(n.sha256||o.sha256||n.target||o.target||"—");return `<tr><td class="mono break">${esc(r.key)}</td><td class="diff-${r.status}">${r.status}</td><td>${esc(o.type||o.kind||"—")} / ${fmtBytes(o.size??o.bytes)}</td><td>${esc(n.type||n.kind||"—")} / ${fmtBytes(n.size??n.bytes)}</td><td class="mono break">${esc(h)}</td></tr>`;}).join("")}</tbody></table></div></div></section>`;} else if(a&&b) html+='<div class="notice">Section-level comparison is available, but both sides do not have extracted filesystem manifests. The viewer does not pretend ciphertext section changes are source-code changes.</div>';
    return html;
  }

  function webDiff(left,right){if(left.kind!=="web-deployment"||right.kind!=="web-deployment")return"";const rows=keyedDiff(DATA.details[left.id]?.resources||[],DATA.details[right.id]?.resources||[],r=>r.requested_url||r.file||r.final_url);return `<section class="panel"><div class="panel-header"><h2>Web resources</h2></div><div class="panel-body flush">${diffSummary(rows)}<div class="table-wrap"><table><thead><tr><th>Resource</th><th>State</th><th>Old outcome</th><th>New outcome</th><th>Old hash</th><th>New hash</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="break">${esc(r.key)}</td><td class="diff-${r.status}">${r.status}</td><td>${esc(r.old?.outcome||"—")}</td><td>${esc(r.new?.outcome||"—")}</td><td class="mono">${esc((r.old?.sha256||"—").slice(0,16))}</td><td class="mono">${esc((r.new?.sha256||"—").slice(0,16))}</td></tr>`).join("")}</tbody></table></div></div></section>`;}
  function androidDiff(left,right){if(left.kind!=="android-store-delivery"||right.kind!=="android-store-delivery")return"";const rows=keyedDiff(DATA.details[left.id]?.components||[],DATA.details[right.id]?.components||[],c=>c.name);return `<section class="panel"><div class="panel-header"><h2>Android delivery components</h2></div><div class="panel-body flush">${diffSummary(rows)}<div class="table-wrap"><table><thead><tr><th>Component</th><th>State</th><th>Old size</th><th>New size</th><th>Signer</th><th>Hash</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="mono">${esc(r.key)}</td><td class="diff-${r.status}">${r.status}</td><td class="right">${fmtBytes(r.old?.bytes)}</td><td class="right">${fmtBytes(r.new?.bytes)}</td><td class="mono">${esc((r.new?.signer_sha256||r.old?.signer_sha256||"—").slice(0,16))}</td><td class="mono">${r.old?.sha256&&r.new?.sha256?(r.old.sha256===r.new.sha256?"same":"changed"):"—"}</td></tr>`).join("")}</tbody></table></div></div></section>`;}
  function comparisonOptions(selected){return DATA.records.map(r=>`<option value="${attr(r.id)}" ${r.id===selected?"selected":""}>${esc(r.platform)} · ${esc(r.kind)} · ${esc(r.version||"—")} · ${esc(r.title)}</option>`).join("");}

  function smartDiffIndexForRecords(left,right){
    if(!left||!right||left.platform!==right.platform||left.family!==right.family) return null;
    const key=`${left.platform}:${left.family}:${left.version}:${right.version}`;
    return DATA.updates?.smart_diffs?.[key]||null;
  }

  async function compareView(){
    const{params}=parseRoute(),leftId=params.get("left")||comparePins[0]||"",rightId=params.get("right")||comparePins[1]||"",left=recordMap.get(leftId),right=recordMap.get(rightId);
    const smartIndex=smartDiffIndexForRecords(left,right);

    const bind=()=>{
      const leftSelect=app.querySelector("[data-compare-left]"),rightSelect=app.querySelector("[data-compare-right]"),swap=app.querySelector("[data-swap]");
      if(!leftSelect||!rightSelect||!swap)return;
      const setSide=(n,v)=>{const next=new URLSearchParams(params);if(v)next.set(n,v);else next.delete(n);location.hash=makeHash("compare",next);};
      leftSelect.addEventListener("change",e=>setSide("left",e.target.value));
      rightSelect.addEventListener("change",e=>setSide("right",e.target.value));
      swap.addEventListener("click",()=>{const next=new URLSearchParams(params);if(rightId)next.set("left",rightId);else next.delete("left");if(leftId)next.set("right",leftId);else next.delete("right");location.hash=makeHash("compare",next);});
    };
    const shell=content=>`${pageHeader("Compare","Arbitrary low-level record comparison. For normal release-to-release browsing, use Updates.","<a class=\"button\" href=\"#/updates\">Browse release updates</a>")}<div class="compare-controls"><label>Old / left<select data-compare-left><option value="">Choose a record…</option>${comparisonOptions(leftId)}</select></label><button type="button" data-swap>Swap</button><label>New / right<select data-compare-right><option value="">Choose a record…</option>${comparisonOptions(rightId)}</select></label></div>${content}`;

    if(!left||!right){
      app.innerHTML=shell('<div class="empty">Choose two records, or add two records to the compare tray while browsing.</div>');
      bind();
      return;
    }

    const intro=`<div class="notice"><strong>${recordLink(left)}</strong> → <strong>${recordLink(right)}</strong><br><span class="muted">${smartIndex?"A content-aware semantic report exists for this exact release pair.":"Binary contents are only decoded when the archive contains structural or semantic analysis."}</span></div>`;
    if(smartIndex){
      app.innerHTML=shell(`${intro}<div class="notice">Loading semantic application diff…</div>`);
      bind();
      try{
        const smart=await loadSmartDiff(smartIndex);
        const current=parseRoute();
        if(current.route!=="compare"||current.params.get("left")!==params.get("left")||current.params.get("right")!==params.get("right")) return;
        app.innerHTML=shell(`${intro}${smartAppDiffHtml(smart)}<details><summary>Normalized archive metadata diff</summary>${metadataDiff(left,right)}</details>`);
        bind();
        return;
      }catch(error){
        app.innerHTML=shell(`${intro}<div class="notice warn">Semantic report could not be loaded: ${esc(error.message)}</div>${metadataDiff(left,right)}`);
        bind();
        return;
      }
    }

    app.innerHTML=shell(`${intro}${metadataDiff(left,right)}${firmwareStructuralDiff(left,right)}${webDiff(left,right)}${androidDiff(left,right)}`);
    bind();
  }

  function sourcesView(){app.innerHTML=`${pageHeader("Sources","Rollup of provenance labels used by normalized records. Source URLs remain attached to individual artifacts.")}<div class="table-wrap"><table><thead><tr><th>Source / provenance</th><th>Records</th><th>Status</th><th>Categories</th></tr></thead><tbody>${DATA.sources.map(s=>`<tr><td class="mono break">${esc(s.name)}</td><td class="right">${fmtNumber(s.total)}</td><td>${Object.entries(s.statuses).sort((a,b)=>b[1]-a[1]).map(([n,c])=>`${statusBadge(n)} <span class="mono">${c}</span>`).join(" ")}</td><td class="break">${Object.entries(s.categories).sort((a,b)=>b[1]-a[1]).map(([n,c])=>`${esc(n)} ${c}`).join(" · ")}</td></tr>`).join("")}</tbody></table></div>`;}

  function keysView(){const base=DATA.records.filter(r=>r.category==="keys"),params=new URLSearchParams("limit=250&sort=status&order=asc");app.innerHTML=`${pageHeader("Key coverage","Recipient coverage only. Private key material is intentionally excluded from the repository and viewer.")}<div class="stats-grid"><div class="stat"><strong>${base.filter(r=>r.status==="recovered").length}</strong><span>recovered recipient keys recorded</span></div><div class="stat"><strong>${base.filter(r=>r.status==="blocked").length}</strong><span>recipient IDs still blocked</span></div><div class="stat"><strong>${fmtNumber(DATA.summary.firmware_section_manifests)}</strong><span>UPD manifests available for envelope/section evidence</span></div></div><div class="notice">A “recovered” row means the archive records successful coverage. It does not publish the private key itself.</div>${recordTable(base,params,{limit:250}).html}`;bindTableActions(params);}

  function kv(label,value,raw=false){if(value===null||value===undefined||value==="")return"";return `<dl class="kv"><dt>${esc(label)}</dt><dd>${raw?value:esc(value)}</dd></dl>`;}

  function detailSpecial(record){const detail=DATA.details[record.id]||{};let html="";if(record.kind==="firmware-package"){const pid=detail.id||record.id.replace(/^firmware:/,""),manifest=DATA.compare.firmware_sections[pid],fs=DATA.compare.filesystems[pid],raw=DATA.compare.raw_receipts[pid];if(manifest)html+=`<section class="panel"><div class="panel-header"><h2>UPD sections</h2><a href="#/compare?left=${encodeURIComponent(record.id)}">compare this package</a></div><div class="panel-body flush"><div class="table-wrap"><table><thead><tr><th>#</th><th>Name</th><th>Type</th><th>Encrypted</th><th>Payload</th><th>Recipient</th><th>SHA-256</th></tr></thead><tbody>${manifest.sections.map(s=>`<tr><td>${s.index}</td><td>${esc(s.name)}</td><td>${s.section_type}</td><td>${s.encrypted?"yes":"no"}</td><td class="right">${fmtBytes(s.payload_length)}</td><td class="mono break">${esc(s.recipient_id||"—")}</td><td class="mono break">${esc(s.sha256)}</td></tr>`).join("")}</tbody></table></div></div></section>`;if(fs){html+=`<section class="panel"><div class="panel-header"><h2>Extracted filesystem manifest</h2><span>${fmtNumber(fs.entries.length)} paths</span></div><div class="panel-body"><input type="search" data-fs-search placeholder="Filter paths…" style="width:100%;margin-bottom:7px"><div class="table-wrap"><table><thead><tr><th>Path</th><th>Type</th><th>Mode</th><th>Size</th><th>SHA-256 / target</th></tr></thead><tbody data-fs-rows></tbody></table></div></div></section>`;setTimeout(()=>bindFilesystem(fs.entries),0);}if(raw)html+=`<details><summary>Raw extraction receipt</summary><pre class="json">${esc(JSON.stringify(raw,null,2))}</pre></details>`;}if(record.kind==="web-deployment"){const resources=detail.resources||[];html+=`<section class="panel"><div class="panel-header"><h2>Deployment resources</h2><span>${fmtNumber(resources.length)} entries</span></div><div class="panel-body flush"><div class="table-wrap"><table><thead><tr><th>Outcome</th><th>Requested URL</th><th>HTTP</th><th>Size</th><th>SHA-256</th><th>Archive</th></tr></thead><tbody>${resources.map(r=>`<tr><td>${statusBadge(r.outcome==="captured"?"preserved":"missing")}</td><td class="break">${externalLink(r.requested_url,r.requested_url)}</td><td>${esc(r.status||"—")}</td><td class="right">${fmtBytes(r.bytes)}</td><td class="mono break">${esc(r.sha256||"—")}</td><td>${r.release_url?externalLink(r.release_url,"asset"):"—"}</td></tr>`).join("")}</tbody></table></div></div></section>`;}if(record.kind==="ios-app-metadata"&&detail.release_notes)html+=`<section class="panel"><div class="panel-header"><h2>Release notes</h2></div><div class="panel-body"><pre class="json">${esc(detail.release_notes)}</pre></div></section>`;return html;}

  function bindFilesystem(entries){const input=app.querySelector("[data-fs-search]"),body=app.querySelector("[data-fs-rows]");if(!input||!body)return;const draw=()=>{const q=input.value.toLowerCase(),rows=entries.filter(e=>!q||String(e.path).toLowerCase().includes(q));body.innerHTML=rows.map(e=>`<tr><td class="mono break">${esc(e.path)}</td><td>${esc(e.type||e.kind||"—")}</td><td class="mono">${e.mode??"—"}</td><td class="right">${fmtBytes(e.size??e.bytes)}</td><td class="mono break">${esc(e.sha256||e.target||"—")}</td></tr>`).join("");};input.addEventListener("input",draw);draw();}

  function recordView(encodedId){const id=decodeURIComponent(encodedId||""),record=recordMap.get(id);if(!record){app.innerHTML=pageHeader("Record not found",`No normalized record named <code>${esc(id)}</code>.`)+'<a href="#/artifacts">Back to artifacts</a>';return;}const detail=DATA.details[id]||{},duplicates=record.sha256?DATA.records.filter(r=>r.id!==id&&r.sha256===record.sha256):[],childRecords=(record.children||[]).map(c=>recordMap.get(c)).filter(Boolean),sourceLinks=record.source_urls.map(url=>`<li>${externalLink(url,url)}</li>`).join("");app.innerHTML=`${pageHeader(record.title,`${statusBadge(record.status)} &nbsp; ${esc(record.kind)} · ${esc(record.platform)}`,`<button type="button" data-pin="${attr(record.id)}">${comparePins.includes(record.id)?"Remove from compare":"Add to compare"}</button><button type="button" data-copy-id>Copy ID</button><button type="button" data-copy-json>Copy JSON</button>${record.release_url?externalLink(record.release_url,"Download / release"):""}`)}<div class="detail-grid"><section class="panel"><div class="panel-header"><h2>Record</h2></div><div class="panel-body">${kv("ID",`<code>${esc(record.id)}</code>`,true)}${kv("Category",record.category)}${kv("Type",record.kind)}${kv("Platform",record.platform)}${kv("Family",record.family)}${kv("Version",record.version)}${kv("Package model",record.model)}${kv("Product",record.product)}${kv("Status",statusBadge(record.status),true)}${kv("Date",record.date)}${kv("Size",record.bytes!=null?`${fmtBytes(record.bytes)} (${fmtNumber(record.bytes)} bytes)`:"")}${kv("SHA-256",record.sha256?`<code>${esc(record.sha256)}</code>`:"",true)}${kv("Artifact status",record.artifact_status)}${kv("Availability",record.availability)}${kv("Raw status",record.raw_status)}${record.kind==="firmware-package"?kv("Decryption state",firmwareStateBadge(record),true):""}${record.kind==="firmware-package"?kv("Source encrypted",record.source_encrypted?"yes":"no"):""}${record.kind==="firmware-package"?kv("Raw components extracted",record.components_extracted?"yes":"no"):""}${record.kind==="firmware-package"?kv("Filesystem indexed",record.filesystem_indexed?"yes":"no"):""}${record.kind==="firmware-package"&&record.recipient_ids?.length?kv("Recipient IDs",record.recipient_ids.map(id=>`<code>${esc(id)}</code>`).join("<br>"),true):""}${kv("Note",record.note)}</div></section><aside><section class="panel"><div class="panel-header"><h2>Provenance</h2></div><div class="panel-body"><div class="tags">${record.sources.map(s=>`<span class="tag">${esc(s)}</span>`).join("")||'<span class="muted">No source label</span>'}</div>${sourceLinks?`<h3>Source URLs</h3><ul class="break">${sourceLinks}</ul>`:""}${record.tags.length?`<h3>Tags</h3><div class="tags">${record.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join("")}</div>`:""}</div></section>${duplicates.length?`<section class="panel"><div class="panel-header"><h2>Same SHA-256</h2></div><div class="panel-body">${duplicates.map(r=>`<div>${recordLink(r)} <span class="muted">${esc(r.kind)}</span></div>`).join("")}</div></section>`:""}</aside></div>${childRecords.length?`<section class="panel"><div class="panel-header"><h2>Contained / child records</h2><span>${childRecords.length}</span></div><div class="panel-body flush">${recordTable(childRecords,new URLSearchParams("limit=250&sort=title&order=asc"),{limit:250,sortable:false,pagination:false}).html}</div></section>`:""}${detailSpecial(record)}<details><summary>Raw normalized source object</summary><pre class="json">${esc(JSON.stringify(detail,null,2))}</pre></details>`;app.querySelectorAll("[data-pin]").forEach(button=>button.addEventListener("click",()=>{togglePin(button.dataset.pin);recordView(encodedId);}));app.querySelector("[data-copy-id]").addEventListener("click",()=>copyText(record.id,"Record ID copied"));app.querySelector("[data-copy-json]").addEventListener("click",()=>copyText(JSON.stringify({record,detail},null,2),"Record JSON copied"));}

  function aboutView(){app.innerHTML=`${pageHeader("Data / help","How the viewer interprets the repository. The viewer does not make completeness claims beyond the archive metadata.")}<div class="grid-2"><section class="panel"><div class="panel-header"><h2>Status meanings</h2></div><div class="panel-body">${kv("preserved","Exact binary/body is archived and referenced.")}${kv("complete","A declared coverage target is complete.")}${kv("recovered","Recovery coverage is recorded; private key material is not published.")}${kv("missing","An exact known artifact is unavailable/unpreserved.")}${kv("partial","Some parts are archived, but the record is explicitly incomplete.")}${kv("metadata-only","Public metadata is preserved, but the corresponding binary is not.")}${kv("blocked","Extraction/recovery is blocked on a specific prerequisite.")}${kv("observed","Evidence/source metadata exists; this is not itself a preserved binary.")}</div></section><section class="panel"><div class="panel-header"><h2>Keyboard / links</h2></div><div class="panel-body">${kv("/","Focus global search")}${kv("Escape","Clear/focus out of global search")}${kv("Deep links","Every view, filter, comparison, and record has a hash URL that can be copied.")}${kv("Compare tray","Add two records from any artifact table or detail view, then open Compare.")}${kv("Exports","Artifact and gap views export the current filtered result set as JSON or CSV.")}</div></section></div><section class="panel"><div class="panel-header"><h2>Build inputs</h2></div><div class="panel-body">${Object.entries(DATA.generated_from).map(([k,v])=>kv(k.replaceAll("_"," "),v||"not recorded")).join("")}${kv("Normalized records",fmtNumber(DATA.summary.record_total))}${kv("Schema",DATA.schema_version)}${kv("Repository",externalLink(`https://github.com/${DATA.repository}`,DATA.repository),true)}</div></section><div class="notice">The UI is generated entirely from committed archive metadata. It does not use AI classification, infer missing versions, or silently substitute nearby releases for exact missing artifacts.</div>`;}

  function exportRecords(records,format){if(format==="json"){downloadBlob("sonos-archive-export.json",JSON.stringify(records,null,2),"application/json");return;}const fields=["id","category","kind","platform","family","version","model","product","status","title","date","bytes","sha256","release_url"],csv=[fields.join(","),...records.map(r=>fields.map(f=>csvCell(r[f])).join(","))].join("\n");downloadBlob("sonos-archive-export.csv",csv,"text/csv");}
  const csvCell=value=>`"${String(value??"").replaceAll('"','""')}"`;
  function downloadBlob(name,content,type){const blob=new Blob([content],{type}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=name;document.body.appendChild(link);link.click();link.remove();URL.revokeObjectURL(url);}
  async function copyText(text,success="Copied"){try{await navigator.clipboard.writeText(text);toast(success);}catch{const area=document.createElement("textarea");area.value=text;area.style.position="fixed";area.style.opacity="0";document.body.appendChild(area);area.select();document.execCommand("copy");area.remove();toast(success);}}

  function render(){const{route,tail}=parseRoute();document.querySelectorAll(".sidebar > a[data-route]").forEach(link=>link.classList.toggle("active",link.dataset.route===route));if(route==="overview")overviewView();else if(route==="updates")updatesView();else if(route==="update-diff")updateDiffView();else if(route==="artifacts")explorerView(false);else if(route==="gaps")explorerView(true);else if(route==="reviews")reviewsView();else if(route==="timeline")timelineView();else if(route==="firmware")firmwareView();else if(route==="compare")compareView();else if(route==="sources")sourcesView();else if(route==="keys")keysView();else if(route==="record")recordView(tail.join("/"));else if(route==="about")aboutView();else app.innerHTML=pageHeader("Not found",`Unknown view <code>${esc(route)}</code>.`)+'<a href="#/overview">Open overview</a>';app.focus({preventScroll:true});}

  document.querySelector("#copy-link").addEventListener("click",()=>copyText(location.href,"Deep link copied"));
  globalSearch.addEventListener("keydown",event=>{if(event.key==="Enter"){const params=new URLSearchParams();if(globalSearch.value.trim())params.set("q",globalSearch.value.trim());location.hash=makeHash("artifacts",params);}else if(event.key==="Escape"){globalSearch.value="";globalSearch.blur();}});
  document.addEventListener("keydown",event=>{if(event.key==="/"&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)){event.preventDefault();globalSearch.focus();}});
  window.addEventListener("hashchange",render);
  const latestMetadata=DATA.generated_from.desktop_updated||DATA.generated_from.android_updated||DATA.generated_from.apple_checked||DATA.generated_from.catalog_generated||"";
  document.querySelector("#freshness").textContent=latestMetadata?`latest metadata ${fmtDate(latestMetadata)}`:"";
  renderCompareTray();
  if(!location.hash) location.hash="#/overview"; else render();
})();
