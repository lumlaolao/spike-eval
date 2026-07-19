# Training-our-own-model feasibility (Qwen post-training deep dive)

> Research deep-dive, 2026-07-18. Four-lens sweep (prior art, data availability, methods/cost, corpus fit) + synthesis. Commissioned to answer: "would it be worth post-training a Qwen model on tons of human or AI games?"


## 1. Verdict

**Worth it — but not the project you asked about.** "Tons and tons of human games" is a dead end: no per-decision human MTG corpus exists anywhere (17lands ReplayData is per-turn aggregates with no within-turn ordering, targets, or stack decisions; Arena/MTGO expose only local self-logs; scraping is ToS-prohibited and technically impossible). What *is* worth it, and cheaply, is post-training a small Apache-2.0 Qwen3 model on **Forge-generated self-play traces plus your own win-labeled decision records** — a corpus this repo is already 80% instrumented to produce at 500k–1M decisions/day for free. The crux is the teacher-ceiling problem: behavioral cloning caps at the strength of the data, and your strongest available teacher is Forge's built-in AI. But since haiku currently *loses* to Forge AI 1–8, cloning the Forge AI is a strict improvement over today, costs $50–500 total, removes API rate limits and per-game latency, and sets up the only known recipes for exceeding the teacher (win-filtered expert iteration, then shaped-reward RL). Novelty bonus: no published system plays full constructed MTG with a trained LLM policy — this would be first.

## 2. Decisive constraints

- **Data reality.** Human data: nonexistent at decision granularity, legally foreclosed at scale. Synthetic data: abundant — Forge AI-vs-AI runs at ~9–20s/game headless (~4,500–9,000 games/day on one Mac), and the card-game scaling literature (arXiv 2509.01328) says you need ~400k–1M decision instances for DouDizhu-class games, with wins still improving monotonically to 1M. That's **1–2 days of generation** once the AI seat is instrumented. Your existing haiku corpus (~2,400 clean prompt/reply pairs + 832 accruing) is too small to train on but is exactly the right eval/regression set.
- **Teacher ceiling.** Every system that surpassed its teacher added a win signal (AlphaGo, AlphaStar, Cicero) — plain BC "cannot exceed the demonstrator." Forge AI is your ceiling for phase one. That's fine: it's above where you are. The absolute ceiling of Forge AI itself is unknown and probably amateur-level; getting *past* it requires outcome-conditioned training or search, not more cloning.
- **Cost is not the constraint.** QLoRA Qwen3-8B on 100k examples ≈ $15–25 on a rented 4090; 1M examples on 8B ≈ $500–700; even 32B/1M ≈ $2k. GRPO on a 7B fits one 24GB GPU via Unsloth. The entire recommended program below is under ~$1k of compute.
- **Serving is nearly free and already wired.** `OpenRouterBridge` speaks OpenAI-format to any `OPENROUTER_BASE_URL` — pointing it at local vLLM is config-only (dummy API key, `OPENROUTER_ALLOW_EGRESS=1`, ideally a localhost carve-out). 8–14B local inference gives 3–8s/decision → faster than the current `claude -p` bridge, no rate limits (the thing that contaminated 324/405 replies in `runs/haiku-vs-ai`), and it unlocks parallel JVMs against a batching server (10–50× LLM-game throughput).
- **The eval trap is already solved here.** PokerBench's warning — decision-accuracy lift did not transfer to head-to-head win rate (fine-tuned model beat GPT-4 on the benchmark, lost the money match) — means the only success metric that counts is **seeded full-game win rate**, which is precisely what your identity-keyed, baseline-anchored eval harness measures. This repo's eval infrastructure is its biggest asset.

## 3. Recommended path

**Phase 0 — Instrument the teacher (~1 week dev, $0 compute).**
Build `PlayerControllerAiLogged` by extending the existing `observeAiDec` shim across all ~15 strategic surfaces: call `super`, snapshot state with the existing prompt builder (observed lines currently log `prompt=null`), enumerate the options menu, map super's chosen SA to an index. Stamp `game_id`/`seed` on every decision line (the current file-co-location join is fragile). Fix a seed-based train/test split convention now, before any data exists. Accept the documented mana-tapping gap (lives in `ComputerUtilMana`, unloggable without touching the pinned engine).
*Success criterion:* seed-42 floor-mirror canary still bit-reproduces; an ai-vs-ai game emits ~90–100 decision records per seat with full prompt+options+choice.

**Phase 1 — Generate the corpus (~2–4 days wall clock, $0).**
Run Forge-AI mirrors and AI-vs-floor headless across your deck plans. Target 5–10k games ≈ **1–2M state/action records**. Rebalance action classes (`play` is 86% and PASS-heavy in the haiku corpus — `analyze_corpus.py` already measures pick bias); keep win labels for filtering.
*Success criterion:* ≥400k clean, class-rebalanced, seed-split records passing `analyze_corpus.py` with zero contamination.

**Phase 2 — SFT clone of Forge AI ($25–150).**
QLoRA Qwen3-8B (fallback 14B if 8B underperforms) on the Phase 1 corpus, output format identical to the current CHOICE/ATTACKERS/BLOCKS contract so the parser is unchanged. Serve via local vLLM through `OpenRouterBridge`; relax the `--jobs 1` guard for local backends.
*Success criterion (full games, seeded A/B, ≥50 games/matchup):* beats floor 20/20, beats random 20/20, **beats haiku head-to-head**, wins ≥35–45% vs Forge AI, with format-violation/fallback rate below haiku's and `ai=0` tally intact. If it can't beat haiku, stop and diagnose before spending more.

**Phase 3 — Exceed the teacher: expert iteration + DPO ($100–500).**
(a) *Rejection sampling / win-filtering:* play the Phase 2 model vs Forge AI and vs itself at scale (cheap now — local inference, parallel JVMs), keep only winners' trajectories, re-SFT. This is the zero-new-infrastructure recipe (PokerGPT's filtered-cloning, ETO baseline) and it also fixes BC's distribution-shift problem because the data now comes from states the student actually visits. Iterate 2–3 rounds.
(b) *DPO* on preference pairs built by offline re-querying stored prompts N times and pairing choices by downstream outcome (no natural same-state pairs exist — 0 duplicate prompts in the corpus). DPO is ~2–3× SFT compute; expect a modest bump, not exploration.
*Success criterion:* **>50% vs Forge AI over ≥200 seeded games** (enough for a meaningful CI). That is the headline result — a trained open model beating the engine's native AI at full constructed Magic.

**Phase 4 (optional, research-grade) — RL or candidate-picker ($200–1k).**
If Phase 3 plateaus near 50%: either GRPO with **shaped per-decision rewards** (life/board deltas, agreement with re-queried stronger model — the chess post-training paper found sparse terminal win/loss fails on 150-decision horizons), or the ICML 2024 Werewolf recipe: freeze the LLM as a candidate generator and train a tiny policy from win signal to pick among its top-k — the cheapest known way to surpass a fixed policy without touching weights.

## 4. What NOT to do

- **Don't pursue human game data.** It doesn't exist at decision granularity, WotC ToS prohibits collection at scale, and crowd-sourced opt-in (the 17lands model) is a multi-year community project, not a training plan.
- **Don't generate a teacher corpus with opus.** 35–45 min/game → 3–4k pairs/day, ~100+ days to reach the 400k-decision regime, at API cost — and ChessArena shows prompted frontier models are unreliable full-game players anyway. Opus is for showcase recordings, per your own validation ladder.
- **Don't full fine-tune or start at 32B.** Full FT buys ~nothing per token over LoRA ("LoRA Learns Less and Forgets Less"), needs an 8-GPU node, and 32B-Q4 is KV-cache-starved on a 4090 at your 2–4k-token prompts. 8–14B is the serving sweet spot.
- **Don't evaluate on decision accuracy.** PokerBench: +25pts accuracy, lost the match. Static agreement-with-teacher is a training diagnostic, never a success criterion. Full seeded games only.
- **Don't lead with sparse-reward RL from scratch.** Solly-style pure self-play worked on Liar's Poker, but arXiv 2507.00726 shows RL can't fix missing domain understanding and terminal-only reward on ~150-decision games is a credit-assignment tarpit. SFT first, RL to polish.
- **Don't bump `FORGE_COMMIT` mid-program.** Corpus, baselines, and eval results are only comparable within one engine version — a drift invalidates the whole A/B chain.

## 5. Open risks

1. **Forge AI's absolute strength is unknown.** You may clone-and-beat it and still have an objectively weak player (ByteRL shows CCG agents overfit to training opponents and stay exploitable). Mitigation: eval against held-out deck archetypes and, eventually, humans.
2. **No queryable teacher.** Forge AI can only be *observed* in its own games, not asked "what would you do here?" on student-visited states — so true DAgger relabeling is impossible without engine surgery. Expert iteration (Phase 3a) is the workaround, but it's weaker.
3. **Surface mismatch.** The AI seat's decision surfaces don't perfectly match the LLM seat's (mana tapping hidden, different auto-handling tiers). If the trained model faces prompt types absent from training, it falls back — every fallback is a bug per house rules. Mitigation: diff surface-type distributions between corpora before training.
4. **Archetype overfit.** The corpus comes from a handful of deck plans; expect sharp generalization cliffs on new decks (MTG-Causal-RL saw 12–70% win-rate spread by archetype).
5. **Eval variance.** 50%→55% vs Forge AI needs hundreds of seeded games to distinguish from noise; budget eval throughput (cheap once local) into every phase.
6. **IP posture.** Card names/oracle text in weights sit in the same tolerated-but-unlicensed zone as MTGJSON/Scryfall. Fine for a free/noncommercial fan release on Qwen3 (Apache-2.0); don't ship it commercially with an embedded card database.

**Bottom line:** ~1 week of instrumentation + <$1k of compute buys a serious shot at the first trained LLM to play full constructed Magic, an agent that beats your current haiku pilot, and freedom from CLI rate limits. The human-data version of the question is a no; the Forge-self-play version is a clear yes.