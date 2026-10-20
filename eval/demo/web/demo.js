// The demo viewer. Every number and name on screen is a field of the recorded JSONL (served by demo_server.py) or a
// count of them; the viewer computes nothing about the game. The board is the deciding seat's own view: what its
// prompt held at that decision, nothing more.
"use strict";

const $ = (sel, el) => (el || document).querySelector(sel);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmtMs = ms => ms == null ? "" : ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s";
const KIND_LABEL = {chosen: "chosen", planned: "plan", looked: "looked up", mentioned: "mentioned", engine: "engine"};
const FILTERS = [
  ["all", "all", () => true],
  ["plays", "plays", e => e.type === "play"],
  ["combat", "combat", e => /attack|block|damage/.test(e.type || "")],
  ["tools", "tool calls", e => e.calls.some(c => c.tools.some(t => t.name !== "answer"))],
  ["plans", "plans", e => !!e.plan],
  ["model", "model asked", e => e.tier === "smart" || e.tier === "fast"],
  ["auto", "not asked", e => e.tier !== "smart" && e.tier !== "fast"],
  ["flags", "flags", e => !!(e.fallback || e.answer_invalid || e.first_failed_reply || isBreach(e))],
];

// An auto row is forced only when match_review says so; LOST and TAKEN are the harness deciding, and a tier "ai" row
// is Forge's AI choosing for the seat (the ai=0 invariant broken).
const AUTO_TEXT = {
  forced: "Forced: only one legal choice, so the harness did not ask (recorded, never hidden).",
  TAKEN: "TAKEN: the harness chose without asking although the choice was not forced (a defect the review sheet lists).",
  LOST: "LOST: the model was asked and its answer could not be used; the harness took a fixed legal option.",
  other: "Not asked: the harness recorded this choice without a model call; see the fallback reason.",
};
const isBreach = e => e.tier === "ai" || e.auto_class === "LOST" || e.auto_class === "TAKEN";
const autoLabel = e => e.tier === "ai" ? "Forge AI chose for the seat: an ai=0 invariant breach."
  : AUTO_TEXT[e.auto_class] || AUTO_TEXT.other;
const cardImg = name => "card/" + encodeURIComponent(name);
// Names whose image failed once are drawn as text tiles at once: a live re-render must not lay out an image box
// and then fall back.
const NO_IMAGE = new Set();
// Only a permanent miss (the server's X-Image-Miss 404, cacheable, so this GET is usually answered by the browser
// cache) is remembered; an offline or failed answer is asked again on the next render.
window.noImage = img => {
  const c = img.closest(".card"), name = c.getAttribute("data-name");
  c.classList.add("noimg");
  fetch(img.src).then(r => { if (r.headers.get("X-Image-Miss") === "1") NO_IMAGE.add(name); }).catch(() => {});
};

// With the audience feed, the board is drawn from engine truth (DemoFeed): every zone of both players with what Forge's
// own card panel shows (current and printed P/T, damage, counters, tapped, sick, attacking, blocking). The seat's
// counters it alone knows (mana pool, land drop) still come from its prompt; its @handles are mapped onto the cards.
function feedPlayers(d, st) {
  const f = d.feed, ids = d.feed_handles || {}, combat = f.combat || [];
  const attacking = new Set(combat.map(a => a.id)), blocking = new Set(combat.flatMap(a => a.blockers));
  const card = (c, zone) => ({
    name: c.name, handle: ids[c.id] != null ? ids[c.id] : null, pt: c.pt || null, base_pt: c.base_pt || null,
    damage: c.damage || 0, counters: c.counters || null, zone,
    tags: [c.tapped ? "tapped" : null, c.sick ? "sick" : null, attacking.has(c.id) ? "attacking" : null,
      blocking.has(c.id) ? "blocking" : null, c.token ? "token" : null, c.face_down ? "face down" : null].filter(Boolean),
    notes: [], feed: true,
  });
  return (f.players || []).map(fp => {
    const mine = fp.name === d.seat;
    const fromPrompt = ((st && st.players) || []).find(p => p.name === fp.name) || {};
    const bf = fp.battlefield || [];
    return {
      kind: mine ? "YOU" : "OPP", name: fp.name, audienceHand: !mine,
      counters: Object.assign({}, fromPrompt.counters || {}, {life: fp.life, poison: fp.poison + "/10", library: fp.library,
        graveyard: (fp.graveyard || []).length, exile: (fp.exile || []).length}),
      hand_hidden: fromPrompt.hand_hidden, lines: fromPrompt.lines || {},
      zones: {hand: (fp.hand || []).map(c => card(c, "hand")), board: bf.filter(c => !c.land).map(c => card(c, "board")),
        lands: bf.filter(c => c.land).map(c => card(c, "lands")), graveyard: (fp.graveyard || []).map(c => card(c, "graveyard")),
        exile: (fp.exile || []).map(c => card(c, "exile"))},
    };
  });
}

class Pane {
  constructor(i, info, root) {
    this.i = i; this.info = info; this.events = []; this.cur = 0; this.filter = "all"; this.done = false; this.gen = null;
    this.el = $("#pane-tpl").content.firstElementChild.cloneNode(true);
    root.appendChild(this.el);
    this.renderFilters();
  }
  get decisions() { return this.events.filter(e => e.kind === "decision"); }
  get decision() { return this.decisions[this.cur]; }
  get game() { return this.events.find(e => e.kind === "game"); }

  async load() {
    const r = await fetch(`api/game/${this.i}/events?from=${this.events.length}`).then(x => x.json());
    this.absorb(r);
    if (this.info.live) this.tail();
  }
  absorb(r) {
    if (this.gen !== null && r.gen !== this.gen) {
      // A newer game replaced this one: forget its events and its summary (header, tally, cost) too.
      this.events = []; this.cur = 0; this.gen = r.gen; this.done = false; this.info.summary = null; this.refreshSummary();
      return false;
    }
    this.gen = r.gen;
    const atEnd = this.cur >= this.decisions.length - 1;
    this.events.push(...r.events);
    this.done = r.done; this.ageMs = r.age_ms; this.waitingForFile = r.waiting_for_file; this.seenAt = Date.now();
    if (this.info.live && atEnd) this.cur = Math.max(0, this.decisions.length - 1);
    return true;
  }
  // A live pane polls for ever: after a game ends, the next game written into the same directory replaces it.
  async tail() {
    for (;;) {
      try {
        const wasDone = this.done;
        const r = await fetch(`api/game/${this.i}/events?from=${this.events.length}&wait=${this.done ? 0 : 1}`).then(x => x.json());
        const same = this.absorb(r);
        if (!same) { this.render(); continue; }
        if (r.events.length || r.done !== wasDone) { this.render(); if (r.done) this.refreshSummary(); }
        if (r.done) await new Promise(res => setTimeout(res, 2000));
      } catch (e) { await new Promise(res => setTimeout(res, 1000)); }
    }
  }
  async refreshSummary() {
    const gen = this.gen;
    const s = await fetch(`api/game/${this.i}/summary`).then(x => x.json());
    if (gen !== this.gen || (s.gen != null && s.gen !== this.gen)) return;  // a summary of another game
    this.info.summary = s;
    this.renderSummary(); this.renderHead();
  }

  stateOf(d) {
    if (!d) return null;
    const src = d.state_from == null ? null : this.events[d.state_from];
    return src ? src.state : null;
  }
  // Highlights for a decision (computed server-side by demo_highlights.py): handle -> {kind, evidence}.
  hl(d) { return (d && d.highlights) || {}; }

  render() {
    this.renderHead(); this.renderStatus(); this.renderBoard(); this.renderPlan(); this.renderTimeline();
    this.renderDetail(); this.renderSummary();
  }

  renderHead() {
    const g = this.game || {}, s = (this.info.summary || {}).game || g;
    const cond = [s.harness_epoch, s.context_profile, s.observation_version,
      s.native_tools === true ? "native tools" : s.native_tools === false ? "text tools" : null,
      s.answer_tool === true ? "answer tool" : s.answer_tool === false ? "text answers" : null,
      s.lookahead ? "simulate" : null, s.memory_tools ? "memory" : null].filter(Boolean);
    const models = [s.smart_a && `A: ${s.controller_a || ""} ${s.smart_a}`, s.smart_b && `B: ${s.controller_b || ""} ${s.controller_b === "ai" ? "(Forge AI)" : s.smart_b}`]
      .filter(Boolean).join(" · ");
    $(".pane-head", this.el).innerHTML =
      `<span class="title">${esc(s.deck_a || "A")} vs ${esc(s.deck_b || "B")}</span>` +
      `<span class="cond">seed ${esc(s.seed)} · ${esc(models)}</span>` +
      `<span class="cond">${esc(cond.join(" · "))}</span>` +
      `<span class="cond">${esc(this.info.path)}</span>`;
  }

  renderStatus() {
    const d = this.decision, st = this.stateOf(d), h = (st && st.header) || {};
    const bits = [];
    if (d) bits.push(`<span class="turn">Turn ${esc(d.turn)} · ${esc(d.phase || h.phase || "")}</span>`);
    if (h.active) bits.push(`<span>active: <b>${esc(h.active)}</b></span>`);
    if (d) bits.push(`<span>deciding: <b>${esc(d.seat)}</b> (${esc(d.type)})</span>`);
    bits.push(`<span class="pill">decision ${this.decisions.length ? this.cur + 1 : 0} / ${this.decisions.length}</span>`);
    if (this.info.live && !this.done) {
      bits.push(`<span class="pill live">LIVE</span>`);
      if (this.waitingForFile) bits.push(`<span class="pill think">waiting for the game to start</span>`);
      else {
        const think = (this.ageMs || 0) + (Date.now() - (this.seenAt || Date.now()));
        bits.push(`<span class="pill think" data-think>model thinking ${fmtMs(think)}</span>`);
      }
    }
    if (this.done && this.cur >= this.decisions.length - 1) {
      const g = this.game || {};
      bits.push(`<span class="pill done">game over: ${esc(g.winner === "A" ? g.deck_a : g.winner === "B" ? g.deck_b : g.winner)} won, T${esc(g.turns)} (${esc(g.win_condition)})</span>`);
    }
    $(".statusbar", this.el).innerHTML = bits.join("");
  }

  renderBoard() {
    const d = this.decision, st = this.stateOf(d), hl = this.hl(d);
    const el = $(".board", this.el);
    if (!st) { el.innerHTML = `<p class="engine-note">${this.waitingForFile ? "No game file yet." : "No board yet (no prompt recorded before this decision)."}</p>`; return; }
    const stale = d.state_from !== d.i ? `<p class="engine-note">forced pick (no prompt): the board shown is the seat's last prompt.</p>` : "";
    const opp = st.players.filter(p => p.kind === "OPP"), you = st.players.filter(p => p.kind === "YOU");
    this.feed = d.feed || null;
    if (this.feed) {
      const fps = feedPlayers(d, st);
      el.innerHTML = `<p class="engine-note">board: engine truth from the audience feed; the seat's own view is its prompt (below the trace)</p>` +
        fps.filter(p => p.kind === "OPP").map(p => this.playerHtml(p, hl)).join("") + this.middleHtml(st, hl) +
        fps.filter(p => p.kind === "YOU").map(p => this.playerHtml(p, hl)).join("") + this.legendHtml(hl);
      return;
    }
    el.innerHTML = stale + opp.map(p => this.playerHtml(p, hl)).join("") + this.middleHtml(st, hl) +
      you.map(p => this.playerHtml(p, hl)).join("") + this.legendHtml(hl);
  }

  playerHtml(p, hl) {
    const c = p.counters || {};
    const ctr = ["poison", "library", "graveyard", "exile", "mana_pool", "lands_played_this_turn", "spells_cast_this_turn"]
      .filter(k => c[k] != null).map(k => `${k.replace(/_/g, " ")} ${esc(c[k])}`).join(" · ");
    const zones = [];
    // With the audience feed (DEMO_FEED), the opponent's real hand: engine truth the seat never sees, drawn in a
    // style of its own and labelled so, never as the seat's knowledge.
    if (p.audienceHand) {
      const hand = p.zones.hand;
      zones.push(`<div class="zone z-hand audience"><span class="zlabel">hand</span>` +
        (hand.length ? hand.map(c => this.cardHtml(c, {})).join("") : `<span class="empty">empty</span>`) +
        `<span class="aud-note">audience view (engine truth): the seat sees only "${esc(p.hand_hidden || hand.length + " cards")}"</span></div>`);
    } else if (p.kind === "OPP" && p.hand_hidden != null) {
      const n = parseInt(p.hand_hidden, 10) || 0;
      zones.push(this.zoneHtml("hand", Array.from({length: Math.min(n, 12)}, () => `<div class="card back" title="hidden from the seat"></div>`).join("") +
        `<span class="empty">${esc(p.hand_hidden)}</span>`));
    }
    for (const z of ["hand", "board", "lands", "graveyard", "exile"]) {
      const objs = (p.zones || {})[z] || [];
      if (z === "hand" && p.kind === "OPP" && (!objs.length || p.audienceHand)) continue;
      zones.push(this.zoneHtml(z, objs.length ? objs.map(o => this.cardHtml(o, hl)).join("") : `<span class="empty">none</span>`));
    }
    const mana = (p.lines || {})["mana available"];
    return `<div class="player ${esc(p.kind)}"><div class="who"><span class="name">${p.kind === "YOU" ? "seat: " : "opponent: "}${esc(p.name)}</span>` +
      `<span class="life">${esc(c.life)} life</span><span class="ctr">${ctr}</span></div>${zones.join("")}` +
      (mana ? `<div class="mana">mana available: ${esc(mana.split(" (")[0])}</div>` : "") + `</div>`;
  }
  zoneHtml(label, inner) { return `<div class="zone z-${esc(label)}"><span class="zlabel">${esc(label)}</span>${inner}</div>`; }
  cardHtml(o, hl) {
    const h = o.handle != null ? hl[o.handle] : null;
    const tags = (o.tags || []).filter(t => t !== "untapped" && t !== "tapped");
    const tapped = (o.tags || []).includes("tapped");
    const cls = ["card", NO_IMAGE.has(o.name) ? "noimg" : "", tapped ? "tapped" : "", h ? "hl-" + h.kind : "", h && h.engine ? "hl-engine" : ""].join(" ");
    const title = [o.name, o.was != null ? `@${o.handle} (was @${o.was}: a zone change made a new object)` : o.handle != null ? `@${o.handle}` : "",
      (o.notes || []).join("; "), h ? `${KIND_LABEL[h.kind] || h.kind}: ${h.evidence || ""}` : "",
      h && h.engine && h.kind !== "engine" ? `engine: ${h.engine}` : ""].filter(Boolean).join("\n");
    // The image is Scryfall's printed card; everything the game changed (P/T, tags, counters) is in the caption
    // under it, never drawn over the card (its credit line stays visible). No image: the text tile.
    // Forge's own panel puts the current P/T over the printed box: green above the printed value, red below or damaged.
    let ptChip = "";
    if (o.pt && o.base_pt) {
      const [p, t] = o.pt.split("/").map(Number), [bp, bt] = o.base_pt.split("/").map(Number);
      const mood = o.damage || p < bp || t < bt ? "down" : p > bp || t > bt ? "up" : "";
      ptChip = `<span class="ptchip ${mood}" title="printed ${esc(o.base_pt)}${o.damage ? ", " + o.damage + " damage" : ""}">${esc(o.pt)}</span>`;
    }
    const chips = [o.damage ? `<span class="chip dmg">${esc(o.damage)} dmg</span>` : "",
      o.counters ? `<span class="chip ctr">${esc(o.counters)}</span>` : "",
      tags.includes("attacking") ? `<span class="chip atk">attacking</span>` : "",
      tags.includes("blocking") ? `<span class="chip blk">blocking</span>` : "",
      tags.includes("sick") ? `<span class="chip sick">summoning sick</span>` : ""].join("");
    return `<div class="${cls}" title="${esc(title)}" data-name="${esc(o.name)}">${h ? `<span class="badge">${esc(KIND_LABEL[h.kind] || h.kind)}</span>` : ""}` +
      ptChip + (chips ? `<span class="chips">${chips}</span>` : "") +
      `<span class="art"><img src="${cardImg(o.name)}" alt="" loading="lazy" onerror="noImage(this)"></span>` +
      `<div class="nm">${esc(o.name)}</div><div class="meta">${o.cost ? `<span class="cost">${esc(o.cost)}</span>` : ""}${o.pt ? `<b>${esc(o.pt)}</b>` : ""}` +
      `${o.handle != null ? `<span class="h">@${esc(o.handle)}</span>` : ""}</div>` +
      `${tags.length || (o.notes || []).length ? `<div class="tags">${esc(tags.concat(o.notes || []).join(", "))}</div>` : ""}</div>`;
  }
  middleHtml(st, hl) {
    const stack = st.stack && st.stack.length
      ? `<ul>${st.stack.map(s => `<li>${esc(s.kind ? `#${s.index} ${s.kind} ${s.source} (${s.who || "?"})${s.text ? ": " + s.text : ""}` : s.text)}</li>`).join("")}</ul>`
      : `<span class="empty">empty</span>`;
    const facts = st.facts ? `<div class="engine-note" title="engine estimates, not truth">FACTS (engine estimates): ${esc(st.facts)}</div>` : "";
    return `<div class="middle"><div class="box"><h4>stack (top first)</h4>${stack}</div>` +
      `<div class="box"><h4>combat</h4>${esc(st.combat || "none")}${facts}</div></div>`;
  }
  legendHtml(hl) {
    if (!Object.keys(hl).length) return "";
    return `<div class="legend">${["chosen", "planned", "looked", "mentioned", "engine"]
      .map(k => `<span><i style="background:var(--${k})"></i>${KIND_LABEL[k]}</span>`).join("")}</div>`;
  }

  renderPlan() {
    const d = this.decision, st = this.stateOf(d);
    let plan = null, at = null;
    for (const e of this.decisions.slice(0, this.cur + 1)) if (e.plan && e.seat === (d && d.seat)) { plan = e.plan; at = e; }
    const blocks = (st && st.blocks) || {};
    const block = name => blocks[name] ? [blocks[name].text].concat(blocks[name].lines).filter(Boolean).join(" ") : "";
    const parts = [];
    if (plan) parts.push(`<div class="box"><h4>plan (set_plan at T${esc(at.turn)} ${esc(at.phase)}${at === d ? ", this decision" : ""})</h4>` +
      `<div class="goal">${esc(plan.goal)}</div><div>next: ${esc(plan.next)}</div></div>`);
    else if (st && st.plan) parts.push(`<div class="box"><h4>plan (as the prompt shows it)</h4><div class="goal">${esc(st.plan.goal)}</div><div>next: ${esc(st.plan.next)}</div></div>`);
    if (block("YOUR OPPONENT PROFILE")) parts.push(`<div class="box"><h4>the seat's opponent profile</h4>${esc(block("YOUR OPPONENT PROFILE"))}</div>`);
    if (block("YOUR CARD INTERACTIONS")) parts.push(`<div class="box"><h4>the seat's card interactions</h4>${esc(block("YOUR CARD INTERACTIONS"))}</div>`);
    $(".plan", this.el).innerHTML = parts.join("");
  }

  renderFilters() {
    $(".filters", this.el).innerHTML = FILTERS.map(([k, label]) => `<button data-f="${k}" class="${k === this.filter ? "on" : ""}">${esc(label)}</button>`).join("");
    $(".filters", this.el).onclick = ev => {
      const f = ev.target.getAttribute("data-f"); if (!f) return;
      this.filter = f; this.renderFilters(); this.renderTimeline();
    };
  }

  renderTimeline() {
    const pass = (FILTERS.find(f => f[0] === this.filter) || FILTERS[0])[2];
    const rows = [];
    let n = -1;
    for (const e of this.events) {
      if (e.kind === "decision") {
        n++;
        if (!pass(e)) continue;
        const tools = e.calls.flatMap(c => c.tools.map(t => t.name)).filter(t => t !== "answer");
        const flag = isBreach(e) || e.answer_invalid ? " !" : "";
        rows.push(`<li data-n="${n}" class="${n === this.cur ? "cur" : ""}"><span class="t">T${esc(e.turn)} ${esc((e.phase || "").slice(0, 6))}</span>` +
          `<span>${esc(e.type)}${e.tier === "ai" ? " (FORGE AI)" : e.tier === "auto" ? ` (${esc(e.auto_class === "forced" ? "forced" : e.auto_class || "not asked")})` : ""}${tools.length ? " · " + esc(tools.join(", ")) : ""}<span class="flag">${flag}</span></span>` +
          `<span class="t">${fmtMs(e.latency_ms)}</span></li>`);
      } else if (e.kind === "window" && this.filter === "all") {
        // Consecutive windows of one turn collapse into one row.
        const prev = rows.length ? rows[rows.length - 1] : null;
        if (prev && prev.win && prev.turn === e.turn) { prev.count++; prev.reasons.add(e.reason); }
        else rows.push({win: true, turn: e.turn, count: 1, reasons: new Set([e.reason])});
      }
    }
    for (let k = 0; k < rows.length; k++) if (rows[k].win) {
      const w = rows[k];
      rows[k] = `<li class="win"><span class="t">T${esc(w.turn)}</span><span>passed ${w.count} priority window${w.count > 1 ? "s" : ""} without asking (${esc([...w.reasons].join(", "))})</span><span></span></li>`;
    }
    const tl = $(".timeline", this.el);
    tl.innerHTML = rows.join("") || `<li class="win"><span></span><span>no decisions yet</span><span></span></li>`;
    tl.onclick = ev => { const li = ev.target.closest("li[data-n]"); if (li) app.seek(this, +li.getAttribute("data-n")); };
    const cur = $("li.cur", tl);
    if (cur) cur.scrollIntoView({block: "nearest"});
  }

  renderDetail() {
    const d = this.decision, el = $(".detail", this.el);
    if (!d) { el.innerHTML = ""; return; }
    const st = d.state, q = st && st.decision ? st.decision.split("\n")[0] : null;
    const chosen = new Set(d.chosen || []);
    const flags = [];
    if (d.fallback) flags.push(`harness fallback: ${d.fallback}`);
    if (d.answer_invalid) flags.push(`the first answer was refused by the engine-side check and corrected in the same conversation`);
    if (d.first_failed_reply) flags.push(`first failed reply: ${d.first_failed_reply}`);
    const opts = d.options.length
      ? `<ol class="opts">${d.options.map((o, i) => `<li class="${chosen.has(i) ? "sel" : ""}"><span class="n">${i + 1})</span>${esc(o)}</li>`).join("")}</ol>` : "";
    const steps = d.calls.map(c => this.callHtml(c)).join("");
    const a = Object.assign({}, d.answer);
    if (d.type === "blockers" && Array.isArray(a.blocks) && !a.blocks.length) a.blocks_note = "no blocks";
    const extras = ["blocks_note", "yield", "profile", "lines", "keep_untapped", "blocks", "amounts", "number", "card", "steps"]
      .filter(k => a[k] != null && a[k] !== "" && a[k] !== "none" && !(Array.isArray(a[k]) && !a[k].length) && !(k === "number" && a[k] === 0))
      .map(k => `<div><b>${esc(k)}</b>: ${esc(typeof a[k] === "string" ? a[k] : JSON.stringify(a[k]))}</div>`).join("");
    el.innerHTML = `<h3>${esc(d.seat)} · T${esc(d.turn)} ${esc(d.phase)} · ${esc(d.type)}</h3>` +
      `<div class="engine-note">tier ${esc(d.tier)} · ${fmtMs(d.latency_ms)} · ${esc(d.prompt_tokens || 0)} prompt tokens (${esc(d.cached_tokens || 0)} cached) · ${esc(d.completion_tokens || 0)} completion · answered via ${esc(d.answer_via || "-")}</div>` +
      (q ? `<div class="q">${esc(q)}</div>` : d.tier !== "smart" && d.tier !== "fast" ? `<div class="q">${esc(autoLabel(d))}</div>` : "") +
      flags.map(f => `<div class="flagbox">${esc(f)}</div>`).join("") + opts +
      (a.why ? `<div class="why"><b>why</b> (the model's own words in its answer): ${esc(a.why)}</div>` : "") +
      (extras ? `<div class="step tool-answer">${extras}</div>` : "") +
      (steps ? `<h3>what the model did, in order</h3>${steps}` : "") +
      (d.prompt ? `<details><summary>the whole prompt the seat was shown</summary><pre>${esc(d.prompt)}</pre></details>` : "");
  }

  callHtml(c) {
    const head = `<div class="head"><span class="k">model call ${esc(c.call)}${c.round ? " · round " + esc(c.round) : ""}</span>` +
      `<span class="m">${fmtMs(c.elapsed_ms)}${c.provider ? " · " + esc(c.provider) : ""}${c.prompt_tokens ? " · " + esc(c.prompt_tokens) + " tok in" : ""}</span></div>`;
    const reasoning = c.reasoning ? `<details class="reasoning"><summary>provider-returned reasoning, as logged (${c.reasoning.length} chars)</summary><pre>${esc(c.reasoning)}</pre></details>` : "";
    const tools = c.tools.map(t => {
      const args = t.args && t.args._raw == null ? JSON.stringify(t.name === "answer" ? compactAnswer(t.args, this.decision) : t.args) : (t.args || {})._raw;
      return `<div class="step tool-${esc(t.name)}"><div class="head"><span class="k">${esc(t.name)}</span><code>${esc(args)}</code></div>` +
        (t.name === "answer" ? `<div class="engine-note">engine: ${esc(t.result)}</div>` : `<pre>${esc(t.result)}</pre>`) + `</div>`;
    }).join("");
    return `<div class="step">${head}${reasoning}${tools}</div>`;
  }

  renderSummary() {
    const s = this.info.summary || {}, g = s.game || this.game || {};
    const tally = Object.entries(s.tally || {}).map(([seat, t]) =>
      `<span>${esc(seat)}: <b>smart ${esc(t.smart)}</b> · auto ${esc(t.auto)} · ` +
      `<span class="${t.ai === "0" ? "ok" : "bad"}">ai=${esc(t.ai)}</span> · <span class="${t.textParses === "0" ? "ok" : "bad"}">textParses=${esc(t.textParses)}</span> · avg ${esc(t.avgSmart)}</span>`).join("");
    const rv = s.review || {};
    const review = Object.keys(rv).length ? `<span>review sheet: ${["TAKEN", "LOST", "SKIP", "TOOL", "SLOW", "LEAK", "REPORT", "LOG", "LINE"].map(k => `${k} <b>${esc(rv[k] || 0)}</b>`).join(" · ")}</span>` : "";
    const cost = s.cost ? `<span>run cost <b>$${s.cost.usd.toFixed(2)}</b> (${esc(s.cost.calls)} calls; prices checked ${esc((s.cost.prices_checked || []).join(","))})</span>` : "";
    const stamps = ["harness_epoch", "observation_version", "answer_tool_version", "native_tools_version", "lookahead_version", "context_profile", "context_sha", "overlay_sha", "deck_sha", "engine_sha"]
      .filter(k => g[k] != null).map(k => `${k}=${esc(g[k])}`).join(" · ");
    $(".summary", this.el).innerHTML = tally + review + cost +
      (g.duration_ms ? `<span>duration ${fmtMs(g.duration_ms)}</span>` : "") +
      (stamps ? `<details><summary>version stamps (game row)</summary><code>${stamps}</code></details>` : "");
  }
}

// The answer call without its unused default fields; a blockers answer keeps `blocks` even when empty (no blocks).
function compactAnswer(a, d) {
  const out = {};
  for (const [k, v] of Object.entries(a || {})) {
    if (k === "blocks" && d && d.type === "blockers") { out[k] = v; continue; }
    if (k === "why" || v === "" || v === false || v === "none" || (Array.isArray(v) && !v.length) || (k === "number" && v === 0)) continue;
    out[k] = v;
  }
  return out;
}

const app = {
  panes: [], timer: null,
  async start() {
    const games = await fetch("api/games").then(x => x.json());
    const root = $("#panes");
    root.classList.toggle("two", games.length > 1);
    this.panes = games.map(g => new Pane(g.i, g, root));
    await Promise.all(this.panes.map(p => p.load()));
    this.fromHash();
    this.renderAll();
    this.bind();
    setInterval(() => this.panes.forEach(p => { if (p.info.live && !p.done) p.renderStatus(); }), 500);
  },
  renderAll() { this.panes.forEach(p => p.render()); this.toHash(); },
  seek(pane, n) {
    pane.cur = Math.max(0, Math.min(n, pane.decisions.length - 1));
    if ($("#lockstep").checked && this.panes.length > 1) {
      const d = pane.decision;
      for (const p of this.panes) if (p !== pane && d) p.cur = closestByTurn(p, d.turn, d.phase);
    }
    this.renderAll();
  },
  step(k) { this.seek(this.panes[0], this.panes[0].cur + k); },
  bind() {
    const pv = $("#preview");
    document.addEventListener("mouseover", ev => {
      const c = ev.target.closest(".card[data-name]");
      if (!c || c.classList.contains("noimg")) { pv.hidden = true; return; }
      pv.innerHTML = `<img src="${cardImg(c.getAttribute("data-name"))}" alt="">`;
      pv.hidden = false;
    });
    $("#first").onclick = () => this.seek(this.panes[0], 0);
    $("#last").onclick = () => this.seek(this.panes[0], 1e9);
    $("#prev").onclick = () => this.step(-1);
    $("#next").onclick = () => this.step(1);
    $("#play").onclick = () => this.toggle();
    $("#turn").onchange = ev => {
      const t = +ev.target.value, p = this.panes[0];
      const n = p.decisions.findIndex(d => d.turn >= t);
      if (n >= 0) this.seek(p, n);
    };
    document.addEventListener("keydown", ev => {
      if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
      if (ev.key === "ArrowRight") this.step(1);
      else if (ev.key === "ArrowLeft") this.step(-1);
      else if (ev.key === "Home") this.seek(this.panes[0], 0);
      else if (ev.key === "End") this.seek(this.panes[0], 1e9);
      else if (ev.key === " ") { ev.preventDefault(); this.toggle(); }
    });
  },
  toggle() {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; $("#play").innerHTML = "&#x25B6;"; return; }
    $("#play").innerHTML = "&#x23F8;";
    const tick = () => {
      const p = this.panes[0];
      if (p.cur >= p.decisions.length - 1) { this.toggle(); return; }
      this.step(1);
      const sp = $("#speed").value, next = p.decisions[p.cur + 1];
      const real = Math.max(300, (next && next.latency_ms) || 1000);
      const wait = sp === "step" ? 1000 : sp === "real" ? real : Math.max(250, real / +sp);
      this.timer = setTimeout(tick, wait);
    };
    this.timer = setTimeout(tick, 200);
  },
  toHash() {
    const p = this.panes[0];
    if (p) history.replaceState(null, "", `#d=${p.cur + 1}`);
  },
  fromHash() {
    const m = /d=(\d+)/.exec(location.hash);
    if (m && this.panes[0]) this.panes[0].cur = Math.max(0, Math.min(+m[1] - 1, this.panes[0].decisions.length - 1));
  },
};

const PHASES = ["null", "UNTAP", "UPKEEP", "DRAW", "MAIN1", "COMBAT_BEGIN", "COMBAT_DECLARE_ATTACKERS", "COMBAT_DECLARE_BLOCKERS",
  "COMBAT_FIRST_STRIKE_DAMAGE", "COMBAT_DAMAGE", "COMBAT_END", "MAIN2", "END_OF_TURN", "CLEANUP"];
// Lockstep: the other arm's last decision at or before the same turn and phase. Same seed, same start; the games
// diverge after the first decision the arms answer differently, so this aligns by game time, not by decision.
function closestByTurn(pane, turn, phase) {
  const key = (t, ph) => t * 100 + Math.max(0, PHASES.indexOf(String(ph)));
  const target = key(turn, phase);
  let best = 0;
  pane.decisions.forEach((d, n) => { if (key(d.turn, d.phase) <= target) best = n; });
  return best;
}

app.start();
