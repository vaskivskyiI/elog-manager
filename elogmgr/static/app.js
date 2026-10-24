"use strict";
/* ELOG Manager front end: plain JS, no build step. */

const S = { state: null, groupsDraft: null, selected: new Set(), filter: "", userSel: null, userPending: {} };

// ------------------------------------------------------------------ utils
const $ = (sel, root = document) => root.querySelector(sel);
const norm = (k) => k.trim().split(/\s+/).join(" ").toLowerCase();
const splitList = (v) => (v || "").split(",").map((s) => s.trim()).filter(Boolean);
const clone = (o) => JSON.parse(JSON.stringify(o));
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-Requested-With": "elogmgr" },
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  let data = {};
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (r.status === 401 && !url.endsWith("api/login")) { showLogin(); throw new Error("Not logged in"); }
  if (!r.ok) { const e = new Error(data.error || r.statusText); e.data = data; e.status = r.status; throw e; }
  return data;
}
const apiUrl = (p) => p.replace(/^\//, "");  // relative, so a URL prefix behind a proxy works

function toast(msg, err) {
  const d = h("div", { class: err ? "err" : "" }, msg);
  $("#toast").append(d);
  setTimeout(() => d.remove(), err ? 7000 : 3500);
}

function modal(title, body, actions) {
  return new Promise((resolve) => {
    const close = (v) => { bg.remove(); resolve(v); };
    const bg = h("div", { class: "modal-bg", onmousedown: (e) => { if (e.target === bg) close(null); } },
      h("div", { class: "modal" }, h("h2", {}, title), body,
        h("div", { class: "actions" },
          h("button", { onclick: () => close(null) }, "Cancel"),
          actions.map((a) => h("button", {
            class: a.class || "primary",
            onclick: async () => {
              if (a.validate) { const ok = await a.validate(); if (!ok) return; }
              close(a.value === undefined ? true : a.value);
            },
          }, a.label)))));
    $("#modal-root").append(bg);
    const f = bg.querySelector("input, select, textarea");
    if (f) f.focus();
  });
}

function renderDiff(text) {
  const pre = h("pre", { class: "diff" });
  if (!text) { pre.append("(no changes)"); return pre; }
  for (const line of text.split("\n")) {
    const cls = line.startsWith("+++") || line.startsWith("---") ? "hunk"
      : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "";
    pre.append(cls ? h("span", { class: cls }, line) : line + "\n");
  }
  return pre;
}

function issueList(issues) {
  return h("div", {}, issues.map((i) => h("div", { class: "issue " + i.level },
    h("span", { class: "lvl" }, i.level), h("span", { class: "msg" }, h("b", {}, `[${i.section}] `), i.message))));
}

/** Preview ops as a diff, ask for confirmation, then write. Returns true when saved. */
async function commit(ops, summary, extra = {}) {
  let preview;
  try {
    preview = await api("POST", apiUrl("/api/apply"), { etag: S.state.etag, ops, dry_run: true, summary, ...extra });
  } catch (e) {
    toast(e.message, true);
    if (e.data && e.data.conflict) await reload();
    return false;
  }
  if (!preview.diff) { toast("No changes to save"); return false; }
  const body = h("div", {},
    h("p", { class: "muted" }, "These lines of elogd.cfg will change. A backup of the current file is kept."),
    renderDiff(preview.diff),
    preview.new_issues.length ? h("div", {}, h("h3", { style: "margin-top:12px" }, "New issues this change introduces"),
      issueList(preview.new_issues)) : null);
  const ok = await modal(summary || "Review changes", body, [{ label: "Save to elogd.cfg" }]);
  if (!ok) return false;
  try {
    const res = await api("POST", apiUrl("/api/apply"), { etag: S.state.etag, ops, summary, ...extra });
    if (res.reload && !res.reload.ok) toast("Saved, but ELOG was not reloaded: " + res.reload.message, true);
    else toast("Saved and " + (res.reload ? res.reload.message : "written") + ".");
    await reload();
    return true;
  } catch (e) {
    toast(e.message, true);
    if (e.data && e.data.conflict) await reload();
    return false;
  }
}

// ------------------------------------------------------------- state & nav
async function reload() {
  S.state = await api("GET", apiUrl("/api/state"));
  document.body.classList.toggle("super", isSuper());
  $("#whoami").textContent = `${S.state.me.name} · ${isSuper() ? "manager admin" : "logbook admin"}`;
  const n = S.state.issues.filter((i) => i.level === "error").length;
  $("#issue-count").textContent = n || "";
  render();
}
const isSuper = () => S.state && S.state.me.role === "super";
const lbByName = (n) => S.state.logbooks.find((l) => l.name.toLowerCase() === n.toLowerCase());
const groupByName = (groups, n) => groups.find((g) => g.name.toLowerCase() === n.toLowerCase());
const getItem = (items, key) => { const f = items.find(([k]) => norm(k) === norm(key)); return f ? f[1] : undefined; };
const globalVal = (key) => getItem(S.state.global, key);
const userNames = () => S.state.users.map((u) => u.name);

function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  return { view: parts[0] || "logbooks", arg: parts[1], tab: parts[2] };
}
function go(...parts) { location.hash = "#/" + parts.map(encodeURIComponent).join("/"); }

function render() {
  if (!S.state) return;
  const r = route();
  for (const a of document.querySelectorAll("#nav a"))
    a.classList.toggle("active", a.dataset.view === r.view || (r.view === "logbook" && a.dataset.view === "logbooks"));
  const app = $("#app");
  const views = { logbooks: viewLogbooks, logbook: viewLogbook, users: viewUsers, issues: viewIssues,
    history: viewHistory, global: viewGlobal, raw: viewRaw };
  const scroll = window.scrollY;
  app.replaceChildren((views[r.view] || viewLogbooks)(r));
  if (r.view === S.lastView) window.scrollTo(0, scroll);
  S.lastView = r.view;
}
window.addEventListener("hashchange", () => render());

// ---------------------------------------------------------------- chips
function chipInput(values, suggestions, onChange, opts = {}) {
  const box = h("div", { class: "chips" });
  const listId = "dl" + Math.random().toString(36).slice(2);
  const known = new Set((suggestions || []).map((s) => s.toLowerCase()));
  const exact = new Set(suggestions || []);  // flag case mismatches too; keep names identical to the account
  const draw = () => {
    box.replaceChildren();
    values.forEach((v, i) => box.append(h("span", {
      class: "chip" + (opts.validate && !exact.has(v) ? " bad" : ""),
      title: opts.validate && !exact.has(v) ? (known.has(v.toLowerCase()) ? "Case differs from the account name" : "Unknown name") : null,
    }, v, h("button", { title: "Remove", onclick: () => { values.splice(i, 1); onChange(values); draw(); } }, "×"))));
    const inp = h("input", { list: listId, placeholder: opts.placeholder || "add…" });
    const add = () => {
      for (const v of splitList(inp.value))
        if (!values.some((x) => x.toLowerCase() === v.toLowerCase())) values.push(v);
      inp.value = ""; onChange(values); draw(); box.querySelector("input").focus();
    };
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); if (inp.value.trim()) add(); }
      else if (e.key === "Backspace" && !inp.value && values.length) { values.pop(); onChange(values); draw(); box.querySelector("input").focus(); }
    });
    inp.addEventListener("change", () => { if (inp.value.trim() && known.has(inp.value.trim().toLowerCase())) add(); });
    inp.addEventListener("blur", () => { if (inp.value.trim()) add(); });
    box.append(inp, h("datalist", { id: listId }, (suggestions || []).map((s) => h("option", { value: s }))));
  };
  draw();
  return box;
}

// ============================================================ LOGBOOKS VIEW
function draftGroups() { return S.groupsDraft || S.state.groups; }

const lc = (s) => s.toLowerCase();
/** A member names a subgroup only if no logbook has that name ("Group Playground = Playground" is a logbook). */
const asGroup = (groups, m) => (lbByName(m) ? null : groupByName(groups, m));

/** Top-level groups (not a member of another group), plus groups only reachable through a cycle. */
function topGroups(groups) {
  const nested = new Set(groups.flatMap((g) => g.members.filter((m) => asGroup(groups, m)).map(lc)));
  const top = groups.filter((g) => !nested.has(lc(g.name)));
  const seen = new Set();
  const walk = (g) => {
    if (seen.has(lc(g.name))) return;
    seen.add(lc(g.name));
    for (const m of g.members) { const sub = asGroup(groups, m); if (sub) walk(sub); }
  };
  top.forEach(walk);
  return top.concat(groups.filter((g) => !seen.has(lc(g.name))));
}

/** True if `name` is `ancestor` itself or appears anywhere inside it. */
function containsGroup(groups, ancestor, name) {
  const seen = new Set();
  const walk = (gname) => {
    if (lc(gname) === lc(name)) return true;
    if (seen.has(lc(gname))) return false;
    seen.add(lc(gname));
    const g = groupByName(groups, gname);
    return !!g && g.members.some((m) => asGroup(groups, m) && walk(m));
  };
  return walk(ancestor);
}

function viewLogbooks() {
  const groups = draftGroups();
  const isGroup = (n) => !!asGroup(groups, n);
  const grouped = new Set(groups.flatMap((g) => g.members.map(lc)));
  const issuesBy = {};
  for (const i of S.state.issues) if (i.level !== "info") (issuesBy[lc(i.section)] ||= []).push(i);
  const f = lc(S.filter);
  const match = (lb) => !f || lc(lb.name).includes(f) || lc(getItem(lb.items, "Comment") || "").includes(f)
    || splitList(getItem(lb.items, "Login user")).some((u) => lc(u) === f);
  const canDrag = isSuper();
  const tops = topGroups(groups);
  S.collapsed ||= new Set();

  const dropZone = (onDrop) => ({
    ondragover: (e) => { if (!canDrag) return; e.preventDefault(); e.stopPropagation(); e.currentTarget.classList.add("drop"); },
    ondragleave: (e) => e.currentTarget.classList.remove("drop"),
    ondrop: (e) => { e.stopPropagation(); e.currentTarget.classList.remove("drop"); onDrop(e); },
  });
  const arrows = (onUp, onDown, upOk, downOk, what) => canDrag ? h("span", { class: "mv" },
    h("button", { class: "link", title: `Move ${what} up`, disabled: !upOk, onclick: (e) => { e.stopPropagation(); onUp(); } }, "↑"),
    h("button", { class: "link", title: `Move ${what} down`, disabled: !downOk, onclick: (e) => { e.stopPropagation(); onDown(); } }, "↓")) : null;

  const lbRow = (member, group, i, n) => {
    const lb = lbByName(member);
    const mv = group ? arrows(() => moveWithin(group, i, -1), () => moveWithin(group, i, 1), i > 0, i < n - 1, "logbook") : null;
    if (!lb) {
      if (f && !lc(member).includes(f)) return null;
      return h("div", { class: "lb missing", draggable: canDrag, title: "This logbook does not exist",
        ondragstart: (e) => dragStart(e, member, group) },
        mv, h("span", { class: "name" }, member), h("span", { class: "comment" }, "missing logbook"),
        group && canDrag ? h("button", { class: "link", title: "Remove from group", onclick: () => moveMember(member, group, null) }, "×") : null);
    }
    if (!match(lb)) return null;
    const login = splitList(getItem(lb.items, "Login user"));
    const iss = issuesBy[lc(lb.name)] || [];
    return h("div", { class: "lb" + (lb.editable ? "" : " readonly"), draggable: canDrag,
      ondragstart: (e) => dragStart(e, lb.name, group), ondragend: (e) => e.target.classList.remove("dragging"),
      ...dropZone((e) => dropOn(e, group, lb.name)),
      ondblclick: () => lb.editable && go("logbook", lb.name) },
      mv,
      isSuper() ? h("input", { type: "checkbox", checked: S.selected.has(lb.name), title: "Select for bulk edit",
        onclick: (e) => { e.stopPropagation(); e.target.checked ? S.selected.add(lb.name) : S.selected.delete(lb.name); render(); } }) : null,
      h("a", { class: "name", href: lb.editable ? "#/logbook/" + encodeURIComponent(lb.name) : null }, lb.name),
      h("span", { class: "comment", title: getItem(lb.items, "Comment") || "" }, getItem(lb.items, "Comment") || ""),
      iss.length ? h("span", { class: "badge err", title: iss.map((x) => x.message).join("\n") }, iss.length + " issue" + (iss.length > 1 ? "s" : "")) : null,
      login.length ? h("span", { class: "badge lock", title: "Restricted to: " + login.join(", ") }, "🔒 " + login.length)
        : h("span", { class: "badge", title: "No 'Login user': every registered user can access it" }, "open"));
  };

  // parent: name of the containing group (null at top level); i/n: position among siblings
  const groupCard = (g, parent, i, n, path) => {
    const collapsed = S.collapsed.has(lc(g.name));
    const nLogbooks = g.members.filter((m) => !isGroup(m)).length;
    const nSub = g.members.length - nLogbooks;
    const up = parent ? () => moveWithin(parent, i, -1) : () => moveTop(g.name, -1);
    const down = parent ? () => moveWithin(parent, i, 1) : () => moveTop(g.name, 1);
    const sub = path.concat(lc(g.name));
    return h("div", { class: "group" + (parent ? " nested" : ""), ...dropZone((e) => dropOn(e, g.name, null)) },
      h("div", { class: "ghead", draggable: canDrag, title: canDrag ? "Drag into another group to nest it" : null,
        ondragstart: (e) => { e.stopPropagation(); dragStart(e, g.name, parent); } },
        h("button", { class: "link twisty", title: collapsed ? "Expand" : "Collapse",
          onclick: () => { collapsed ? S.collapsed.delete(lc(g.name)) : S.collapsed.add(lc(g.name)); render(); } }, collapsed ? "▸" : "▾"),
        h("h3", {}, g.name, g.top ? h("span", { class: "badge", title: "Top group" }, " top") : null),
        h("span", { class: "badge", title: `${nLogbooks} logbooks, ${nSub} subgroups` }, nSub ? `${nLogbooks} + ${nSub} sub` : nLogbooks),
        arrows(up, down, i > 0, i < n - 1, "group"),
        canDrag ? h("span", { class: "gact" },
          h("button", { class: "link", onclick: () => renameGroup(g.name) }, "Rename"),
          h("button", { class: "link", title: "Create a subgroup inside this group", onclick: () => newGroup(g.name) }, "+ Subgroup"),
          h("button", { class: "link", title: "New logbook in this group", onclick: () => newLogbook({ group: g.name }) }, "+ Logbook"),
          h("button", { class: "link", style: "color:var(--danger)", onclick: () => deleteGroup(g.name) }, "Delete")) : null),
      collapsed ? null : h("div", { class: "items" },
        g.members.map((m, j) => {
          const sg = asGroup(groups, m);
          if (!sg) return lbRow(m, g.name, j, g.members.length);
          if (sub.includes(lc(sg.name))) return h("div", { class: "lb missing" }, h("span", { class: "name" }, m), h("span", { class: "comment" }, "group contains itself"));
          return groupCard(sg, g.name, j, g.members.length, sub);
        }),
        !g.members.length ? h("div", { class: "muted", style: "padding:6px" }, canDrag ? "Empty: drag logbooks or groups here" : "Empty") : null));
  };

  const ungrouped = S.state.logbooks.filter((l) => !grouped.has(lc(l.name)));
  const dirty = S.groupsDraft && JSON.stringify(S.groupsDraft) !== JSON.stringify(S.state.groups);

  return h("div", {},
    h("div", { class: "toolbar" },
      h("h2", { style: "margin:0" }, "Logbooks"),
      h("span", { class: "muted" }, `${S.state.logbooks.length} logbooks · ${groups.length} groups`),
      h("span", { class: "spacer" }),
      h("input", { type: "search", placeholder: "Filter by name, comment or user…", value: S.filter, style: "width:260px",
        oninput: (e) => { S.filter = e.target.value; const pos = e.target.selectionStart; render();
          const inp = $("#app input[type=search]"); inp.focus(); inp.setSelectionRange(pos, pos); } }),
      h("button", { title: "Collapse or expand all groups", onclick: () => {
        S.collapsed = S.collapsed.size ? new Set() : new Set(groups.map((g) => lc(g.name))); render(); } }, S.collapsed.size ? "Expand all" : "Collapse all"),
      isSuper() ? [
        S.selected.size ? h("button", { onclick: bulkEdit }, `Bulk edit (${S.selected.size})`) : null,
        S.selected.size ? h("button", { class: "link", onclick: () => { S.selected.clear(); render(); } }, "clear") : null,
        h("button", { onclick: () => newGroup(null) }, "New group"),
        h("button", { class: "primary", onclick: () => newLogbook({}) }, "New logbook"),
      ] : null),
    dirty ? h("div", { class: "pending" }, h("b", {}, "Unsaved group layout."),
      h("span", { class: "spacer" }),
      h("button", { onclick: () => { S.groupsDraft = null; render(); } }, "Discard"),
      h("button", { class: "primary", onclick: saveGroups }, "Review & save")) : null,
    canDrag ? h("p", { class: "muted", style: "margin-top:0" },
      "Order here is the order on ELOG's start page. Use ↑ ↓ to reorder, or drag: drop on a logbook to place before it, ",
      "on a group to move into it (Ctrl copies; a logbook may be in several groups). Drag a group by its title to nest it.") : null,
    h("div", { class: "board" },
      tops.map((g, i) => groupCard(g, null, i, tops.length, [])),
      ungrouped.length || canDrag ? h("div", { class: "group ungrouped", ...dropZone((e) => dropOn(e, null, null)) },
        h("div", { class: "ghead" }, h("h3", {}, "Not in any group"), h("span", { class: "badge" }, ungrouped.length)),
        h("div", { class: "items" }, ungrouped.map((l) => lbRow(l.name, null, 0, 0)),
          canDrag ? h("div", { class: "muted", style: "padding:6px" }, "Drop a logbook here to remove it from its group, or a subgroup to make it top-level.") : null)) : null));
}

let DRAG = null;
function dragStart(e, name, from) {
  DRAG = { name, from };
  e.dataTransfer.effectAllowed = "copyMove";
  e.dataTransfer.setData("text/plain", name);
  e.target.classList.add("dragging");
}
function dropOn(e, toGroup, beforeName) {
  e.preventDefault();
  if (!DRAG) return;
  const copy = e.ctrlKey || e.metaKey;
  moveMember(DRAG.name, DRAG.from, toGroup, beforeName, copy);
  DRAG = null;
}
function moveMember(name, from, to, before, copy) {
  const groups = clone(draftGroups());
  if (to && asGroup(groups, name) && containsGroup(groups, name, to))
    return toast(`'${to}' is inside '${name}'; a group cannot contain itself`, true);
  if (before && lc(before) === lc(name)) return;
  if (from && !copy) {
    const g = groupByName(groups, from);
    g.members = g.members.filter((m) => lc(m) !== lc(name));
  }
  if (to) {
    const g = groupByName(groups, to);
    g.members = g.members.filter((m) => lc(m) !== lc(name));
    const idx = before ? g.members.findIndex((m) => lc(m) === lc(before)) : -1;
    if (idx >= 0) g.members.splice(idx, 0, name); else g.members.push(name);
  }
  S.groupsDraft = groups;
  render();
}
/** Swap a member (logbook or subgroup) with its neighbour inside a group. */
function moveWithin(group, i, d) {
  const groups = clone(draftGroups());
  const m = groupByName(groups, group).members;
  if (i + d < 0 || i + d >= m.length) return;
  [m[i], m[i + d]] = [m[i + d], m[i]];
  S.groupsDraft = groups;
  render();
}
/** Swap a top-level group with the previous/next top-level group (their order in [global]). */
function moveTop(name, d) {
  const groups = clone(draftGroups());
  const tops = topGroups(groups).map((g) => lc(g.name));
  const t = tops.indexOf(lc(name));
  if (t + d < 0 || t + d >= tops.length) return;
  const a = groups.findIndex((g) => lc(g.name) === tops[t]);
  const b = groups.findIndex((g) => lc(g.name) === tops[t + d]);
  [groups[a], groups[b]] = [groups[b], groups[a]];
  S.groupsDraft = groups;
  render();
}
async function newGroup(parent) {
  const inp = h("input", { placeholder: parent ? "e.g. STM Experiments" : "e.g. Experiments" });
  const ok = await modal(parent ? `New subgroup in '${parent}'` : "New group", h("label", {}, "Group name", inp),
    [{ label: "Add", validate: () => !!inp.value.trim() }]);
  if (!ok) return;
  const name = inp.value.trim();
  const groups = clone(draftGroups());
  if (groupByName(groups, name) || lbByName(name)) return toast("A group or logbook with this name exists", true);
  const g = { name, members: [], top: false };
  if (parent) {
    const p = groupByName(groups, parent);
    p.members.push(name);
    groups.splice(groups.indexOf(p) + 1, 0, g);  // keep the definition next to its parent in elogd.cfg
  } else groups.push(g);
  S.groupsDraft = groups;
  S.collapsed && S.collapsed.delete(lc(parent || ""));
  render();
  toast("Group added. Drag logbooks into it, then save.");
}
async function renameGroup(name) {
  const inp = h("input", { value: name });
  const ok = await modal(`Rename group '${name}'`, h("label", {}, "New name", inp), [{ label: "Rename" }]);
  if (!ok || !inp.value.trim() || inp.value.trim() === name) return;
  const groups = clone(draftGroups());
  for (const g of groups) {
    if (g.name === name) g.name = inp.value.trim();
    g.members = g.members.map((m) => (lc(m) === lc(name) ? inp.value.trim() : m));
  }
  S.groupsDraft = groups;
  render();
}
async function deleteGroup(name) {
  const g = groupByName(draftGroups(), name);
  const subs = g.members.filter((m) => asGroup(draftGroups(), m));
  const ok = await modal(`Delete group '${name}'?`, h("p", {}, "The logbooks stay; only the grouping is removed.",
    subs.length ? ` Its subgroups (${subs.join(", ")}) move up into its place.` : ""),
    [{ label: "Delete group", class: "danger" }]);
  if (!ok) return;
  const groups = clone(draftGroups()).filter((x) => x.name !== name);
  for (const p of groups) {
    const i = p.members.findIndex((m) => lc(m) === lc(name));
    if (i >= 0) p.members.splice(i, 1, ...subs.filter((s) => !p.members.some((m) => lc(m) === lc(s))));
  }
  S.groupsDraft = groups;
  render();
}
async function saveGroups() {
  if (await commit([{ op: "set_groups", groups: S.groupsDraft }], "Update logbook groups")) S.groupsDraft = null;
  render();
}

function suggestName(group) {
  const g = group && groupByName(S.state.groups, group);
  if (!g) return "";
  let best = null;
  for (const m of g.members) {
    const mm = /^(.*?)(\d+)$/.exec(m);
    if (mm && (!best || +mm[2] > +best[2])) best = mm;
  }
  if (!best) return "";
  return best[1] + String(+best[2] + 1).padStart(best[2].length, "0");
}

async function newLogbook({ group, template }) {
  const groups = S.state.groups;
  const st = { group: group || "", template: template || "", login: [], admin: [] };
  const lastIn = (gname) => {
    const g = groupByName(groups, gname || "");
    const ex = g ? g.members.filter((m) => lbByName(m)) : [];
    return ex.length ? ex[ex.length - 1] : "";
  };
  if (!st.template) st.template = lastIn(st.group);
  const nameInp = h("input", { value: template ? template + "-copy" : suggestName(st.group), placeholder: "e.g. np011" });
  const commentInp = h("input", { placeholder: "Short description shown on the main page" });
  const accessBox = h("div", {});
  const fillAccess = () => {
    const t = st.template && lbByName(st.template);
    st.login = t ? splitList(getItem(t.items, "Login user")) : [];
    st.admin = t ? splitList(getItem(t.items, "Admin user")) : [];
    accessBox.replaceChildren(
      h("label", {}, h("span", {}, "Login users ", h("span", { class: "hint" }, "empty = every registered user")),
        chipInput(st.login, userNames(), (v) => (st.login = v), { validate: S.state.users.length > 0 })),
      h("label", { style: "margin-top:10px" }, "Admin users",
        chipInput(st.admin, userNames(), (v) => (st.admin = v), { validate: S.state.users.length > 0 })));
  };
  const tplSel = h("select", { onchange: (e) => { st.template = e.target.value; fillAccess(); } },
    h("option", { value: "" }, "(empty logbook)"),
    S.state.logbooks.map((l) => h("option", { value: l.name, selected: l.name === st.template }, l.name)));
  const grpSel = h("select", { onchange: (e) => {
    st.group = e.target.value;
    const s = suggestName(st.group); if (s && !template) nameInp.value = s;
    st.template = template || lastIn(st.group); tplSel.value = st.template; fillAccess();
  } }, h("option", { value: "" }, "(no group)"), groups.map((g) => h("option", { value: g.name, selected: g.name === st.group }, g.name)));
  fillAccess();
  const body = h("div", { class: "grid2" },
    h("label", {}, "Name", nameInp),
    h("label", {}, "Group", grpSel),
    h("label", {}, h("span", {}, "Copy settings from ", h("span", { class: "hint" }, "attributes, options, display…")), tplSel),
    h("label", {}, "Comment", commentInp),
    h("div", { style: "grid-column:1/-1" }, accessBox));
  const ok = await modal(template ? `Clone '${template}'` : "New logbook", body, [{
    label: "Preview", validate: () => {
      if (!nameInp.value.trim()) { toast("Name is required", true); return false; }
      if (lbByName(nameInp.value.trim())) { toast("A logbook with this name exists", true); return false; }
      return true;
    },
  }]);
  if (!ok) return;
  const name = nameInp.value.trim();
  const set = { "Login user": st.login.length ? st.login.join(", ") : null, "Admin user": st.admin.length ? st.admin.join(", ") : null };
  if (await commit([{ op: "create_logbook", name, template: st.template || null, group: st.group || null,
    comment: commentInp.value, set }], `Create logbook '${name}'`)) go("logbook", name);
}

async function bulkEdit() {
  const names = [...S.selected];
  const keys = [...new Set(S.state.logbooks.flatMap((l) => l.items.map(([k]) => k)))].sort();
  const opSel = h("select", {},
    h("option", { value: "list_add" }, "Add to list (e.g. a user to Login user)"),
    h("option", { value: "list_remove" }, "Remove from list"),
    h("option", { value: "set" }, "Set value"),
    h("option", { value: "delete_key" }, "Delete setting"));
  const keyInp = h("input", { list: "bulk-keys", value: "Login user" });
  const valInp = h("input", { placeholder: "value or comma-separated items" });
  const body = h("div", {},
    h("p", {}, "Applies to: ", h("b", {}, names.join(", "))),
    h("div", { class: "grid2" }, h("label", {}, "Operation", opSel), h("label", {}, "Setting", keyInp),
      h("label", { style: "grid-column:1/-1" }, "Value", valInp)),
    h("datalist", { id: "bulk-keys" }, keys.map((k) => h("option", { value: k }))));
  const ok = await modal(`Bulk edit ${names.length} logbooks`, body, [{ label: "Preview", validate: () => !!keyInp.value.trim() }]);
  if (!ok) return;
  const op = opSel.value, key = keyInp.value.trim();
  const ops = names.map((section) => op === "set" ? { op, section, key, value: valInp.value }
    : op === "delete_key" ? { op, section, key } : { op, section, key, items: splitList(valInp.value) });
  if (await commit(ops, `Bulk: ${op} '${key}'`)) { S.selected.clear(); render(); }
}

// ============================================================ LOGBOOK EDITOR
const ATTR_PREFIXES = ["Preset on first reply", "Preset on reply", "Preset on edit", "Preset on duplicate", "Preset",
  "Subst on reply", "Subst on edit", "Subst on duplicate", "Subst", "Options", "MOptions", "ROptions", "IOptions",
  "Comment", "Tooltip", "Type", "Format", "Style", "Icon comment"];
const ATTR_LIST_KEYS = ["Required Attributes", "Locked Attributes", "Extendable options", "Fixed Attributes Reply",
  "Fixed Attributes Edit", "Propagate Attributes", "Quick filter", "List display", "Show Attributes",
  "Show Attributes Edit", "Show attributes reply", "Hidden attributes", "Sort Attributes"];
const OPT_KINDS = ["Options", "MOptions", "ROptions", "IOptions"];
const TYPES = ["", "numeric", "date", "datetime", "userlist", "muserlist", "useremail", "museremail"];
const BUILTIN = ["ID", "Date", "Subtext", "Text", "Attachments", "Edit", "Delete"];

function splitCond(key) { const m = /^\s*\{[^}]*\}\s*/.exec(key); return m ? [m[0], key.slice(m[0].length)] : ["", key]; }
function attrTarget(key) {
  const [cond, bare] = splitCond(key);
  const nk = norm(bare);
  for (const p of ATTR_PREFIXES) if (nk.startsWith(p.toLowerCase() + " "))
    return { cond, prefix: p, target: bare.trim().split(/\s+/).join(" ").slice(p.length + 1) };
  return null;
}

class Editor {
  constructor(lb) {
    this.name = lb.name;
    this.orig = clone(lb.items);
    this.items = clone(lb.items);
    this.groups = [...lb.groups];
    this.origGroups = [...lb.groups];
  }
  get(key) { return getItem(this.items, key); }
  set(key, value) {
    const i = this.items.findIndex(([k]) => norm(k) === norm(key));
    if (value === null || value === undefined || value === "") { if (i >= 0) this.items.splice(i, 1); return; }
    if (i >= 0) this.items[i][1] = value; else this.items.push([key, value]);
  }
  list(key) { return splitList(this.get(key)); }
  setList(key, arr) { this.set(key, arr.join(", ")); }
  attrs() {
    return this.list("Attributes").map((name) => {
      const a = { name, orig: name, optKind: "", options: "", type: "", preset: "", comment: "" };
      for (const [k, v] of this.items) {
        const t = attrTarget(k);
        if (!t || t.cond || t.target.toLowerCase() !== name.toLowerCase()) continue;
        if (OPT_KINDS.includes(t.prefix)) { a.optKind = t.prefix; a.options = v; }
        if (t.prefix === "Type") a.type = v;
        if (t.prefix === "Preset") a.preset = v;
        if (t.prefix === "Comment") a.comment = v;
      }
      const inL = (key) => this.list(key).some((x) => x.toLowerCase() === name.toLowerCase());
      a.required = inL("Required Attributes"); a.locked = inL("Locked Attributes"); a.extendable = inL("Extendable options");
      return a;
    });
  }
  renameAttr(oldN, newN) {
    const lo = oldN.toLowerCase();
    const re = new RegExp("\\$" + esc(oldN) + "(?![\\w])", "gi");
    this.items = this.items.map(([k, v]) => {
      const t = attrTarget(k);
      if (t && (t.target.toLowerCase() === lo || (t.prefix === "Style" && t.target.toLowerCase().startsWith(lo + " "))))
        k = t.cond + t.prefix + " " + newN + t.target.slice(oldN.length);
      if (ATTR_LIST_KEYS.concat(["Attributes"]).some((x) => norm(x) === norm(splitCond(k)[1])))
        v = splitList(v).map((x) => (x.toLowerCase() === lo ? newN : x)).join(", ");
      return [k, v.replace(re, "$" + newN)];
    });
  }
  removeAttr(name) {
    const lo = name.toLowerCase();
    this.items = this.items.filter(([k]) => { const t = attrTarget(k); return !(t && !t.cond && t.target.toLowerCase() === lo); });
    for (const key of ATTR_LIST_KEYS.concat(["Attributes"])) {
      if (this.get(key) === undefined) continue;
      this.setList(key, this.list(key).filter((x) => x.toLowerCase() !== lo));
    }
  }
  setAttrProp(a, prop, value) {
    const perKey = (prefix, val) => {
      this.items = this.items.filter(([k]) => { const t = attrTarget(k); return !(t && !t.cond && t.prefix === prefix && t.target.toLowerCase() === a.name.toLowerCase()); });
      if (val) this.items.push([prefix + " " + a.name, val]);
    };
    const flag = (key, on) => {
      let l = this.list(key).filter((x) => x.toLowerCase() !== a.name.toLowerCase());
      if (on) l.push(a.name);
      this.setList(key, l);
    };
    if (prop === "options" || prop === "optKind") {
      for (const p of OPT_KINDS) perKey(p, "");
      a[prop] = value;
      if (a.options && a.optKind === "") a.optKind = "Options";
      if (a.optKind && a.options) perKey(a.optKind, a.options);
    } else if (prop === "type") perKey("Type", value);
    else if (prop === "preset") perKey("Preset", value);
    else if (prop === "comment") perKey("Comment", value);
    else if (prop === "required") flag("Required Attributes", value);
    else if (prop === "locked") flag("Locked Attributes", value);
    else if (prop === "extendable") flag("Extendable options", value);
  }
  ops() {
    const ops = [];
    const om = new Map(this.orig.map(([k, v]) => [norm(k), [k, v]]));
    const cm = new Map(this.items.map(([k, v]) => [norm(k), [k, v]]));
    for (const [nk, [k]] of om) if (!cm.has(nk)) ops.push({ op: "delete_key", section: this.name, key: k });
    let prev = null;
    for (const [k, v] of this.items) {
      const o = om.get(norm(k));
      if (!o) ops.push({ op: "set", section: this.name, key: k, value: v, after: prev });
      else if (o[1] !== v) ops.push({ op: "set", section: this.name, key: o[0], value: v });
      prev = k;
    }
    return ops;
  }
  groupsChanged() { return JSON.stringify([...this.groups].sort()) !== JSON.stringify([...this.origGroups].sort()); }
  dirty() { return this.ops().length > 0 || this.groupsChanged(); }
}

function viewLogbook(r) {
  const lb = lbByName(r.arg || "");
  if (!lb) return h("div", { class: "card" }, "No such logbook. ", h("a", { href: "#/logbooks" }, "Back"));
  if (!S.editor || S.editor.name !== lb.name || S.editorEtag !== S.state.etag) {
    S.editor = new Editor(lb); S.editorEtag = S.state.etag;
  }
  const E = S.editor;
  const tab = r.tab || "overview";
  const rerender = () => render();
  const tabs = [["overview", "Overview"], ["access", "Access"], ["attributes", "Attributes"], ["all", "All settings"], ["raw", "Raw text"]];
  const issues = S.state.issues.filter((i) => i.section.toLowerCase() === lb.name.toLowerCase());
  const elogUrl = S.state.elog_url ? S.state.elog_url.replace(/\/$/, "") + "/" + encodeURIComponent(lb.name) + "/" : null;

  const save = async () => {
    const ops = E.ops();
    if (E.groupsChanged()) {
      const groups = clone(S.state.groups);
      for (const g of groups) {
        const inG = E.groups.includes(g.name);
        const has = g.members.some((m) => m.toLowerCase() === lb.name.toLowerCase());
        if (inG && !has) g.members.push(lb.name);
        if (!inG && has) g.members = g.members.filter((m) => m.toLowerCase() !== lb.name.toLowerCase());
      }
      ops.push({ op: "set_groups", groups });
    }
    if (await commit(ops, `Edit logbook '${lb.name}'`)) S.editor = null;
  };

  let content;
  if (tab === "overview") content = tabOverview(E, rerender);
  else if (tab === "access") content = tabAccess(E, rerender);
  else if (tab === "attributes") content = tabAttributes(E, rerender);
  else if (tab === "all") content = kvTable(E, rerender, globalVal);
  else content = tabRaw(lb);

  return h("div", {},
    h("div", { class: "toolbar" },
      h("a", { href: "#/logbooks" }, "← Logbooks"),
      h("h2", { style: "margin:0" }, lb.name),
      lb.groups.map((g) => h("span", { class: "badge" }, g)),
      h("span", { class: "spacer" }),
      elogUrl ? h("a", { href: elogUrl, target: "_blank", rel: "noopener" }, "Open in ELOG ↗") : null,
      isSuper() ? [
        h("button", { onclick: () => newLogbook({ template: lb.name, group: lb.groups[0] }) }, "Clone"),
        h("button", { onclick: () => renameLogbook(lb.name) }, "Rename"),
        h("button", { class: "danger", onclick: () => deleteLogbook(lb.name) }, "Delete"),
      ] : null),
    issues.length ? h("div", { style: "margin-bottom:12px" }, issueList(issues)) : null,
    E.dirty() && tab !== "raw" ? h("div", { class: "pending" }, h("b", {}, `${E.ops().length + (E.groupsChanged() ? 1 : 0)} unsaved change(s)`),
      h("span", { class: "spacer" }),
      h("button", { onclick: () => { S.editor = null; render(); } }, "Discard"),
      h("button", { class: "primary", onclick: save }, "Review & save")) : null,
    h("div", { class: "tabs" }, tabs.map(([id, label]) => h("button", { class: id === tab ? "active" : "",
      onclick: () => go("logbook", lb.name, id) }, label))),
    content);
}

function field(E, key, label, hint, rerender, opts = {}) {
  const g = globalVal(key);
  const inp = h(opts.textarea ? "textarea" : "input", { value: E.get(key) || "", type: opts.type,
    placeholder: g !== undefined ? `inherits global: ${g}` : opts.placeholder || "",
    onchange: (e) => { E.set(key, e.target.value.trim()); rerender(); } });
  return h("label", {}, h("span", {}, label, " ", hint ? h("span", { class: "hint" }, hint) : null), inp);
}
function triState(E, key, label, rerender) {
  const v = E.get(key);
  const g = globalVal(key);
  return h("label", {}, label, h("select", { onchange: (e) => { E.set(key, e.target.value); rerender(); } },
    h("option", { value: "", selected: v === undefined }, `inherit${g !== undefined ? ` (global: ${g === "1" ? "yes" : "no"})` : ""}`),
    h("option", { value: "1", selected: v === "1" }, "yes"),
    h("option", { value: "0", selected: v === "0" }, "no")));
}

function tabOverview(E, rerender) {
  const attrNames = E.list("Attributes");
  const listChips = (key, label, hint) => h("label", { style: "grid-column:1/-1" },
    h("span", {}, label, " ", h("span", { class: "hint" }, hint)),
    chipInput(E.list(key), BUILTIN.concat(attrNames), (v) => { E.setList(key, v); rerender(); }));
  return h("div", { class: "card" },
    h("div", { class: "grid2" },
      h("div", { style: "grid-column:1/-1" }, field(E, "Comment", "Comment", "shown on the logbook selection page", rerender)),
      field(E, "Page title", "Page title", "e.g. ELOG - $subject", rerender),
      field(E, "Thread display", "Thread display", "$Subject ($Category)…", rerender),
      field(E, "Entries per page", "Entries per page", "", rerender, { type: "number" }),
      field(E, "Refresh", "Auto refresh", "seconds, 0 = off", rerender, { type: "number" }),
      field(E, "Restrict edit time", "Restrict edit time", "hours after which entries cannot be edited", rerender, { type: "number" }),
      triState(E, "Reverse sort", "Newest entries first", rerender),
      triState(E, "Allow HTML", "Allow HTML in entries", rerender),
      field(E, "Subdir", "Data subdirectory", "default: the logbook name", rerender, { placeholder: "(logbook name)" }),
      listChips("Quick filter", "Quick filter", "filter boxes above the entry list"),
      listChips("List display", "List display", "columns of the entry list; empty = all attributes"),
      isSuper() ? h("div", { style: "grid-column:1/-1" }, h("b", {}, "Groups"), h("div", { class: "row", style: "margin-top:6px" },
        S.state.groups.map((g) => h("label", { class: "inline" }, h("input", { type: "checkbox", checked: E.groups.includes(g.name),
          onchange: (e) => { E.groups = e.target.checked ? [...E.groups, g.name] : E.groups.filter((x) => x !== g.name); rerender(); } }), g.name)))) : null));
}

function tabAccess(E, rerender) {
  const login = E.list("Login user"), admin = E.list("Admin user");
  const validate = S.state.users.length > 0;
  return h("div", { class: "card" },
    h("label", {}, h("span", {}, "Login users ", h("span", { class: "hint" }, "who may read and write this logbook")),
      chipInput(login, userNames(), (v) => { E.setList("Login user", v); rerender(); }, { validate })),
    h("p", { class: login.length ? "muted" : "", style: login.length ? "" : "color:var(--warn)" }, login.length
      ? `Only these ${login.length} users (plus admins) can open the logbook.`
      : "No list set: every user in the password file can open this logbook."),
    h("label", { style: "margin-top:12px" }, h("span", {}, "Admin users ", h("span", { class: "hint" }, "may change this logbook's config here and in ELOG")),
      chipInput(admin, userNames(), (v) => { E.setList("Admin user", v); rerender(); }, { validate })),
    h("div", { class: "grid2", style: "margin-top:16px" },
      triState(E, "Restrict edit", "Users can only edit their own entries", rerender),
      field(E, "Allow Delete", "Allow Delete", "users allowed to delete entries", rerender),
      field(E, "Guest menu commands", "Guest menu commands", "set to allow anonymous read access", rerender, { placeholder: "(no guest access)" })),
    validate ? null : h("p", { class: "muted" }, "Password file not readable: user names are not validated."));
}

function tabAttributes(E, rerender) {
  const attrs = E.attrs();
  const row = (a, i) => h("tr", {},
    h("td", {}, h("input", { value: a.name, onchange: (e) => {
      const n = e.target.value.trim(); if (!n || n === a.name) return;
      if (attrs.some((x) => x.name.toLowerCase() === n.toLowerCase())) { toast("Attribute exists", true); return rerender(); }
      E.renameAttr(a.name, n); rerender(); } })),
    h("td", {}, h("select", { onchange: (e) => { E.setAttrProp(a, "optKind", e.target.value); rerender(); } },
      [["", "free text"], ["Options", "drop-down"], ["MOptions", "checkboxes (multi)"], ["ROptions", "radio buttons"], ["IOptions", "icons"]]
        .map(([v, l]) => h("option", { value: v, selected: a.optKind === v }, l)))),
    h("td", { class: "wide" }, h("input", { value: a.options, title: a.options, placeholder: a.optKind ? "A, B, C" : "", onchange: (e) => { E.setAttrProp(a, "options", e.target.value.trim()); rerender(); } })),
    h("td", {}, h("select", { onchange: (e) => { E.setAttrProp(a, "type", e.target.value); rerender(); } },
      TYPES.map((t) => h("option", { value: t, selected: a.type.toLowerCase() === t }, t || "text")))),
    ["required", "locked", "extendable"].map((p) => h("td", { class: "c" }, h("input", { type: "checkbox", checked: a[p],
      disabled: p === "extendable" && !a.optKind, onchange: (e) => { E.setAttrProp(a, p, e.target.checked); rerender(); } }))),
    h("td", {}, h("input", { value: a.preset, placeholder: "", onchange: (e) => { E.setAttrProp(a, "preset", e.target.value.trim()); rerender(); } })),
    h("td", {}, h("input", { value: a.comment, onchange: (e) => { E.setAttrProp(a, "comment", e.target.value.trim()); rerender(); } })),
    h("td", { style: "white-space:nowrap" },
      h("button", { class: "link", disabled: i === 0, title: "Move up", onclick: () => { const l = E.list("Attributes"); [l[i - 1], l[i]] = [l[i], l[i - 1]]; E.setList("Attributes", l); rerender(); } }, "↑"), " ",
      h("button", { class: "link", style: "color:var(--danger)", onclick: () => { E.removeAttr(a.name); rerender(); } }, "Remove")));
  const newInp = h("input", { placeholder: "New attribute name" });
  return h("div", { class: "card", style: "overflow-x:auto" },
    h("table", {},
      h("thead", {}, h("tr", {}, ["Name", "Input", "Options", "Type"].map((t) => h("th", {}, t)),
        ["Required", "Locked", "Extendable"].map((t) => h("th", { class: "c" }, t)),
        h("th", {}, "Preset"), h("th", {}, "Comment"), h("th", {}))),
      h("tbody", {}, attrs.map(row))),
    h("div", { class: "row", style: "margin-top:12px" }, newInp, h("button", { onclick: () => {
      const n = newInp.value.trim(); if (!n) return;
      if (attrs.some((x) => x.name.toLowerCase() === n.toLowerCase())) return toast("Attribute exists", true);
      const l = E.list("Attributes"); const si = l.findIndex((x) => x.toLowerCase() === "subject");
      si >= 0 ? l.splice(si, 0, n) : l.push(n); E.setList("Attributes", l); rerender(); } }, "Add attribute")),
    h("p", { class: "hint" }, "Renaming updates every setting that refers to the attribute, including $name in titles. ",
      "Conditional settings like {1} Subst SN are kept and can be edited under All settings. ",
      "Extendable lets users add new option values when writing an entry."));
}

function kvTable(E, rerender, inherit) {
  const origMap = new Map(E.orig.map(([k, v]) => [norm(k), v]));
  const keyInp = h("input", { placeholder: "Setting name", list: "kv-keys" });
  const valInp = h("input", { placeholder: "Value" });
  const allKeys = [...new Set(S.state.logbooks.flatMap((l) => l.items.map(([k]) => k)).concat(S.state.global.map(([k]) => k)))].sort();
  return h("div", { class: "card" },
    h("table", { class: "kv" }, h("tbody", {}, E.items.map(([k, v], i) => {
      if (E.hideKey && E.hideKey(k)) return null;
      const changed = origMap.get(norm(k)) !== v;
      return h("tr", { class: changed ? "changed" : "" },
        h("td", {}, h("input", { value: k, onchange: (e) => { const nk = e.target.value.trim(); if (nk) E.items[i][0] = nk; rerender(); } })),
        h("td", {}, h("input", { value: v, onchange: (e) => { E.items[i][1] = e.target.value.trim(); rerender(); } })),
        h("td", { style: "width:1%" }, h("button", { class: "link", style: "color:var(--danger)", title: "Delete setting",
          onclick: () => { E.items.splice(i, 1); rerender(); } }, "×")));
    }))),
    h("div", { class: "row", style: "margin-top:12px" }, keyInp, valInp, h("button", { onclick: () => {
      const k = keyInp.value.trim(); if (!k) return;
      if (E.get(k) !== undefined) return toast("Setting already exists; edit it above", true);
      E.items.push([k, valInp.value.trim()]); rerender(); } }, "Add setting"),
      inherit ? h("span", { class: "hint" }, "Settings not listed here inherit from [global].") : null),
    h("datalist", { id: "kv-keys" }, allKeys.map((k) => h("option", { value: k }))),
    h("p", { class: "hint" }, "Reference: ", h("a", { href: "https://elog.psi.ch/elog/config.html", target: "_blank", rel: "noopener" }, "elogd.cfg options")));
}

function tabRaw(lb) {
  const ta = h("textarea", { class: "code", spellcheck: "false" }, "Loading…");
  api("GET", apiUrl("/api/section/" + encodeURIComponent(lb.name))).then((d) => (ta.value = d.text));
  return h("div", { class: "card" },
    h("p", { class: "muted", style: "margin-top:0" }, "Edit this logbook's section directly. Comments are kept."),
    ta,
    h("div", { class: "row", style: "margin-top:12px" }, h("span", { class: "spacer" }),
      h("button", { class: "primary", onclick: async () => {
        if (await commit([{ op: "section_text", section: lb.name, text: ta.value }], `Edit [${lb.name}] text`)) S.editor = null;
      } }, "Review & save")));
}

async function renameLogbook(name) {
  const inp = h("input", { value: name });
  const keep = h("input", { type: "checkbox", checked: true });
  const lb = lbByName(name);
  const hasSubdir = getItem(lb.items, "Subdir");
  const body = h("div", {}, h("label", {}, "New name", inp),
    hasSubdir ? h("p", { class: "muted" }, `Entries stay in Subdir '${hasSubdir}'.`)
      : h("label", { class: "inline", style: "margin-top:10px" }, keep,
        "Keep existing entries: add 'Subdir = " + name + "' so the data directory does not have to be renamed"),
    h("p", { class: "muted" }, "Group memberships are updated. Bookmarks to the old URL will stop working."));
  const ok = await modal(`Rename '${name}'`, body, [{ label: "Preview" }]);
  if (!ok || !inp.value.trim() || inp.value.trim() === name) return;
  const nn = inp.value.trim();
  if (await commit([{ op: "rename_logbook", name, new: nn, keep_data: keep.checked }], `Rename '${name}' → '${nn}'`)) go("logbook", nn);
}

async function deleteLogbook(name) {
  const inp = h("input", { placeholder: name });
  const body = h("div", {},
    h("p", {}, "Removes the logbook from elogd.cfg and from all groups. ",
      h("b", {}, "Entries on disk are not deleted"), "; re-adding a section with the same name brings them back."),
    h("label", {}, `Type '${name}' to confirm`, inp));
  const ok = await modal(`Delete logbook '${name}'?`, body, [{ label: "Preview", class: "danger",
    validate: () => inp.value === name || (toast("Name does not match", true), false) }]);
  if (!ok) return;
  if (await commit([{ op: "delete_logbook", name }], `Delete logbook '${name}'`)) go("logbooks");
}

// ============================================================ USERS
function viewUsers(r) {
  const users = S.state.users;
  const known = new Set(users.map((u) => u.name.toLowerCase()));
  const refs = {};  // name -> {login:[], admin:[]}
  for (const lb of S.state.logbooks) for (const [key, kind] of [["Login user", "login"], ["Admin user", "admin"]])
    for (const u of splitList(getItem(lb.items, key))) ((refs[u] ||= { login: [], admin: [] })[kind]).push(lb.name);
  const unknown = Object.keys(refs).filter((u) => !known.has(u.toLowerCase()) || !users.some((x) => x.name === u));
  const sel = r.arg || S.userSel;
  if (sel !== S.userSel) { S.userSel = sel; S.userPending = {}; }
  const q = (S.userFilter || "").toLowerCase();
  const pend = S.userPending;
  const pendCount = Object.keys(pend).length;
  const countOf = (n) => refs[n] ? new Set(refs[n].login.concat(refs[n].admin)).size : 0;

  const list = h("div", { class: "card userlist" },
    h("input", { type: "search", placeholder: "Find user…", value: S.userFilter || "", style: "width:100%;margin-bottom:6px",
      oninput: (e) => { S.userFilter = e.target.value; render(); const i = $(".userlist input"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }),
    unknown.length ? h("div", { class: "muted", style: "cursor:default" }, "Not in password file") : null,
    unknown.filter((u) => !q || u.toLowerCase().includes(q)).map((u) => h("div", { class: sel === u ? "sel" : "", onclick: () => go("users", u) },
      h("span", { class: "badge err" }, "?"), h("b", {}, u), h("span", { class: "fn" }, "unknown user"), h("span", { class: "badge" }, countOf(u)))),
    users.length ? h("div", { class: "muted", style: "cursor:default" }, "ELOG users") : null,
    users.filter((u) => !q || u.name.toLowerCase().includes(q) || u.full_name.toLowerCase().includes(q)).map((u) =>
      h("div", { class: sel === u.name ? "sel" : "", onclick: () => go("users", u.name) },
        h("b", {}, u.name), h("span", { class: "fn" }, u.full_name), u.inactive ? h("span", { class: "badge" }, "inactive") : null,
        h("span", { class: "badge" }, countOf(u.name)))));

  let detail;
  if (!sel) {
    detail = h("div", { class: "card" }, h("h2", {}, "Users & access"),
      h("p", {}, "Pick a user to see and change which logbooks they can access."),
      h("p", { class: "muted" }, "Accounts and passwords are managed in ELOG itself (Config → Change password / admin user list). ",
        "This page edits the 'Login user' and 'Admin user' lists of each logbook."),
      !users.length ? h("p", { class: "error" }, "The password file could not be read, so only names found in elogd.cfg are listed.") : null);
  } else {
    const u = users.find((x) => x.name === sel);
    const groups = S.state.groups;
    const shown = new Set();
    const rows = [];
    const lbRow = (lb) => {
      shown.add(lb.name);
      const login = splitList(getItem(lb.items, "Login user"));
      const admin = splitList(getItem(lb.items, "Admin user"));
      const open = login.length === 0;
      const cell = (kind, list) => {
        const key = `${lb.name}\u0000${kind}`;
        const cur = list.includes(sel);
        const val = key in pend ? pend[key] : cur;
        return h("td", { class: "c" }, h("input", { type: "checkbox", checked: val || (kind === "login" && open),
          disabled: !lb.editable || (kind === "login" && open),
          title: kind === "login" && open ? "Open to all users. Set Login user in the logbook to restrict it." : null,
          onchange: (e) => { if (e.target.checked === cur) delete pend[key]; else pend[key] = e.target.checked; render(); } }));
      };
      return h("tr", { class: Object.keys(pend).some((k) => k.startsWith(lb.name + "\u0000")) ? "changed" : "" },
        h("td", {}, lb.editable ? h("a", { href: "#/logbook/" + encodeURIComponent(lb.name) + "/access" }, lb.name) : lb.name,
          open ? [" ", h("span", { class: "badge" }, "open")] : null),
        h("td", { class: "muted" }, getItem(lb.items, "Comment") || ""),
        cell("login", login), cell("admin", admin));
    };
    for (const g of groups) {
      const lbs = g.members.map(lbByName).filter(Boolean);
      if (!lbs.length) continue;
      rows.push(h("tr", { class: "grp" }, h("td", { colspan: 4 }, g.name)));
      lbs.forEach((lb) => rows.push(lbRow(lb)));
    }
    const rest = S.state.logbooks.filter((lb) => !shown.has(lb.name));
    if (rest.length) { rows.push(h("tr", { class: "grp" }, h("td", { colspan: 4 }, "Not in any group"))); rest.forEach((lb) => rows.push(lbRow(lb))); }

    detail = h("div", { class: "card" },
      h("div", { class: "toolbar" },
        h("div", {}, h("h2", { style: "margin:0" }, sel, u && u.full_name ? h("span", { class: "muted", style: "font-weight:400" }, " · " + u.full_name) : null),
          u ? h("div", { class: "muted" }, [u.email, u.inactive ? "inactive" : null, u.last_activity ? "last activity " + fmtTime(u.last_activity) : null].filter(Boolean).join(" · "))
            : h("div", { class: "error" }, "Not an account in the password file: probably a typo or a removed user.")),
        h("span", { class: "spacer" }),
        h("button", { onclick: () => copyAccess(sel) }, "Copy access from…"),
        h("button", { onclick: () => replaceUser(sel) }, u ? "Rename in config…" : "Replace with…"),
        h("button", { class: "danger", onclick: () => removeUserEverywhere(sel) }, "Remove from all")),
      pendCount ? h("div", { class: "pending" }, h("b", {}, `${pendCount} unsaved change(s)`), h("span", { class: "spacer" }),
        h("button", { onclick: () => { S.userPending = {}; render(); } }, "Discard"),
        h("button", { class: "primary", onclick: saveUserPending }, "Review & save")) : null,
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Logbook"), h("th", {}, "Description"), h("th", { class: "c" }, "Access"), h("th", { class: "c" }, "Admin"))),
        h("tbody", {}, rows)));
  }
  return h("div", { class: "split" }, list, detail);
}

function fmtTime(t) { const n = +t; return n > 1e9 ? new Date(n * 1000).toLocaleDateString() : t; }

async function saveUserPending() {
  const ops = Object.entries(S.userPending).map(([k, on]) => {
    const [section, kind] = k.split("\u0000");
    return { op: on ? "list_add" : "list_remove", section, key: kind === "login" ? "Login user" : "Admin user", items: [S.userSel] };
  });
  if (await commit(ops, `Update access for '${S.userSel}'`)) S.userPending = {};
  render();
}
function lbsReferencing(user) {
  const out = [];
  for (const lb of S.state.logbooks) for (const key of ["Login user", "Admin user"])
    if (splitList(getItem(lb.items, key)).includes(user) && lb.editable) out.push([lb.name, key]);
  return out;
}
async function copyAccess(target) {
  const sel = h("select", {}, userNames().filter((n) => n !== target).map((n) => h("option", { value: n }, n)));
  const adm = h("input", { type: "checkbox" });
  const ok = await modal(`Give '${target}' the same access as…`, h("div", {}, h("label", {}, "User", sel),
    h("label", { class: "inline", style: "margin-top:8px" }, adm, "also copy admin rights")), [{ label: "Preview" }]);
  if (!ok) return;
  const ops = lbsReferencing(sel.value).filter(([, key]) => key === "Login user" || adm.checked)
    .map(([section, key]) => ({ op: "list_add", section, key, items: [target] }));
  if (!ops.length) return toast(`'${sel.value}' has no restricted logbooks to copy`);
  await commit(ops, `Copy access ${sel.value} → ${target}`);
}
async function replaceUser(old) {
  const inp = h("input", { list: "rep-users" });
  const ok = await modal(`Replace '${old}' in all logbooks`, h("div", {}, h("label", {}, "New user name", inp),
    h("datalist", { id: "rep-users" }, userNames().map((n) => h("option", { value: n })))), [{ label: "Preview", validate: () => !!inp.value.trim() }]);
  if (!ok) return;
  const ops = lbsReferencing(old).flatMap(([section, key]) => [
    { op: "list_remove", section, key, items: [old], delete_if_empty: false },
    { op: "list_add", section, key, items: [inp.value.trim()] }]);
  if (await commit(ops, `Replace user '${old}' → '${inp.value.trim()}'`)) go("users", inp.value.trim());
}
async function removeUserEverywhere(user) {
  const refs = lbsReferencing(user);
  if (!refs.length) return toast("Nothing to remove");
  const restrictedOnly = refs.filter(([section, key]) => key === "Login user" && splitList(getItem(lbByName(section).items, key)).length === 1);
  if (restrictedOnly.length) toast(`Note: ${restrictedOnly.map((r) => r[0]).join(", ")} would become open to all users`, true);
  await commit(refs.map(([section, key]) => ({ op: "list_remove", section, key, items: [user] })), `Remove '${user}' from all logbooks`);
}

// ============================================================ ISSUES
function viewIssues() {
  const issues = S.state.issues;
  const fixable = issues.filter((i) => i.fix);
  const toOp = (f) => f.op === "rename_key" ? { op: "rename_key", section: f.section, key: f.key, new: f.new } : f;
  return h("div", {},
    h("div", { class: "toolbar" }, h("h2", { style: "margin:0" }, "Configuration issues"), h("span", { class: "spacer" }),
      fixable.length && isSuper() ? h("button", { class: "primary", onclick: () => commit(fixable.map((i) => toOp(i.fix)), "Fix all automatically fixable issues") },
        `Fix ${fixable.length} automatically`) : null),
    !issues.length ? h("div", { class: "card" }, "✓ No issues found.") : null,
    h("div", { class: "card" }, issues.map((i) => h("div", { class: "issue " + i.level },
      h("span", { class: "lvl" }, i.level),
      h("span", { class: "msg" }, lbByName(i.section) ? h("a", { href: "#/logbook/" + encodeURIComponent(i.section) + "/all" }, `[${i.section}]`)
        : h("b", {}, `[${i.section}]`), " ", i.message),
      i.fix && (isSuper() || (lbByName(i.section) || {}).editable) ? h("button", { onclick: () => commit([toOp(i.fix)], "Fix: " + i.message) }, "Fix") : null))),
    h("p", { class: "muted" }, "Checks: unknown options (typos), group references, users missing from the password file, ",
      "attributes referenced but not defined, unused option tags, malformed styles, duplicate keys/sections."));
}

// ============================================================ HISTORY
function viewHistory() {
  const box = h("div", {}, h("h2", {}, "History"), h("p", { class: "muted" }, "Loading…"));
  api("GET", apiUrl("/api/history")).then((d) => {
    box.replaceChildren(h("h2", {}, "History"),
      h("div", { class: "card", style: "margin-bottom:16px" }, h("h3", {}, "Changes made with the manager"),
        d.audit.length ? h("table", {}, h("tbody", {}, d.audit.map((a) => h("tr", {},
          h("td", { style: "white-space:nowrap" }, a.time.replace("T", " ")), h("td", {}, h("b", {}, a.user)), h("td", {}, a.action),
          h("td", { class: "muted" }, (a.detail.ops || []).map((o) => o.section || o.name || o.op).filter(Boolean).slice(0, 6).join(", ")))))) : h("p", { class: "muted" }, "No changes yet.")),
      isSuper() ? h("div", { class: "card" }, h("h3", {}, "Backups (state before each change)"),
        d.backups.length ? h("table", {}, h("tbody", {}, d.backups.map((b) => h("tr", {},
          h("td", { style: "white-space:nowrap" }, b.time ? new Date(b.time * 1000).toLocaleString() : b.id),
          h("td", {}, b.user || ""), h("td", {}, "before: " + (b.action || "")),
          h("td", { style: "white-space:nowrap" },
            h("button", { onclick: async () => { const x = await api("GET", apiUrl("/api/history/" + encodeURIComponent(b.id)));
              modal(`Differences: ${b.id} → current`, renderDiff(x.diff), []); } }, "Compare"), " ",
            h("button", { onclick: () => commit([{ op: "restore", backup: b.id }], `Restore ${b.id}`) }, "Restore")))))) : h("p", { class: "muted" }, "No backups yet.")) : null);
  }).catch((e) => toast(e.message, true));
  return box;
}

// ============================================================ GLOBAL & RAW
function viewGlobal() {
  if (!isSuper()) return h("div", { class: "card" }, "Only manager admins can edit global settings.");
  if (!S.gEditor || S.gEditorEtag !== S.state.etag) {
    S.gEditor = new Editor({ name: "global", items: S.state.global, groups: [] });
    S.gEditor.hideKey = (k) => /^(top )?group /i.test(norm(k));
    S.gEditorEtag = S.state.etag;
  }
  const E = S.gEditor;
  const ops = E.ops();
  return h("div", {},
    h("div", { class: "toolbar" }, h("h2", { style: "margin:0" }, "Global settings"),
      h("span", { class: "muted" }, "apply to every logbook unless the logbook overrides them. Groups are edited on the Logbooks page.")),
    ops.length ? h("div", { class: "pending" }, h("b", {}, `${ops.length} unsaved change(s)`), h("span", { class: "spacer" }),
      h("button", { onclick: () => { S.gEditor = null; render(); } }, "Discard"),
      h("button", { class: "primary", onclick: async () => { if (await commit(ops, "Edit global settings")) S.gEditor = null; } }, "Review & save")) : null,
    kvTable(E, () => render(), null));
}

function viewRaw() {
  if (!isSuper()) return h("div", { class: "card" }, "Only manager admins can edit the raw file.");
  const ta = h("textarea", { class: "code", spellcheck: "false" }, "Loading…");
  let etag;
  api("GET", apiUrl("/api/raw")).then((d) => { ta.value = d.text; etag = d.etag; });
  return h("div", {},
    h("div", { class: "toolbar" }, h("h2", { style: "margin:0" }, "elogd.cfg"), h("span", { class: "spacer" }),
      h("button", { class: "primary", onclick: () => commit([{ op: "raw", text: ta.value }], "Edit raw elogd.cfg") }, "Review & save")),
    h("div", { class: "card" }, ta));
}

// ============================================================ boot
function showLogin() {
  $("#topbar").hidden = true;
  $("#app").replaceChildren();
  $("#login").hidden = false;
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#login-error").textContent = "";
  try {
    await api("POST", apiUrl("/api/login"), { user: f.user.value, password: f.password.value });
    f.password.value = "";
    await start();
  } catch (err) { $("#login-error").textContent = err.message; }
});
$("#reload").addEventListener("click", async () => {
  try {
    const r = await api("POST", apiUrl("/api/reload"));
    toast(r.ok ? "ELOG: " + r.message : r.message, !r.ok);
  } catch (e) { toast(e.message, true); }
});
$("#logout").addEventListener("click", async () => { await api("POST", apiUrl("/api/logout")); S.state = null; showLogin(); });

async function start() {
  const me = await api("GET", apiUrl("/api/me"));
  if (!me.user) return showLogin();
  if (!me.role) { showLogin(); $("#login-error").textContent = `'${me.user}' is not allowed to manage this ELOG.`; return; }
  $("#login").hidden = true;
  $("#topbar").hidden = false;
  try { await reload(); } catch (e) { toast(e.message, true); }
}
window.addEventListener("beforeunload", (e) => {
  if ((S.editor && S.editor.dirty()) || (S.groupsDraft && JSON.stringify(S.groupsDraft) !== JSON.stringify(S.state.groups))
    || Object.keys(S.userPending).length) { e.preventDefault(); e.returnValue = ""; }
});
start();
