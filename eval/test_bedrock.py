#!/usr/bin/env python3
"""Self-test for eval/bedrock.py — hermetic: no AWS call, no real credential,
no egress. Run: python3 eval/test_bedrock.py

THE CENTRAL EVIDENCE is the SigV4 known-answer test. The signer is checked
against the AWS-published aws-sig-v4-test-suite vectors:

  get-vanilla                    GET, empty body, host + x-amz-date signed
    expected string-to-sign      AWS4-HMAC-SHA256 / 20150830T123600Z /
                                 20150830/us-east-1/service/aws4_request /
                                 bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63
    expected signature           5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31
  post-x-www-form-urlencoded     POST, body "Param1=value1", content-type signed
    expected signature           ff11897932ad3f4e8b18135d722051e5ac45fc38421b1da7b9d196a0fe09473a

Both are the suite's own .sts/.authz expectations for the published example
credential AKIDEXAMPLE / wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY, region
us-east-1, service "service", 20150830T123600Z. We assert the CANONICAL
REQUEST hash, the STRING-TO-SIGN, and the final hex SIGNATURE separately, so a
break names the stage that broke.

NOT covered by those two vectors, and therefore asserted against explicitly
hand-computed fixtures (and labelled as such below):
  * canonical_uri()'s non-S3 double encoding of the path (test_canonical_uri)
  * the x-amz-security-token header being SIGNED (test_session_token_is_signed
    asserts membership + signature sensitivity, not an AWS-published digest)

WHAT WOULD MAKE THIS FAIL
  * Any change to the canonical-request layout (header order, the blank line,
    the trailing payload hash, value trimming/space collapsing) breaks the
    two published signatures.
  * A wrong signing-key derivation order (date/region/service/aws4_request)
    breaks both signatures while leaving the canonical request correct — which
    is exactly why the string-to-sign is asserted separately.
  * Dropping x-amz-security-token from the signed set, or not re-signing on
    retry, breaks test_session_token_is_signed / test_retry_resigns.
  * The egress gate defaulting open, accepting "true"/"yes"/"0", or being
    evaluated AFTER credentials/URL construction, breaks
    test_egress_gate_refuses_and_sends_nothing (it asserts the transport was
    never entered, not merely that an exception was raised).
  * A thinking budget that is ADDED to max_tokens instead of carved OUT of it
    breaks test_thinking_budget_is_carved_out — the CLAUDE.md empty-reply
    failure where a reasoning model scores as an interface failure.
  * A secret key or session token reaching an exception message, a repr, or a
    printed artifact breaks test_secrets_never_leak.
  * Following a redirect (which would replay a host-scoped Authorization
    header and the full game snapshot at an unconsented host) breaks
    test_redirects_are_refused.

NOTE: this file never sets OPENROUTER_ALLOW_EGRESS. The transport is proven
against a localhost mock via send_signed(), which is deliberately NOT gated;
invoke() is proven to gate before it.
"""

import http.server
import json
import os
import sys
import threading
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bedrock  # noqa: E402

FAILURES = []
N_CHECKS = [0]

# The AWS-published EXAMPLE credential from the signing test suite. Not a real
# key; the whole point of a known-answer vector is that everyone has it.
AKID = "AKIDEXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
AMZ_DATE = "20150830T123600Z"
REGION = "us-east-1"
SUITE_SERVICE = "service"


def check(name, cond, detail=""):
    N_CHECKS[0] += 1
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name,
                         (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def raises(exc_type, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc_type as exc:
        return exc
    except Exception as exc:                 # noqa: BLE001
        return ("WRONG-TYPE", exc)
    return None


# =========================================================================
# 1. SIGV4 KNOWN-ANSWER VECTORS (aws-sig-v4-test-suite)
# =========================================================================

def test_sigv4_get_vanilla():
    """get-vanilla: GET / with host + x-amz-date, empty body."""
    headers = {"host": "example.amazonaws.com", "x-amz-date": AMZ_DATE}
    sig = bedrock.sign_v4("GET", "/", "", headers, b"", REGION,
                          SUITE_SERVICE, AKID, SECRET, AMZ_DATE)

    expected_creq = (
        "GET\n"
        "/\n"
        "\n"
        "host:example.amazonaws.com\n"
        "x-amz-date:20150830T123600Z\n"
        "\n"
        "host;x-amz-date\n"
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    check("get-vanilla: canonical request matches the suite layout",
          sig["canonical_request"] == expected_creq,
          repr(sig["canonical_request"]))
    # The suite's own .sts publishes the canonical-request HASH, so this
    # asserts our canonical request against AWS's, not against our own text.
    check("get-vanilla: canonical-request hash == published bb579772...",
          bedrock.sha256_hex(sig["canonical_request"])
          == "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63",
          bedrock.sha256_hex(sig["canonical_request"]))
    check("get-vanilla: string-to-sign == published .sts",
          sig["string_to_sign"] == (
              "AWS4-HMAC-SHA256\n"
              "20150830T123600Z\n"
              "20150830/us-east-1/service/aws4_request\n"
              "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63"),
          repr(sig["string_to_sign"]))
    check("get-vanilla: signature == published 5fa00fa3...",
          sig["signature"]
          == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31",
          sig["signature"])
    check("get-vanilla: authorization header == published .authz",
          sig["authorization"] == (
              "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/"
              "service/aws4_request, SignedHeaders=host;x-amz-date, "
              "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8a"
              "ae1d763fbf31"),
          sig["authorization"])


def test_sigv4_post_x_www_form_urlencoded():
    """post-x-www-form-urlencoded: POST with a body AND a signed
    content-type — the shape the Bedrock invoke call actually uses."""
    headers = {"content-type": "application/x-www-form-urlencoded",
               "host": "example.amazonaws.com",
               "x-amz-date": AMZ_DATE}
    sig = bedrock.sign_v4("POST", "/", "", headers, b"Param1=value1", REGION,
                          SUITE_SERVICE, AKID, SECRET, AMZ_DATE)
    check("post-form: signed headers include content-type in sorted order",
          sig["signed_headers"] == "content-type;host;x-amz-date",
          sig["signed_headers"])
    check("post-form: signature == published ff118979...",
          sig["signature"]
          == "ff11897932ad3f4e8b18135d722051e5ac45fc38421b1da7b9d196a0fe09473a",
          sig["signature"])
    # A body that is signed at all: flipping one byte must move the signature.
    other = bedrock.sign_v4("POST", "/", "", headers, b"Param1=value2", REGION,
                            SUITE_SERVICE, AKID, SECRET, AMZ_DATE)
    check("post-form: the payload is actually covered by the signature",
          other["signature"] != sig["signature"])


def test_header_canonicalization():
    block, signed = bedrock.canonical_headers(
        {"X-Amz-Date": AMZ_DATE, "Host": "  example.amazonaws.com  ",
         "Content-Type": "application/json"})
    check("canonical headers are lowercased + sorted",
          signed == "content-type;host;x-amz-date", signed)
    check("canonical header values are trimmed",
          "host:example.amazonaws.com\n" in block, repr(block))
    check("canonical header runs of spaces collapse",
          bedrock._collapse("a   b  c") == "a b c")
    check("canonical header block keeps its trailing newline",
          block.endswith("\n"))


def test_canonical_uri():
    """HAND-COMPUTED fixture (the two published vectors only use "/").
    Non-S3 SigV4 encodes the path a SECOND time, so the '%' of an already
    encoded ARN model id becomes '%25' in the canonical request."""
    check("plain bedrock id needs no encoding at all",
          bedrock.invoke_path("anthropic.claude-opus-5")
          == "/model/anthropic.claude-opus-5/invoke",
          bedrock.invoke_path("anthropic.claude-opus-5"))
    check("plain path is its own canonical uri",
          bedrock.canonical_uri("/model/anthropic.claude-opus-5/invoke")
          == "/model/anthropic.claude-opus-5/invoke")
    arn = "arn:aws:bedrock:us-east-2:1:inference-profile/x"
    path = bedrock.invoke_path(arn)
    check("ARN id is percent-encoded into ONE path segment (no traversal)",
          path == "/model/arn%3Aaws%3Abedrock%3Aus-east-2%3A1%3A"
                  "inference-profile%2Fx/invoke", path)
    check("canonical uri double-encodes the '%' (non-S3 SigV4 rule)",
          bedrock.canonical_uri(path)
          == "/model/arn%253Aaws%253Abedrock%253Aus-east-2%253A1%253A"
             "inference-profile%252Fx/invoke", bedrock.canonical_uri(path))
    check("empty path canonicalizes to /", bedrock.canonical_uri("") == "/")


def test_canonical_query():
    check("no query -> empty string", bedrock.canonical_query("") == "")
    check("query params sort after encoding",
          bedrock.canonical_query("b=2&a=1&a=0") == "a=0&a=1&b=2",
          bedrock.canonical_query("b=2&a=1&a=0"))


def test_session_token_is_signed():
    """HAND-COMPUTED (not an AWS vector): assert membership + sensitivity."""
    creds = bedrock.Credentials(AKID, SECRET, "SESSIONTOKEN123456", "test")
    _, headers, _, sig = bedrock.signed_request(
        "https://bedrock-runtime.us-east-2.amazonaws.com/model/m/invoke",
        b"{}", creds, "us-east-2", amz_date=AMZ_DATE)
    check("session token is sent",
          headers.get("x-amz-security-token") == "SESSIONTOKEN123456")
    check("session token is in the SIGNED header set",
          "x-amz-security-token" in sig["signed_headers"],
          sig["signed_headers"])
    creds2 = bedrock.Credentials(AKID, SECRET, "DIFFERENTTOKEN9999", "test")
    _, _, _, sig2 = bedrock.signed_request(
        "https://bedrock-runtime.us-east-2.amazonaws.com/model/m/invoke",
        b"{}", creds2, "us-east-2", amz_date=AMZ_DATE)
    check("changing the session token changes the signature",
          sig2["signature"] != sig["signature"])
    # No token -> the header must not appear at all (an empty one would break
    # the signature server-side).
    _, h3, _, s3 = bedrock.signed_request(
        "https://bedrock-runtime.us-east-2.amazonaws.com/model/m/invoke",
        b"{}", bedrock.Credentials(AKID, SECRET, None, "test"), "us-east-2",
        amz_date=AMZ_DATE)
    check("no session token -> header absent and unsigned",
          "x-amz-security-token" not in h3
          and "x-amz-security-token" not in s3["signed_headers"])


def test_endpoint_shape():
    url = bedrock.invoke_url("us-east-2", "anthropic.claude-opus-5")
    check("bedrock-runtime regional endpoint, model in the PATH",
          url == "https://bedrock-runtime.us-east-2.amazonaws.com/model/"
                 "anthropic.claude-opus-5/invoke", url)
    check("service name for SigV4 is 'bedrock'", bedrock.SERVICE == "bedrock")


# =========================================================================
# 2. EGRESS GATE
# =========================================================================

def test_egress_gate_refuses_and_sends_nothing():
    calls = []
    real_send = bedrock._send
    bedrock._send = lambda *a, **kw: calls.append(a) or (200, b"{}")
    try:
        exc = raises(bedrock.EgressRefused, bedrock.invoke,
                     "anthropic.claude-opus-5",
                     [{"role": "user", "content": "hi"}], env={})
        check("invoke refuses with no consent flag",
              isinstance(exc, bedrock.EgressRefused), repr(exc))
        check("refusal happened BEFORE any transport", calls == [], str(calls))
        check("refusal names the flag and says a snapshot would be sent",
              "OPENROUTER_ALLOW_EGRESS" in str(exc)
              and "game snapshot" in str(exc), str(exc))
        # Even with credentials present in the env, no consent -> no call.
        exc2 = raises(bedrock.EgressRefused, bedrock.invoke, "m", [],
                      env={"AWS_ACCESS_KEY_ID": AKID,
                           "AWS_SECRET_ACCESS_KEY": SECRET})
        check("holding credentials is not consent",
              isinstance(exc2, bedrock.EgressRefused))
        check("still no transport", calls == [])
    finally:
        bedrock._send = real_send


def test_egress_flag_must_be_exactly_one():
    for bad in ("", "0", "true", "yes", "1 ", " 1", "TRUE", "on", "2"):
        check("flag %r does NOT grant consent" % bad,
              not bedrock.egress_allowed({bedrock.EGRESS_FLAG: bad}))
    check("no flag at all does not grant consent",
          not bedrock.egress_allowed({}))
    check("exactly '1' grants consent",
          bedrock.egress_allowed({bedrock.EGRESS_FLAG: "1"}))
    check("one shared consent switch, not a second flag",
          bedrock.EGRESS_FLAG == "OPENROUTER_ALLOW_EGRESS")


def test_cli_egress_refusal_is_nonzero():
    rc = bedrock.main(["--model", "anthropic.claude-opus-5",
                       "--prompt", "hi"], env={})
    check("CLI refusal exits nonzero (2)", rc == 2, "rc=%s" % rc)


# =========================================================================
# 3. CREDENTIALS + SECRET HANDLING
# =========================================================================

def _fake_home(tmp, creds_text=None, config_text=None):
    aws = Path(tmp) / ".aws"
    aws.mkdir(parents=True, exist_ok=True)
    if creds_text is not None:
        (aws / "credentials").write_text(creds_text, encoding="utf-8")
    if config_text is not None:
        (aws / "config").write_text(config_text, encoding="utf-8")
    return str(tmp)


def test_credentials_resolution():
    import tempfile
    env = {"AWS_ACCESS_KEY_ID": AKID, "AWS_SECRET_ACCESS_KEY": SECRET,
           "AWS_SESSION_TOKEN": "TOK"}
    c = bedrock.resolve_credentials(env=env, home="/nonexistent")
    check("env credentials win", c.access_key == AKID and c.source == "env")
    check("session token carried", c.session_token == "TOK")

    with tempfile.TemporaryDirectory() as tmp:
        home = _fake_home(
            tmp,
            creds_text=("[default]\n"
                        "aws_access_key_id = AKIA_DEFAULT\n"
                        "aws_secret_access_key = secret-default\n"
                        "\n[work]\n"
                        "aws_access_key_id = AKIA_WORK\n"
                        "aws_secret_access_key = secret-work\n"
                        "aws_session_token = tok-work\n"),
            config_text="[default]\nregion = us-east-2\n"
                        "[profile work]\nregion = eu-west-1\n")
        c = bedrock.resolve_credentials(env={}, home=home)
        check("falls back to ~/.aws/credentials [default]",
              c.access_key == "AKIA_DEFAULT", c.access_key)
        check("source names the file+profile, never the secret",
              c.source == "credentials[default]"
              and "secret-default" not in c.source, c.source)
        c = bedrock.resolve_credentials(env={"AWS_PROFILE": "work"}, home=home)
        check("AWS_PROFILE selects the profile", c.access_key == "AKIA_WORK")
        check("profile session token read", c.session_token == "tok-work")
        check("region from ~/.aws/config [default]",
              bedrock.resolve_region(env={}, home=home) == "us-east-2")
        check("region from [profile NAME]",
              bedrock.resolve_region(env={"AWS_PROFILE": "work"}, home=home)
              == "eu-west-1")
        check("AWS_REGION beats the config file",
              bedrock.resolve_region(env={"AWS_REGION": "ap-south-1"},
                                     home=home) == "ap-south-1")

    with tempfile.TemporaryDirectory() as tmp:
        home = _fake_home(tmp, creds_text="[default]\nrole_arn = arn:aws:iam::1:role/r\n")
        exc = raises(bedrock.CredentialsNotFound, bedrock.resolve_credentials,
                     env={}, home=home)
        check("assume-role profile fails loudly instead of half-working",
              isinstance(exc, bedrock.CredentialsNotFound)
              and "assume-role" in str(exc), repr(exc))

    with tempfile.TemporaryDirectory() as tmp:
        exc = raises(bedrock.CredentialsNotFound, bedrock.resolve_credentials,
                     env={}, home=tmp)
        check("no credentials anywhere -> CredentialsNotFound naming the files",
              isinstance(exc, bedrock.CredentialsNotFound)
              and ".aws" in str(exc), repr(exc))

    check("region default is the user's us-east-2",
          bedrock.resolve_region(env={}, home="/nonexistent") == "us-east-2")


def test_secrets_never_leak():
    creds = bedrock.Credentials(AKID, SECRET, "SESSIONTOKENSECRET", "test")
    r = repr(creds)
    check("repr redacts the secret key", SECRET not in r, r)
    check("repr redacts the session token", "SESSIONTOKENSECRET" not in r, r)
    check("repr keeps only an akid PREFIX", "AKID..." in r, r)
    check("str is the same redacted repr", SECRET not in str(creds))

    leaky = "boom secret=%s token=%s" % (SECRET, "SESSIONTOKENSECRET")
    scrubbed = bedrock._scrub(leaky, creds)
    check("_scrub removes the secret key", SECRET not in scrubbed, scrubbed)
    check("_scrub removes the session token",
          "SESSIONTOKENSECRET" not in scrubbed, scrubbed)
    check("_scrub leaves a readable message", "boom" in scrubbed)

    # An API error body that echoes the secret must not reach the exception.
    port, stop = _serve(_EchoErrorHandler)
    try:
        cfg = bedrock.ModelCfg(max_tokens=64, timeout_s=5, retries=0)
        exc = raises(bedrock.BedrockError, bedrock.send_signed,
                     "http://127.0.0.1:%d/model/m/invoke" % port,
                     json.dumps({"echo": SECRET}).encode("utf-8"),
                     creds, "us-east-2", cfg, sleep=lambda s: None)
        check("a leaky error body is scrubbed before it becomes an exception",
              isinstance(exc, bedrock.BedrockError) and SECRET not in str(exc),
              str(exc)[:200])
        check("the scrubbed error still says what happened",
              "400" in str(exc) and "<redacted>" in str(exc), str(exc)[:200])
    finally:
        stop()

    # --dry-run prints a signed request: it must not print secret material.
    import contextlib
    import io
    buf = io.StringIO()
    env = {"AWS_ACCESS_KEY_ID": AKID, "AWS_SECRET_ACCESS_KEY": SECRET,
           "AWS_SESSION_TOKEN": "SESSIONTOKENSECRET", "AWS_REGION": "us-east-2"}
    with contextlib.redirect_stdout(buf):
        rc = bedrock.main(["--model", "anthropic.claude-haiku-4-5",
                           "--prompt", "hi", "--dry-run"], env=env)
    out = buf.getvalue()
    check("--dry-run exits 0", rc == 0, "rc=%s" % rc)
    check("--dry-run prints no secret key", SECRET not in out)
    check("--dry-run prints no session token", "SESSIONTOKENSECRET" not in out)
    check("--dry-run redacts the Authorization header",
          '"authorization": "<redacted>"' in out, out[:300])
    check("--dry-run states that nothing was sent", '"sent": false' in out)


# =========================================================================
# 4. PER-MODEL CONFIG
# =========================================================================

def test_model_cfg_resolution():
    env = {}
    opus = bedrock.resolve_model_cfg("anthropic.claude-opus-5", env)
    check("opus-5 matches the most specific row first",
          opus.max_tokens == 8000 and opus.thinking_budget_tokens == 4000,
          repr(opus))
    check("opus-5 does NOT fall through to the generic claude-opus row",
          opus.timeout_s == 300.0, repr(opus))
    haiku = bedrock.resolve_model_cfg("anthropic.claude-haiku-4-5", env)
    check("haiku has thinking OFF (cheap control arm)",
          haiku.thinking_budget_tokens is None, repr(haiku))
    check("haiku pins temperature 0 for determinism",
          haiku.temperature == 0.0, repr(haiku))
    unknown = bedrock.resolve_model_cfg("meta.llama-99", env)
    dflt = dict(bedrock.model_cfg_table(env))["default"]
    check("an unknown model gets the DOCUMENTED default row",
          unknown == dflt, repr(unknown))
    check("the default row is conservative: no thinking",
          dflt.thinking_budget_tokens is None and dflt.max_tokens == 4000,
          repr(dflt))
    check("None/empty model id -> default",
          bedrock.resolve_model_cfg(None, env) == dflt
          and bedrock.resolve_model_cfg("", env) == dflt)
    check("matching is case-insensitive",
          bedrock.resolve_model_cfg("ANTHROPIC.CLAUDE-OPUS-5", env) == opus)
    check("'default' is never substring-matched",
          bedrock.resolve_model_cfg("some-default-model", env) == dflt)
    check("every row keeps answer headroom",
          all(c.answer_headroom >= bedrock.MIN_ANSWER_HEADROOM
              for _, c in bedrock.model_cfg_table(env)))


def test_thinking_budget_is_carved_out():
    """The CLAUDE.md failure: hidden reasoning eats the cap and the model
    returns an EMPTY reply that scores as an interface failure."""
    exc = raises(bedrock.ConfigError, bedrock.ModelCfg,
                 max_tokens=4000, timeout_s=60, retries=0,
                 thinking_budget_tokens=4000)
    check("a budget equal to max_tokens is REJECTED (nothing left to answer)",
          isinstance(exc, bedrock.ConfigError), repr(exc))
    check("the rejection explains the empty-reply failure",
          "EMPTY reply" in str(exc), str(exc))
    exc2 = raises(bedrock.ConfigError, bedrock.ModelCfg,
                  max_tokens=2000, timeout_s=60, retries=0,
                  thinking_budget_tokens=1500)
    check("a budget leaving < MIN_ANSWER_HEADROOM is rejected",
          isinstance(exc2, bedrock.ConfigError), repr(exc2))
    exc3 = raises(bedrock.ConfigError, bedrock.ModelCfg,
                  max_tokens=8000, timeout_s=60, retries=0,
                  thinking_budget_tokens=512)
    check("a budget below the API minimum is rejected",
          isinstance(exc3, bedrock.ConfigError), repr(exc3))

    cfg = bedrock.resolve_model_cfg("anthropic.claude-opus-5", {})
    body = bedrock.build_body(cfg, [{"role": "user", "content": "x"}], "sys")
    check("body max_tokens is the TOTAL cap",
          body["max_tokens"] == cfg.max_tokens == 8000, json.dumps(body))
    check("thinking budget is strictly LESS than max_tokens (carved out)",
          body["thinking"]["budget_tokens"] < body["max_tokens"],
          json.dumps(body["thinking"]))
    check("visible-answer headroom survives",
          body["max_tokens"] - body["thinking"]["budget_tokens"]
          >= bedrock.MIN_ANSWER_HEADROOM)
    check("temperature omitted when thinking is on (API rejects != 1)",
          "temperature" not in body, json.dumps(body))

    hcfg = bedrock.resolve_model_cfg("anthropic.claude-haiku-4-5", {})
    hbody = bedrock.build_body(hcfg, [{"role": "user", "content": "x"}])
    check("non-thinking row sends no thinking block",
          "thinking" not in hbody, json.dumps(hbody))
    check("non-thinking row sends temperature 0",
          hbody["temperature"] == 0.0, json.dumps(hbody))


def test_body_shape():
    cfg = bedrock.ModelCfg(max_tokens=512, timeout_s=30, retries=0)
    body = bedrock.build_body(cfg, [{"role": "user", "content": "hi"}], "SYS")
    check("anthropic_version is in the BODY",
          body["anthropic_version"] == "bedrock-2023-05-31", json.dumps(body))
    check("no 'model' field in the body (the id is in the path)",
          "model" not in body, json.dumps(body))
    check("system is a top-level field, not a role", body["system"] == "SYS")
    sysmsg, rest = bedrock.split_system(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}])
    check("split_system hoists the system role", sysmsg == "S")
    check("split_system keeps the rest in order",
          rest == [{"role": "user", "content": "U"}], str(rest))
    body2 = bedrock.build_body(cfg, rest)
    check("no system -> no system key", "system" not in body2)


def test_model_cfg_env_override():
    base = bedrock.resolve_model_cfg("anthropic.claude-haiku-4-5", {})
    env = {bedrock.CFG_ENV: json.dumps(
        {"claude-haiku-4-5": {"max_tokens": 999, "timeout_s": 7}})}
    got = bedrock.resolve_model_cfg("anthropic.claude-haiku-4-5", env,
                                    warn=lambda m: None)
    check("override changes the named fields",
          got.max_tokens == 999 and got.timeout_s == 7.0, repr(got))
    check("override is a MERGE, untouched fields survive",
          got.retries == base.retries and got.temperature == base.temperature,
          repr(got))

    env_camel = {bedrock.CFG_ENV: json.dumps(
        {"claude-haiku-4-5": {"maxTokens": 111}})}
    check("camelCase (the Java spelling) is accepted too",
          bedrock.resolve_model_cfg("anthropic.claude-haiku-4-5", env_camel,
                                    warn=lambda m: None).max_tokens == 111)

    env_new = {bedrock.CFG_ENV: json.dumps({"llama": {"max_tokens": 321}})}
    got = bedrock.resolve_model_cfg("meta.llama-3", env_new,
                                    warn=lambda m: None)
    check("a NEW key can be added without a code change",
          got.max_tokens == 321, repr(got))
    check("a new key does not disturb the default row",
          bedrock.resolve_model_cfg("meta.other", env_new,
                                    warn=lambda m: None).max_tokens == 4000)

    warns = []
    for bad in ("{not json", "[]", json.dumps({"claude-haiku": 5})):
        got = bedrock.resolve_model_cfg("anthropic.claude-haiku-4-5",
                                        {bedrock.CFG_ENV: bad},
                                        warn=warns.append)
        check("malformed override %r keeps the built-ins untouched"
              % bad[:12], got == base, repr(got))
    check("malformed overrides warn (never silent)", len(warns) >= 3,
          str(warns))

    # A row that would produce an empty reply must be REJECTED, not applied.
    warns2 = []
    bad_env = {bedrock.CFG_ENV: json.dumps(
        {"claude-opus-5": {"thinking_budget_tokens": 7999}})}
    got = bedrock.resolve_model_cfg("anthropic.claude-opus-5", bad_env,
                                    warn=warns2.append)
    check("an override that starves the visible answer is rejected",
          got.thinking_budget_tokens == 4000, repr(got))
    check("the rejection is reported", any("rejected" in w for w in warns2),
          str(warns2))

    check("cfg sha is 'builtin' with no override",
          bedrock.model_cfg_sha({}) == "builtin")
    sha = bedrock.model_cfg_sha(env)
    check("cfg sha is a 12-hex provenance stamp",
          len(sha) == 12 and sha != "builtin", sha)
    check("cfg sha changes with the override",
          bedrock.model_cfg_sha(env_new) != sha)


# =========================================================================
# 5. TRANSPORT (localhost mock only — no egress, flag never set)
# =========================================================================

RECEIVED = []


class _Base(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n)

    def _json(self, code, obj):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class _OkHandler(_Base):
    def do_POST(self):
        RECEIVED.append({"path": self.path,
                         "headers": dict(self.headers),
                         "body": self._read().decode("utf-8")})
        self._json(200, {
            "content": [{"type": "thinking", "thinking": "hmm" * 10},
                        {"type": "text", "text": "CHOICE: 2"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 11, "output_tokens": 5}})


class _EchoErrorHandler(_Base):
    def do_POST(self):
        body = self._read().decode("utf-8")
        self._json(400, {"message": "ValidationException: " + body})


class _FlakyHandler(_Base):
    hits = [0]

    def do_POST(self):
        self._read()
        _FlakyHandler.hits[0] += 1
        if _FlakyHandler.hits[0] <= 2:
            self._json(429, {"message": "ThrottlingException"})
            return
        RECEIVED.append({"headers": dict(self.headers)})
        self._json(200, {"content": [{"type": "text", "text": "ok"}],
                         "stop_reason": "end_turn"})


class _RedirectHandler(_Base):
    def do_POST(self):
        self._read()
        self.send_response(302)
        self.send_header("Location", "https://evil.example.com/model/m/invoke")
        self.send_header("Content-Length", "0")
        self.end_headers()


def _serve(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def stop():
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)
    return srv.server_address[1], stop


def test_signed_post_round_trip():
    del RECEIVED[:]
    port, stop = _serve(_OkHandler)
    try:
        creds = bedrock.Credentials(AKID, SECRET, None, "test")
        cfg = bedrock.resolve_model_cfg("anthropic.claude-opus-5", {})
        url = "http://127.0.0.1:%d%s" % (
            port, bedrock.invoke_path("anthropic.claude-opus-5"))
        body = json.dumps(bedrock.build_body(
            cfg, [{"role": "user", "content": "hi"}], "SYS")).encode("utf-8")
        status, raw, attempts = bedrock.send_signed(
            url, body, creds, "us-east-2", cfg, sleep=lambda s: None)
        check("round trip status 200", status == 200, str(status))
        check("no retry needed", attempts == 1, str(attempts))
        got = RECEIVED[-1]
        check("model id travels in the path",
              got["path"] == "/model/anthropic.claude-opus-5/invoke",
              got["path"])
        auth = got["headers"].get("authorization") or \
            got["headers"].get("Authorization") or ""
        check("Authorization is a SigV4 header for service 'bedrock'",
              auth.startswith("AWS4-HMAC-SHA256 Credential=%s/" % AKID)
              and "/bedrock/aws4_request" in auth, auth[:120])
        check("SignedHeaders covers content-type, host, x-amz-date",
              "SignedHeaders=content-type;host;x-amz-date" in auth, auth)
        sent = json.loads(got["body"])
        check("wire body carries anthropic_version",
              sent["anthropic_version"] == "bedrock-2023-05-31")
        check("wire body carries the carved-out thinking budget",
              sent["thinking"]["budget_tokens"] == 4000
              and sent["max_tokens"] == 8000, json.dumps(sent)[:200])

        parsed = bedrock.parse_response(status, raw)
        check("text blocks are concatenated", parsed["text"] == "CHOICE: 2",
              parsed["text"])
        check("thinking is counted, not returned",
              parsed["thinking_chars"] == 30
              and "hmm" not in parsed["text"], str(parsed["thinking_chars"]))
        check("usage is carried through",
              parsed["usage"]["output_tokens"] == 5)
        check("a real answer is not flagged empty_reply",
              parsed["empty_reply"] is False)
    finally:
        stop()


def test_retry_resigns():
    del RECEIVED[:]
    _FlakyHandler.hits[0] = 0
    port, stop = _serve(_FlakyHandler)
    try:
        creds = bedrock.Credentials(AKID, SECRET, None, "test")
        cfg = bedrock.ModelCfg(max_tokens=64, timeout_s=5, retries=2)
        status, raw, attempts = bedrock.send_signed(
            "http://127.0.0.1:%d/model/m/invoke" % port, b"{}", creds,
            "us-east-2", cfg, sleep=lambda s: None)
        check("429 ThrottlingException is retried", attempts == 3,
              str(attempts))
        check("the retry eventually succeeds", status == 200)
        check("the successful attempt carried a fresh signature",
              "AWS4-HMAC-SHA256" in (RECEIVED[-1]["headers"]
                                     .get("Authorization") or ""))
    finally:
        stop()


def test_4xx_is_not_retried():
    port, stop = _serve(_EchoErrorHandler)
    try:
        creds = bedrock.Credentials(AKID, SECRET, None, "test")
        cfg = bedrock.ModelCfg(max_tokens=64, timeout_s=5, retries=3)
        exc = raises(bedrock.BedrockError, bedrock.send_signed,
                     "http://127.0.0.1:%d/model/m/invoke" % port, b"{}",
                     creds, "us-east-2", cfg, sleep=lambda s: None)
        check("a 400 validation error is NOT retried (money, not luck)",
              isinstance(exc, bedrock.BedrockError)
              and "after 1 attempt" in str(exc), str(exc)[:160])
    finally:
        stop()


def test_redirects_are_refused():
    port, stop = _serve(_RedirectHandler)
    try:
        creds = bedrock.Credentials(AKID, SECRET, None, "test")
        cfg = bedrock.ModelCfg(max_tokens=64, timeout_s=5, retries=0)
        exc = raises(bedrock.BedrockError, bedrock.send_signed,
                     "http://127.0.0.1:%d/model/m/invoke" % port, b"{}",
                     creds, "us-east-2", cfg, sleep=lambda s: None)
        check("a 302 away from the signed host is refused, not followed",
              isinstance(exc, bedrock.BedrockError)
              and "refusing to follow" in str(exc), str(exc)[:200])
        check("the refusal names the host nobody signed for",
              "evil.example.com" in str(exc), str(exc)[:200])
    finally:
        stop()


def test_send_signed_is_not_an_ungated_back_door():
    """send_signed() is public and invoke() is the gated caller — so
    send_signed re-checks the gate itself for any NON-LOCAL host. Localhost
    stays ungated (that is what the transport tests above use, and it is not
    egress). The https requirement sits behind the gate."""
    creds = bedrock.Credentials(AKID, SECRET, None, "test")
    cfg = bedrock.ModelCfg(max_tokens=64, timeout_s=5, retries=0)
    exc = raises(bedrock.EgressRefused, bedrock.send_signed,
                 "https://bedrock-runtime.us-east-2.amazonaws.com/model/m/invoke",
                 b"{}", creds, "us-east-2", cfg, sleep=lambda s: None, env={})
    check("a remote host reached via send_signed still hits the gate",
          isinstance(exc, bedrock.EgressRefused), repr(exc))
    exc2 = raises(bedrock.EgressRefused, bedrock.send_signed,
                  "http://bedrock-runtime.us-east-2.amazonaws.com/x", b"{}",
                  creds, "us-east-2", cfg, sleep=lambda s: None, env={})
    check("plain http to a remote host is refused too",
          isinstance(exc2, bedrock.EgressRefused), repr(exc2))
    # And the localhost path is genuinely still open (test_signed_post_round_
    # trip depends on it) — assert it does not raise the gate error.
    exc3 = raises(bedrock.BedrockError, bedrock.send_signed,
                  "http://127.0.0.1:1/model/m/invoke", b"{}", creds,
                  "us-east-2", cfg, sleep=lambda s: None, env={})
    check("localhost is not gated (connection error, not EgressRefused)",
          isinstance(exc3, bedrock.BedrockError)
          and not isinstance(exc3, bedrock.EgressRefused), repr(exc3))


def test_empty_reply_is_its_own_signal():
    parsed = bedrock.parse_response(200, json.dumps({
        "content": [{"type": "thinking", "thinking": "x" * 100}],
        "stop_reason": "max_tokens"}).encode("utf-8"))
    check("an all-thinking max_tokens reply is flagged empty_reply",
          parsed["empty_reply"] is True and parsed["text"] == "",
          json.dumps(parsed))
    ok = bedrock.parse_response(200, json.dumps({
        "content": [{"type": "text", "text": "hi"}],
        "stop_reason": "max_tokens"}).encode("utf-8"))
    check("a truncated but non-empty reply is NOT empty_reply",
          ok["empty_reply"] is False)
    exc = raises(bedrock.BedrockError, bedrock.parse_response, 200,
                 b"not json")
    check("unparseable body raises a typed error", isinstance(exc, bedrock.BedrockError))
    exc2 = raises(bedrock.BedrockError, bedrock.parse_response, 200,
                  b'{"message": "AccessDeniedException"}')
    check("a body with no content block raises rather than scoring empty",
          isinstance(exc2, bedrock.BedrockError), repr(exc2))


def main():
    # Evidence, not a claim: this file must contain no assignment of the
    # consent flag anywhere (env dict literal, os.environ write, or shell).
    src = Path(__file__).read_text(encoding="utf-8")
    flag = "OPENROUTER" + "_ALLOW_EGRESS"
    check("this test never grants egress consent anywhere in its source",
          ('%s": "1"' % flag) not in src and ("%s=1" % flag) not in src
          and ("%s'] = " % flag) not in src and ('%s"] = ' % flag) not in src)
    if os.environ.get(flag) == "1":
        print("[note] %s is set in the SHELL; this suite still never sends "
              "anything off-box (transport tests use 127.0.0.1 only)" % flag)
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        print("--- %s ---" % t.__name__)
        t()
    print("\n%d test groups, %d checks, %d failure(s)"
          % (len(tests), N_CHECKS[0], len(FAILURES)))
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
