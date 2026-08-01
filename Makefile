# mtg-forge-llm-agent — the single implementation of the validation ladder and
# the recurring pipelines. Slash commands in .claude/commands/ and any docs
# must WRAP these targets, never re-implement them: two implementations of the
# same procedure is how CLAUDE.md's prose drifted from reality.
#
# Recipes are POSIX sh (make's shell), not zsh.

FORGE_HOME ?= $(HOME)/code/forge
CANDIDATES ?=
ROUND      ?=
ARM        ?=
RUNS       ?=

.PHONY: help test jtest canary ladder install preflight mine str-floors scorecard leaderboard

help:
	@echo "mtg-forge-llm-agent targets:"
	@echo "  make test         - ladder 0: every eval/test_*.py (~10s, no JVM)"
	@echo "  make jtest        - ladder 0b: LlmSpikeForkTest + LlmSpikeTest (~1min, no game)"
	@echo "  make canary       - ladder 1: seed-42 floor mirror; ASSERTS 'A after 108 turns' (~12s)"
	@echo "  make ladder       - test + jtest + canary, in order"
	@echo "  make install      - copy forge-overlay into FORGE_HOME (bash install.sh)"
	@echo "  make preflight CANDIDATES=x.jsonl - deck names / classpath / pin / disk checks"
	@echo "  make mine ROUND=n [JOBS=6]        - full mining round (eval/pipelines/mine_round.sh)"
	@echo "  make str-floors ARM=random|floor|ai - STR floor arm (eval/pipelines/str_floors.sh)"
	@echo "  make scorecard    - five-axis scorecard (eval/build_report.py)"
	@echo "  make leaderboard RUNS='runs/puz/x/*' - PUZ leaderboard (refuses cross-suite ranks)"

# ---- validation ladder (cheapest first; see CLAUDE.md) ----------------------

test:
	@fail=0; \
	for f in eval/test_*.py; do \
	  python3 "$$f" >/dev/null 2>&1 || { echo "FAIL $$f"; fail=1; }; \
	done; \
	[ "$$fail" -eq 0 ] && echo "ladder 0: all $$(ls eval/test_*.py | wc -l | tr -d ' ') eval test files pass" || exit 1

jtest:
	@cd "$(FORGE_HOME)" && mvn -q -pl forge-gui-desktop -am -o test \
	  -Dtest='LlmSpikeForkTest+LlmSpikeTest' \
	  -Dsurefire.failIfNoSpecifiedTests=false \
	&& echo "ladder 0b: LlmSpikeForkTest + LlmSpikeTest pass"

# The seed-42 floor mirror is only a canary if something ASSERTS its pinned
# outcome. Unasserted, a drifted outcome scrolls past unread (see CLAUDE.md:
# "a stale canary is a canary that can't fail"). If a deliberate harness
# change moves the pin, update BOTH the assertion below and CLAUDE.md.
canary:
	@out=$$(mktemp); \
	( cd "$(FORGE_HOME)" && GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor \
	  bash run-llm-headless.sh ) >"$$out" 2>&1; \
	if grep -q "outcome: A after 108 turns (AllOpponentsLost)" "$$out"; then \
	  echo "canary: A after 108 turns (AllOpponentsLost) — pinned outcome holds"; rm -f "$$out"; \
	else \
	  echo "CANARY FAILED — expected 'outcome: A after 108 turns (AllOpponentsLost)', got:"; \
	  grep "outcome:" "$$out" || tail -5 "$$out"; \
	  echo "full log: $$out"; exit 1; \
	fi

ladder: test jtest canary

# ---- build / install --------------------------------------------------------

install:
	bash install.sh

# ---- pipelines (each refuses to start if preflight fails) -------------------

preflight:
	@[ -n "$(CANDIDATES)" ] || { echo "usage: make preflight CANDIDATES=path.jsonl"; exit 2; }
	python3 eval/preflight.py "$(CANDIDATES)"

mine:
	@[ -n "$(ROUND)" ] || { echo "usage: make mine ROUND=4 [JOBS=6]"; exit 2; }
	bash eval/pipelines/mine_round.sh "$(ROUND)"

str-floors:
	@[ -n "$(ARM)" ] || { echo "usage: make str-floors ARM=random|floor|ai"; exit 2; }
	bash eval/pipelines/str_floors.sh "$(ARM)"

# ---- reporting --------------------------------------------------------------

scorecard:
	python3 eval/build_report.py

leaderboard:
	@[ -n "$(RUNS)" ] || { echo "usage: make leaderboard RUNS='runs/puz/tournament-8/*'"; exit 2; }
	python3 eval/puz_index.py leaderboard $(RUNS)
