"use strict";
// The eval page: GET api/eval (demo_eval.report) and draw it. No number here is computed in the browser.
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const f3 = x => (x == null ? "?" : (+x).toFixed(3));
const ci = c => (c ? `[${f3(c[0])}, ${f3(c[1])}]` : "");
// The canaries and the zero-model gaming arms: the stages that would fail if the harness stopped deciding honestly.
const KEY = new Set(["canary-floor", "canary-random", "gaming", "javatests", "surfaces"]);

function headCard(h) {
  if (h.status === "recorded") {
    // The headline card shows the latest condition (the list's last run); the forest below shows them all.
    const best = h.result.runs[h.result.runs.length - 1];
    const fl = best ? best.headline.floors : {};
    return `<div class="head"><h3>${esc(h.key)} <span class="status recorded">recorded</span></h3>
      <p class="what">${esc(h.what)}</p>
      ${best ? `<div class="big">${f3(best.headline.value)}</div>
      <p class="what">95% CI ${ci(best.headline.ci95)} · ${esc(best.label)} · ${esc(best.puzzles)} puzzles × k=${esc(best.k)}</p>
      <p class="floor">floors: random ${f3(fl.random)} · PASS ${f3(fl.pass)} · first option ${f3(fl.first)}</p>` : "<p class='what'>no results on disk</p>"}
      <p class="floor">${esc(h.floor)}</p></div>`;
  }
  const p = h.plan;
  return `<div class="head"><h3>${esc(h.key)} <span class="status notrun">${esc(h.status)}</span></h3>
    <p class="what">${esc(h.what)}</p>
    <div class="big">&mdash;</div>
    ${p ? `<p class="what">plan: ${esc(p.panel)} panel, ${esc(p.cells)} cells, ${esc(p.games)} games (seeds ${esc(p.seeds)}, both seat orders)</p>
    <p class="what">expected cost $${esc(p.cost_usd[0])} to $${esc(p.cost_usd[1])} at $${esc(p.game_cost_usd[0])} to $${esc(p.game_cost_usd[1])} a Luna game</p>` : ""}
    <p class="floor">floor: ${esc(h.floor)}</p></div>`;
}

// A forest plot on a fixed axis: each condition's headline and interval, the floors as dashed lines.
function forest(puz, caption) {
  const lo = -0.2, hi = 1.0, x = v => `${((v - lo) / (hi - lo)) * 100}%`;
  const fl = puz.runs[0] ? puz.runs[0].headline.floors : {};
  const floors = [["random", fl.random], ["PASS", fl.pass], ["first", fl.first]].filter(f => f[1] != null);
  const rows = puz.runs.map(r => `<tr><td>${esc(r.label)}</td>
    <td class="num">${f3(r.headline.value)} ${ci(r.headline.ci95)}</td>
    <td class="num">pass@1 ${f3(r["pass@1"].value)}</td>
    <td><div class="axis"><div class="rule"></div>
      ${floors.map(([n, v]) => `<div class="fl" style="left:${x(v)}"></div>`).join("")}
      <div class="ci" style="left:${x(r.headline.ci95[0])};width:calc(${x(r.headline.ci95[1])} - ${x(r.headline.ci95[0])})"></div>
      <div class="pt" style="left:${x(r.headline.value)}"></div></div></td></tr>`).join("");
  const ticks = [-0.2, 0, 0.2, 0.4, 0.6, 0.8, 1.0].map(t => `<span style="left:${x(t)}">${t}</span>`).join("");
  return `<table class="forest">${rows}
    <tr><td></td><td></td><td></td><td><div class="axis ticks">${ticks}</div></td></tr></table>
    <p class="lede">Bar: the headline and its 95% interval. Dashed lines, the floors: ${floors.map(([n, v]) => `${esc(n)} ${f3(v)}`).join(" · ")}.
    ${caption}</p>`;
}

function gate(acc) {
  if (!acc) return `<p class="lede">No acceptance run given (start the server with --acceptance OUT).</p>`;
  return `<table class="gate"><tr><th>stage</th><th></th><th>what it proved</th></tr>` +
    acc.stages.map(s => `<tr class="${KEY.has(s.stage) ? "key" : ""}"><td class="stage">${esc(s.stage)}</td>
      <td><span class="status ${esc(s.status)}">${esc(s.status)}</span></td><td class="detail">${esc(s.detail)}</td></tr>`).join("") +
    `</table>`;
}

fetch("api/eval").then(r => r.json()).then(r => {
  $("#heads").innerHTML = r.headlines.map(headCard).join("");
  const p = r.headlines.find(h => h.key === "PUZ");
  if (p && p.result.runs.length) {
    const pr = p.result;
    $("#puz-title").hidden = false;
    $("#puz").innerHTML = forest(pr, `Luna, ${esc(pr.runs[0].puzzles)} frozen rules puzzles (${esc(pr.suite)}), 5 attempts each. ` +
      `The headline subtracts each puzzle's random solve rate, so random play scores 0. Source: ${esc(pr.report)}.`);
    if (pr.hard && !pr.hard.error) {
      $("#hard-title").hidden = false;
      $("#hard").innerHTML = forest({runs: [pr.hard]}, `A pilot, not a PUZ score: one attempt each on ${esc(pr.hard.puzzles)} open ` +
        `boards of the generated hard suite (${esc(pr.hard.suite)}); floors: ${Object.entries(pr.hard.headline.floors).map(([n, v]) => `${esc(n)} ${f3(v)}`).join(" · ")}. Source: ${esc(pr.hard.path)}.`);
    } else if (pr.hard) {  // a --hard-results that could not be scored says so, rather than leaving the row out
      $("#hard-title").hidden = false;
      $("#hard").innerHTML = `<p class="lede">The hard-suite pilot could not be read: ${esc(pr.hard.error)}</p>`;
    }
  }
  const a = r.acceptance;
  const notPassed = a ? a.stages.filter(s => s.status !== "PASS").map(s => `${s.stage} ${s.status}`) : [];
  $("#gate-lede").textContent = a ? `${a.passed} of ${a.total} stages passed in ${a.out}` +
    (notPassed.length ? `; not passed: ${notPassed.join(", ")}.` : ". Each line says what the stage proved.") : "";
  $("#gate").innerHTML = gate(a);
}).catch(e => { $("#heads").innerHTML = `<p class="lede">could not load the report: ${esc(e)}</p>`; });
