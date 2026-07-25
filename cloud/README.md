# Cloud runbook — running DAgger generation on an AWS GPU box

Plain-language guide for going from zero to a running data-generation box and
back, without surprises on the bill. Placeholders you must replace are written
`<LIKE_THIS>`.

**The golden rule: the instance costs money every hour it exists in "running"
state, whether or not it is doing anything. The last step of every session is
STOP THE INSTANCE.**

## Cost expectations

| What | Rate (us-east-1, July 2026 ballpark) |
|---|---|
| g5.xlarge **spot** | ~$0.44/hr (moves with demand; can be interrupted) |
| g5.xlarge on-demand | ~$1.01/hr |
| g5.2xlarge spot | ~$0.60/hr |
| 200 GB gp3 EBS volume | ~$16/mo — **charged even while the instance is stopped** |
| Data transfer out (rsync results home) | ~$0.09/GB (jsonl corpora are small; cents) |

A 6-hour generation session on g5.xlarge spot ≈ **$2.60**. Forgetting the
instance overnight (14h) ≈ $6.20 spot / $14 on-demand. Forgetting it for a
month ≈ $320 on-demand. Stop the instance.

Track every session in the local ledger so the dashboard budget bar stays honest:

```bash
# on your laptop, after each session (hours * rate = what you spent)
echo '{"ts": '"$(date +%s)"', "what": "g5.xlarge spot", "hours": 2.5, "rate": 0.44}' \
  >> ~/code/mtg-forge-llm-agent/cloud/aws_ledger.jsonl
```

## 0. Prerequisites (one-time)

- AWS account with the GPU quota approved ("Running On-Demand G and VT
  instances" and/or "All G and VT Spot Instance Requests" ≥ 8 vCPUs in
  us-east-1 — g5.xlarge is 4 vCPUs, g5.2xlarge is 8).
- An EC2 **key pair**: EC2 console → Key Pairs → Create key pair → type ED25519,
  format `.pem`. Save it as `~/.ssh/mtg-gpu.pem` and run
  `chmod 400 ~/.ssh/mtg-gpu.pem`. You cannot download it again later.

## 1. Launch the instance

EC2 console → Launch instance:

1. **Name**: `mtg-dagger-gpu`
2. **AMI**: search "Deep Learning OSS Nvidia Driver AMI GPU PyTorch" —
   pick the **Ubuntu 22.04** one.
3. **Instance type**: `g5.xlarge` (or `g5.2xlarge` if you want more CPU for
   parallel JVMs).
4. **Spot**: Advanced details → Purchasing option → check **Request Spot
   Instances** (leave the price fields at default). Spot can be reclaimed
   with 2 minutes' notice — the DAgger driver resumes per-cell, so an
   interruption costs you at most the in-flight cell.
5. **Key pair**: the one from step 0.
6. **Network settings**: allow **SSH (port 22)** from **My IP** only.
   Do NOT open port 8080 to the world — the model server binds 127.0.0.1
   and everything talks over localhost.
7. **Storage**: 200 GB gp3.
8. Launch, wait for state "Running", copy the **Public IPv4 address**
   (call it `<INSTANCE_IP>`; it changes on every stop/start).

## 2. SSH in

```bash
ssh -i ~/.ssh/mtg-gpu.pem ubuntu@<INSTANCE_IP>
```

First connect asks "are you sure?" — type `yes`. If you get
`Permission denied (publickey)`: wrong user (must be `ubuntu`), wrong `.pem`,
or `chmod 400` missing.

## 3. Get the code onto the box

The agent repo is private, so easiest is rsync from your laptop (run this **on
the laptop**, not the instance):

```bash
rsync -av -e "ssh -i ~/.ssh/mtg-gpu.pem" \
  --exclude runs --exclude models --exclude .venv-mlx --exclude '.claude' \
  ~/code/mtg-forge-llm-agent/ ubuntu@<INSTANCE_IP>:mtg-forge-llm-agent/
```

(Alternative: `AGENT_REPO_URL=git@github.com:<YOU>/mtg-forge-llm-agent.git`
passed to the bootstrap, if you set up a deploy key.)

## 4. Upload the student model

vLLM wants a HuggingFace-format directory (config.json + safetensors), **not**
the MLX-fused one. On the laptop:

```bash
rsync -av -e "ssh -i ~/.ssh/mtg-gpu.pem" \
  <LOCAL_HF_MODEL_DIR>/ ubuntu@<INSTANCE_IP>:models/student/
```

## 5. Run the bootstrap (on the instance)

```bash
cd ~/mtg-forge-llm-agent
bash cloud/bootstrap_g5.sh --dry-run   # read the plan first
bash cloud/bootstrap_g5.sh             # ~10-20 min on first run
```

It is safe to re-run any time (it skips finished steps), and it is the fix
for "reboot broke everything" (a reboot wipes `/tmp/forge_cp.txt`, which every
game needs; the bootstrap restores it from cache).

It ends with a **seed-42 canary game**: identity A must win in 108 turns. If
that check fails, the engine on the box does not match the validated baseline —
stop and investigate before generating any data.

Then start the model server in a tmux session (so it survives SSH drops):

```bash
tmux new -s vllm 'bash ~/serve_student.sh'
# detach: Ctrl-b then d      reattach later: tmux attach -t vllm
```

Optionally re-run the bootstrap's LLM smoke test once the server is up (it
prints the exact commands — it will ask you to set `OPENROUTER_ALLOW_EGRESS=1`
yourself; with the localhost endpoint no data leaves the machine, but the
harness's consent gate applies to every `openrouter:` spec and the scripts
will never set it for you).

## 6. Start a generation run

```bash
tmux new -s dagger
cd ~/mtg-forge-llm-agent
export OPENROUTER_ALLOW_EGRESS=1 OPENROUTER_API_KEY=local-only
export OPENROUTER_BASE_URL=http://127.0.0.1:8080/v1

python3 eval/run_batch.py --exploratory --shadow-ai \
  --seeds 9100..9102 --pairs --jobs <N> --cap-minutes 8 \
  --deck-a eval/decks/<DECK_A>.txt --deck-b eval/decks/<DECK_B>.txt \
  --name-a <DECK_A> --name-b <DECK_B> \
  --controller-a llm --controller-b ai \
  --smart-a openrouter:default_model --fast-a openrouter:default_model \
  --out runs/<RUN_NAME>/<DECK_A>-vs-<DECK_B>
# detach with Ctrl-b d; check progress with: tmux attach -t dagger
```

`--jobs <N>`: vLLM handles concurrent requests properly (unlike the local mlx
server), so 2-4 parallel JVMs are reasonable on g5.2xlarge; start with 2 on
g5.xlarge (16 GB RAM, ~1 GB RSS per JVM + vLLM's host RAM) and watch `free -h`.

## 7. rsync results back (on the laptop)

```bash
rsync -av -e "ssh -i ~/.ssh/mtg-gpu.pem" \
  ubuntu@<INSTANCE_IP>:mtg-forge-llm-agent/runs/<RUN_NAME>/ \
  ~/code/mtg-forge-llm-agent/runs/<RUN_NAME>-cloud/
```

Pull results **before stopping** — a spot instance can also be reclaimed, so
rsync periodically during long runs, not only at the end.

## 8. STOP THE INSTANCE

EC2 console → select the instance → Instance state → **Stop instance**.
Or from the laptop, if you have the AWS CLI configured:

```bash
aws ec2 stop-instances --instance-ids <INSTANCE_ID> --region us-east-1
aws ec2 describe-instances --instance-ids <INSTANCE_ID> --region us-east-1 \
  --query 'Reservations[].Instances[].State.Name' --output text   # expect: stopping/stopped
```

- **Stop** = billing for compute ends; the disk (and your setup) persists for
  ~$16/mo; next session is just start + `bash cloud/bootstrap_g5.sh` (fast) + go.
- **Terminate** = everything is deleted, $0/mo; next session repeats this
  runbook from step 1.
- Note: pure spot *requests* sometimes only offer Terminate. If Stop is greyed
  out, rsync everything you care about, then Terminate.

Finally, append the session to the ledger (see "Cost expectations" above) so
the dashboard's budget bar reflects reality.

## Local dashboard

On the laptop, `python3 cloud/dashboard.py` serves http://localhost:8377 —
DAgger run status, corpus stats, budget bar (from the ledger above), training
log tail, incident watch. Two things to know:

- **Paths are per-job.** The shipped defaults point at the current run
  (`runs/d4-dagger-r1`, job `f9e4d46b`). For the next run, copy
  `cloud/dashboard_config.example.json` to `cloud/dashboard_config.json`
  (git-ignored) and update `runs_dir` / `dagger_dir` — otherwise those panels
  will truthfully say "no data" about a finished job forever.
- **Real AWS cost is opt-in.** Set `"aws_ce": true` in the config to also
  fetch actual month-to-date spend via Cost Explorer. Off by default because
  each query is a real (tiny) AWS charge: $0.01/request, cached 1 hour here.
  Without it the budget panel runs on the ledger alone.

## If something fails

| Symptom | Likely cause / fix |
|---|---|
| `Permission denied (publickey)` on SSH | user must be `ubuntu`; key must be the launch key pair; `chmod 400 ~/.ssh/mtg-gpu.pem` |
| SSH hangs forever | security group doesn't allow port 22 from your (current) IP — "My IP" pins to the IP at rule-creation time; edit the inbound rule |
| bootstrap dies at "apt-get update failed" | instance has no internet route (launched without public IP?) — relaunch with default VPC settings |
| bootstrap dies at "Forge HEAD is ... but pin is ..." | Forge upstream moved and the pinned commit didn't fetch — do not override; the pin is what makes results comparable. `cd ~/forge && git fetch origin <PIN_SHA>` then re-run |
| Maven build fails | check `java -version` says 17; first build needs network for dependencies; re-run bootstrap (it resumes) |
| every game exits 1 instantly | `/tmp/forge_cp.txt` missing (classic post-reboot failure) — re-run `bash cloud/bootstrap_g5.sh`, it restores it from cache |
| canary FAILS (not A/108) | engine mismatch or harness drift — do NOT generate data; compare `FORGE_COMMIT`, re-run bootstrap, and if it persists debug locally, not in the cloud |
| `pip install vllm` fails | `nvidia-smi` broken → wrong AMI; relaunch with the Deep Learning OSS Nvidia Driver AMI |
| vLLM OOMs / won't load model | model too big for A10G 24 GB VRAM — quantize or lower `--max-model-len`; edit `~/serve_student.sh` |
| games run but 0 model calls | server not on port 8080, or `OPENROUTER_BASE_URL` unset in the shell that started the run; the per-cell validation quarantines such games — check the run's quarantine dir |
| spot instance vanished mid-run | it was reclaimed; results already on disk survive only if you rsynced — start a new instance, re-bootstrap, resume (the batch driver skips finished games) |
| bill higher than expected | something is still running: check EC2 console in **us-east-1** for running instances, and delete unused volumes/snapshots |
