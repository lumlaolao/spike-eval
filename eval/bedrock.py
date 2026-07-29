#!/usr/bin/env python3
"""bedrock.py — AWS Bedrock (Anthropic Messages API) backend for the eval
harness. STDLIB ONLY: SigV4 is hand-rolled with hmac/hashlib/urllib because
eval/ may not import boto3 or the anthropic SDK.

Usage:
  python3 eval/bedrock.py --print-cfg [MODEL]        # show the resolved table
  python3 eval/bedrock.py --whoami                   # credential SOURCE only
  python3 eval/bedrock.py --model anthropic.claude-opus-5 --prompt "hi" \
      [--region us-east-2] [--system TEXT] [--dry-run]

*** EGRESS BOUNDARY ***
Every call here ships the full reconstructed game snapshot (board, BOTH
players' hands, deck plan) to a third party. This module HARD-REFUSES unless
OPENROUTER_ALLOW_EGRESS=1 is set in the environment — the SAME single consent
switch the OpenRouter path uses, deliberately not a second flag, so there is
exactly one thing to audit. Holding AWS credentials is not consent. This
module never sets, defaults, or writes that variable, and the gate is PURE
(no socket is opened, no credential is read) and runs BEFORE any URL is
constructed. Refusal is exit 2 / EgressRefused.

The gate is enforced at TWO levels so there is no back door: invoke() gates
first, and send_signed() re-gates for any non-localhost host even if some
future caller reaches it directly. A 127.0.0.1 endpoint is not egress and
stays ungated — that is how the tests exercise the real signing + HTTP +
retry path without ever setting the consent flag.

*** SECRET HANDLING ***
The secret access key and the session token are never logged, never printed,
never written to an artifact, and never placed in an exception message: every
error string this module raises is passed through _scrub(). --whoami prints
the credential SOURCE and the access-key-id prefix only; --dry-run prints the
canonical request and string-to-sign with the Authorization value redacted.
Test fixtures use the AWS-published example key (AKIDEXAMPLE/wJalrXU...), a
documented test vector, never a real credential.

*** SIGV4 EVIDENCE ***
sign_v4() is checked against the AWS-published aws-sig-v4-test-suite
known-answer vectors in eval/test_bedrock.py — get-vanilla (GET, empty body)
and post-x-www-form-urlencoded (POST with a body and a signed content-type) —
asserting the canonical request, the string-to-sign, AND the final hex
signature against the suite's own .creq/.sts/.authz expectations. A signing
implementation with no known-answer test is not evidence that it works.
Not covered by those vectors and therefore only asserted against
hand-computed fixtures: path double-encoding (canonical_uri) and the
x-amz-security-token header, neither of which the two vectors exercise.

Exit codes: 0 OK; 1 transport/API error; 2 usage error or egress refusal.
"""

import argparse
import configparser
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

SERVICE = "bedrock"
ALGORITHM = "AWS4-HMAC-SHA256"
# Bedrock requires the anthropic_version IN THE BODY and the model id in the
# PATH (there is no "model" body field on this route).
ANTHROPIC_VERSION = "bedrock-2023-05-31"
DEFAULT_REGION = "us-east-2"
# ONE consent switch, shared with the OpenRouter path on purpose.
EGRESS_FLAG = "OPENROUTER_ALLOW_EGRESS"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
# Anthropic's documented floor for an extended-thinking budget.
MIN_THINKING_BUDGET = 1024
# Tokens that must remain for the VISIBLE answer after the thinking budget is
# carved out of max_tokens. See BEDROCK_MODEL_CFG's docstring: a budget that
# eats the whole cap surfaces as an EMPTY reply, which the eval would score as
# an interface failure rather than as a model that ran out of room.
MIN_ANSWER_HEADROOM = 1024

RETRYABLE_STATUS = frozenset((408, 409, 425, 429, 500, 502, 503, 504))


class BedrockError(Exception):
    """Transport or API failure (message already scrubbed of secrets)."""


class CredentialsNotFound(BedrockError):
    """No usable AWS credentials in the environment or ~/.aws."""


class EgressRefused(BedrockError):
    """The egress consent flag was not set. Nothing was sent."""


class ConfigError(BedrockError):
    """A model-config row is internally inconsistent."""


# ---------------------------------------------------------------------------
# EGRESS GATE — pure, no network, no credential read, runs first.
# ---------------------------------------------------------------------------

def egress_allowed(env=None):
    """True only when the single shared consent flag is exactly "1"."""
    env = os.environ if env is None else env
    return env.get(EGRESS_FLAG) == "1"


def enforce_egress(env=None):
    """Raise EgressRefused unless consent is explicitly granted. Pure."""
    if egress_allowed(env):
        return
    raise EgressRefused(
        "REFUSING to call AWS Bedrock without egress consent. Every call "
        "sends the full game snapshot (board, BOTH players' hands, deck "
        "plan) to a third-party API. Holding AWS credentials is not "
        "consent. Set %s=1 explicitly to opt in (see CLAUDE.md / "
        "secrets.env.example)." % EGRESS_FLAG)


# ---------------------------------------------------------------------------
# CREDENTIALS
# ---------------------------------------------------------------------------

class Credentials(object):
    """An AWS credential set. __repr__/__str__ REDACT the secret material —
    this object ends up inside exception context and debug prints."""

    __slots__ = ("access_key", "secret_key", "session_token", "source")

    def __init__(self, access_key, secret_key, session_token=None,
                 source="unknown"):
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token or None
        self.source = source

    @property
    def akid_prefix(self):
        """First 4 chars of the access key id — enough to tell two profiles
        apart in a log, not enough to be a credential."""
        return (self.access_key or "")[:4]

    def __repr__(self):
        return ("Credentials(access_key='%s...', secret_key=<redacted>, "
                "session_token=%s, source=%r)"
                % (self.akid_prefix,
                   "<redacted>" if self.session_token else "None",
                   self.source))

    __str__ = __repr__


def _scrub(text, creds=None):
    """Remove any secret material from a string before it is printed, logged,
    raised, or persisted. Defensive: the AWS error bodies we have seen never
    echo credentials, but an exception message is exactly the place a secret
    leaks from, so every error string this module emits goes through here."""
    s = "" if text is None else str(text)
    if creds is not None:
        for secret in (creds.secret_key, creds.session_token):
            if secret and len(secret) >= 8:
                s = s.replace(secret, "<redacted>")
    return s


def _aws_path(env, var, home, *parts):
    override = env.get(var)
    if override:
        return override
    return os.path.join(home, *parts)


def _read_ini(path):
    """Parse an AWS ini file. Returns an empty parser when absent/unreadable —
    a missing ~/.aws is a normal state, not an error."""
    cp = configparser.RawConfigParser()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cp.read_file(fh)
    except (OSError, configparser.Error):
        return configparser.RawConfigParser()
    return cp


def _section(cp, profile, config_style=False):
    """~/.aws/credentials uses [profile]; ~/.aws/config uses [profile NAME]
    except for [default]."""
    names = [profile]
    if config_style and profile != "default":
        names.insert(0, "profile %s" % profile)
    for name in names:
        if cp.has_section(name):
            return dict(cp.items(name))
    return {}


def resolve_credentials(env=None, home=None):
    """Env first (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
    AWS_SESSION_TOKEN), then ~/.aws/credentials, then ~/.aws/config, honouring
    AWS_PROFILE (default "default"). Raises CredentialsNotFound with a message
    that names the profile and the files checked but NEVER their contents.

    Deliberately NOT supported (boto3-only features that need a second network
    call or an external helper): assume-role via `role_arn`, SSO, and
    `credential_process`. Those raise CredentialsNotFound naming the reason
    rather than half-working."""
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home

    ak = env.get("AWS_ACCESS_KEY_ID")
    sk = env.get("AWS_SECRET_ACCESS_KEY")
    if ak and sk:
        return Credentials(ak, sk, env.get("AWS_SESSION_TOKEN"), "env")

    profile = env.get("AWS_PROFILE") or "default"
    cred_path = _aws_path(env, "AWS_SHARED_CREDENTIALS_FILE", home,
                          ".aws", "credentials")
    conf_path = _aws_path(env, "AWS_CONFIG_FILE", home, ".aws", "config")

    for path, config_style in ((cred_path, False), (conf_path, True)):
        row = _section(_read_ini(path), profile, config_style)
        ak = row.get("aws_access_key_id")
        sk = row.get("aws_secret_access_key")
        if ak and sk:
            return Credentials(ak, sk, row.get("aws_session_token"),
                               "%s[%s]" % (os.path.basename(path), profile))
        if row.get("role_arn") or row.get("sso_start_url") \
                or row.get("sso_session") or row.get("credential_process"):
            raise CredentialsNotFound(
                "AWS profile %r uses assume-role / SSO / credential_process, "
                "which this stdlib-only backend cannot resolve (that needs "
                "boto3). Export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
                "(and AWS_SESSION_TOKEN) instead, e.g. via `aws configure "
                "export-credentials --format env`." % profile)

    raise CredentialsNotFound(
        "no AWS credentials for profile %r: checked AWS_ACCESS_KEY_ID/"
        "AWS_SECRET_ACCESS_KEY in the environment, then %s, then %s."
        % (profile, cred_path, conf_path))


def resolve_region(env=None, home=None, default=DEFAULT_REGION):
    """AWS_REGION, then AWS_DEFAULT_REGION, then the profile's ~/.aws/config
    region, then `default`."""
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        if env.get(var):
            return env[var]
    profile = env.get("AWS_PROFILE") or "default"
    conf_path = _aws_path(env, "AWS_CONFIG_FILE", home, ".aws", "config")
    row = _section(_read_ini(conf_path), profile, True)
    return row.get("region") or default


# ---------------------------------------------------------------------------
# SIGV4 — https://docs.aws.amazon.com/IAM/latest/UserGuide/
#         reference_sigv-create-signed-request.html
# Every step below is a separate pure function so the known-answer vectors can
# assert the INTERMEDIATE artifacts (canonical request, string-to-sign), not
# just the final signature: when a signature mismatches, the failing step is
# what tells you why.
# ---------------------------------------------------------------------------

def sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data or b"").hexdigest()


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def canonical_uri(path):
    """The CanonicalURI field.

    SigV4 for every service EXCEPT S3 URI-encodes each path segment TWICE:
    the path as sent is already encoded once (we build it with
    quote(model_id, safe="")), and the canonical form encodes it again — so a
    '%' in the sent path becomes '%25' here. This mirrors botocore's
    SigV4Auth._normalize_url_path (quote(path, safe='/~')). For the plain
    Bedrock ids we send (anthropic.claude-opus-5 — letters, digits, '.', '-',
    all unreserved) both encodings are the identity and the canonical URI is
    the literal path; the double encoding only bites for ARN-style ids, which
    contain ':' and '/'. NOT exercised by the two known-answer vectors, so it
    is asserted separately against a hand-computed fixture."""
    if not path:
        return "/"
    return urllib.parse.quote(path, safe="/~")


def canonical_query(query):
    """The CanonicalQueryString: params URI-encoded, then sorted by
    (name, value). Bedrock's invoke route has no query string; implemented
    (and tested empty) so the signer is not silently wrong if one is added."""
    if not query:
        return ""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    encoded = sorted((urllib.parse.quote(k, safe="~"),
                      urllib.parse.quote(v, safe="~")) for k, v in pairs)
    return "&".join("%s=%s" % (k, v) for k, v in encoded)


def _collapse(value):
    """Trim, and collapse runs of spaces to one (SigV4 header-value rule)."""
    return " ".join(str(value).strip().split())


def canonical_headers(headers):
    """-> (canonical_headers_block, signed_headers). The block keeps its
    trailing newline, which is what makes the blank line in the canonical
    request when the fields are joined with '\\n'."""
    items = sorted((k.lower(), _collapse(v)) for k, v in headers.items())
    block = "".join("%s:%s\n" % (k, v) for k, v in items)
    signed = ";".join(k for k, _ in items)
    return block, signed


def build_canonical_request(method, path, query, headers, payload):
    """-> (canonical_request, signed_headers)."""
    block, signed = canonical_headers(headers)
    creq = "\n".join([
        method.upper(),
        canonical_uri(path),
        canonical_query(query),
        block,
        signed,
        sha256_hex(payload),
    ])
    return creq, signed


def credential_scope(datestamp, region, service):
    return "%s/%s/%s/aws4_request" % (datestamp, region, service)


def build_string_to_sign(amz_date, scope, canonical_request):
    return "\n".join([ALGORITHM, amz_date, scope,
                      sha256_hex(canonical_request)])


def derive_signing_key(secret_key, datestamp, region, service):
    key = ("AWS4" + secret_key).encode("utf-8")
    for part in (datestamp, region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def sign_v4(method, path, query, headers, payload, region, service,
            access_key, secret_key, amz_date):
    """The whole SigV4 computation as one pure function.

    `headers` must already contain EVERY header to be signed (host,
    x-amz-date, content-type, x-amz-security-token...). Returns a dict with
    the canonical_request / string_to_sign / signature / authorization so a
    known-answer test can assert each stage. Takes the raw secret and returns
    no secret material other than the signature itself."""
    datestamp = amz_date[:8]
    creq, signed = build_canonical_request(method, path, query, headers,
                                           payload)
    scope = credential_scope(datestamp, region, service)
    sts = build_string_to_sign(amz_date, scope, creq)
    signing_key = derive_signing_key(secret_key, datestamp, region, service)
    signature = hmac.new(signing_key, sts.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    authorization = ("%s Credential=%s/%s, SignedHeaders=%s, Signature=%s"
                     % (ALGORITHM, access_key, scope, signed, signature))
    return {
        "canonical_request": creq,
        "string_to_sign": sts,
        "signed_headers": signed,
        "credential_scope": scope,
        "signature": signature,
        "authorization": authorization,
    }


def utcnow_amz():
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# ENDPOINT
# ---------------------------------------------------------------------------

def bedrock_host(region):
    return "bedrock-runtime.%s.amazonaws.com" % region


def invoke_path(model_id):
    """The model id lives in the PATH and is URL-encoded (ARN-style ids
    contain ':' and '/'). quote(safe="") also makes path traversal
    impossible: a '/' inside a model id becomes %2F, never a path segment."""
    return "/model/%s/invoke" % urllib.parse.quote(model_id, safe="")


def invoke_url(region, model_id, scheme="https"):
    return "%s://%s%s" % (scheme, bedrock_host(region), invoke_path(model_id))


def signed_request(url, body, creds, region, service=SERVICE, amz_date=None,
                   content_type="application/json"):
    """-> (url, headers, body_bytes) ready for urlopen. `body` may be bytes or
    a str. Signs host, content-type, x-amz-date and (when present)
    x-amz-security-token."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    parts = urllib.parse.urlsplit(url)
    amz_date = amz_date or utcnow_amz()
    to_sign = {
        "host": parts.netloc,
        "content-type": content_type,
        "x-amz-date": amz_date,
    }
    if creds.session_token:
        to_sign["x-amz-security-token"] = creds.session_token
    sig = sign_v4("POST", parts.path, parts.query, to_sign, body, region,
                  service, creds.access_key, creds.secret_key, amz_date)
    headers = dict(to_sign)
    headers["authorization"] = sig["authorization"]
    headers["accept"] = "application/json"
    return url, headers, body, sig


# ---------------------------------------------------------------------------
# PER-MODEL CONFIG
#
# WHY THIS TABLE EXISTS (CLAUDE.md / LlmSpikeGui.ModelCfg): a reasoning model
# spends HIDDEN reasoning tokens out of the SAME max_tokens budget as its
# visible answer. When the budget is exhausted before the first visible token,
# the API returns stop_reason=max_tokens with an EMPTY content block — not a
# truncated answer. Downstream that parses as "no CHOICE line", i.e. an
# INTERFACE FAILURE, and a capable model gets scored as broken. One shared
# default silently does this to exactly the models the benchmark exists to
# rank. So: the thinking budget is carved OUT of max_tokens (never added to
# it) and MIN_ANSWER_HEADROOM tokens are guaranteed for the visible reply.
#
# Mirrors LlmSpikeGui.java's ModelCfg: an ordered substring-keyed table with
# an explicit "default" row, plus a JSON env override (BEDROCK_MODEL_CFG) so a
# model can be retuned without a code change. The override is ADDITIVE — it
# merges field-by-field onto the matching row (or onto "default" for an
# unknown key) and a malformed value warns once and leaves the built-ins
# untouched, exactly like the Java side.
# ---------------------------------------------------------------------------

CFG_ENV = "BEDROCK_MODEL_CFG"

_FIELD_ALIASES = {
    "maxtokens": "max_tokens",
    "max_tokens": "max_tokens",
    "timeouts": "timeout_s",
    "timeout_s": "timeout_s",
    "timeoutseconds": "timeout_s",
    "retries": "retries",
    "thinkingbudgettokens": "thinking_budget_tokens",
    "thinking_budget_tokens": "thinking_budget_tokens",
    "budgettokens": "thinking_budget_tokens",
    "temperature": "temperature",
}


class ModelCfg(object):
    """One row. `max_tokens` is the TOTAL cap (thinking + visible answer);
    `thinking_budget_tokens` is carved out of it, never added."""

    __slots__ = ("max_tokens", "timeout_s", "retries",
                 "thinking_budget_tokens", "temperature")

    def __init__(self, max_tokens, timeout_s, retries,
                 thinking_budget_tokens=None, temperature=0.0):
        self.max_tokens = int(max_tokens)
        self.timeout_s = float(timeout_s)
        self.retries = int(retries)
        self.thinking_budget_tokens = (None if thinking_budget_tokens is None
                                       else int(thinking_budget_tokens))
        self.temperature = (None if temperature is None else float(temperature))
        self.validate()

    def validate(self):
        if self.max_tokens < 1:
            raise ConfigError("max_tokens must be >= 1 (got %d)"
                              % self.max_tokens)
        if self.timeout_s <= 0:
            raise ConfigError("timeout_s must be > 0 (got %s)" % self.timeout_s)
        if self.retries < 0:
            raise ConfigError("retries must be >= 0 (got %d)" % self.retries)
        b = self.thinking_budget_tokens
        if b is not None:
            if b < MIN_THINKING_BUDGET:
                raise ConfigError(
                    "thinking_budget_tokens %d is below the API minimum %d"
                    % (b, MIN_THINKING_BUDGET))
            headroom = self.max_tokens - b
            if headroom < MIN_ANSWER_HEADROOM:
                raise ConfigError(
                    "thinking budget %d leaves only %d of max_tokens %d for "
                    "the VISIBLE answer (need >= %d): the budget is carved "
                    "OUT of max_tokens, and a model that spends the whole cap "
                    "on hidden reasoning returns an EMPTY reply, which this "
                    "eval scores as an interface failure."
                    % (b, headroom, self.max_tokens, MIN_ANSWER_HEADROOM))

    @property
    def answer_headroom(self):
        if self.thinking_budget_tokens is None:
            return self.max_tokens
        return self.max_tokens - self.thinking_budget_tokens

    def merge(self, fields):
        """-> a NEW row with `fields` (already normalized) applied on top."""
        vals = self.as_dict()
        vals.update(fields)
        return ModelCfg(**vals)

    def as_dict(self):
        return {
            "max_tokens": self.max_tokens,
            "timeout_s": self.timeout_s,
            "retries": self.retries,
            "thinking_budget_tokens": self.thinking_budget_tokens,
            "temperature": self.temperature,
        }

    def __repr__(self):
        return "ModelCfg(%s)" % json.dumps(self.as_dict(), sort_keys=True)

    def __eq__(self, other):
        return isinstance(other, ModelCfg) and self.as_dict() == other.as_dict()

    def __ne__(self, other):
        return not self.__eq__(other)


# Ordered MOST-SPECIFIC FIRST: forModel() takes the first key that is a
# substring of the (lowercased) model id, so "claude-opus-5" must be tried
# before "claude-opus". "default" is never substring-matched.
#
# Chosen values and why (report these with any run):
#   claude-opus-5 / claude-opus-4-8 — extended thinking ON. budget 4000 of a
#     8000 cap => 4000 tokens still reserved for the visible CHOICE line
#     (the reply contract needs ~20). Magic decisions are the case extended
#     thinking is for: counting lethal, sequencing, stack interaction.
#     timeout 300s because 4000 thinking tokens at realistic Bedrock decode
#     rates is minutes, and a slow-but-correct answer timing out is recorded
#     as a TRANSPORT error, which deflates a capable model's score.
#     temperature None (omitted): the Anthropic API REJECTS temperature != 1
#     when thinking is enabled, so the row must not carry 0.0.
#   claude-sonnet-5 — same shape, timeout 240s (faster decode).
#   claude-haiku-4-5 / claude-haiku — thinking OFF, 2000-token cap,
#     temperature 0.0 for determinism (matches replay_match's temperature 0).
#     Haiku is the cheap control arm; paying for a thinking budget on it
#     defeats the point of having a cheap arm.
#   default — the DOCUMENTED fallback for an unknown id: thinking OFF,
#     4000 tokens, 120s, 2 retries, temperature 0.0. Deliberately conservative
#     and NOT a thinking row: turning thinking on for a model that does not
#     support it is a hard 400 (a bad run), whereas leaving it off for a model
#     that does support it costs some quality but still produces a scoreable
#     answer. Retune with BEDROCK_MODEL_CFG, do not guess in code.
#   retries=2 everywhere — Bedrock throttles (429 ThrottlingException) under
#     the concurrency puzzle_run uses. Retries apply ONLY to 429/5xx and
#     transport errors; a 4xx validation error is never retried (it would
#     just re-burn the request).
def _builtin_table():
    thinking_big = dict(max_tokens=8000, retries=2,
                        thinking_budget_tokens=4000, temperature=None)
    rows = [
        ("claude-opus-5", ModelCfg(timeout_s=300, **thinking_big)),
        ("claude-opus-4-8", ModelCfg(timeout_s=300, **thinking_big)),
        ("claude-opus", ModelCfg(timeout_s=300, **thinking_big)),
        ("claude-sonnet-5", ModelCfg(timeout_s=240, **thinking_big)),
        ("claude-sonnet", ModelCfg(timeout_s=240, **thinking_big)),
        ("claude-haiku-4-5", ModelCfg(max_tokens=2000, timeout_s=120,
                                      retries=2, temperature=0.0)),
        ("claude-haiku", ModelCfg(max_tokens=2000, timeout_s=120,
                                  retries=2, temperature=0.0)),
        ("default", ModelCfg(max_tokens=4000, timeout_s=120, retries=2,
                             temperature=0.0)),
    ]
    return rows


BEDROCK_MODEL_CFG = _builtin_table()


def _normalize_override_fields(raw, warn):
    """camelCase or snake_case -> ModelCfg kwargs. Unknown keys warn and are
    ignored (additive schema rule: never crash on an extra field)."""
    out = {}
    for key, value in raw.items():
        canon = _FIELD_ALIASES.get(str(key).replace("-", "").replace("_", "")
                                   .lower())
        if canon is None:
            canon = _FIELD_ALIASES.get(str(key).lower())
        if canon is None:
            warn("%s: unknown field %r ignored" % (CFG_ENV, key))
            continue
        out[canon] = value
    return out


def model_cfg_table(env=None, warn=None):
    """The built-in table with any BEDROCK_MODEL_CFG override merged in.
    -> list of (key, ModelCfg) in match order. Never raises: a malformed
    override warns once and the built-ins are used untouched."""
    env = os.environ if env is None else env
    if warn is None:
        def warn(msg):
            print("[MODEL-CFG] %s" % msg, file=sys.stderr)

    rows = list(_builtin_table())
    raw = env.get(CFG_ENV)
    if not raw:
        return rows
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("top level must be an object")
    except (ValueError, TypeError) as exc:
        warn("failed to parse %s (%s); using built-in defaults untouched"
             % (CFG_ENV, exc))
        return rows

    index = dict((k, i) for i, (k, _) in enumerate(rows))
    default_row = rows[index["default"]][1]
    for key in sorted(obj):
        fields = obj[key]
        if not isinstance(fields, dict):
            warn("%s: row %r is not an object; ignored" % (CFG_ENV, key))
            continue
        lkey = str(key).lower()
        base = rows[index[lkey]][1] if lkey in index else default_row
        try:
            merged = base.merge(_normalize_override_fields(fields, warn))
        except (ConfigError, ValueError, TypeError) as exc:
            warn("%s: row %r rejected (%s); built-in row kept" % (CFG_ENV, key, exc))
            continue
        if lkey in index:
            rows[index[lkey]] = (lkey, merged)
        else:
            # A new key must be matched BEFORE "default" (which is never
            # substring-matched) but after the built-in specifics.
            rows.insert(len(rows) - 1, (lkey, merged))
            index[lkey] = len(rows) - 2
            index["default"] = len(rows) - 1
        warn("override applied for %r from %s" % (lkey, CFG_ENV))
    return rows


def resolve_model_cfg(model_id, env=None, warn=None):
    """First table key that is a substring of the lowercased model id; the
    explicit "default" row otherwise. Same rule as ModelCfg.forModel()."""
    rows = model_cfg_table(env, warn)
    mid = (model_id or "").lower()
    for key, cfg in rows:
        if key != "default" and key in mid:
            return cfg
    return dict(rows)["default"]


def model_cfg_sha(env=None):
    """sha256[:12] of the override JSON, or "builtin" when unset — the
    provenance stamp, mirroring the Java side's cfg sha."""
    env = os.environ if env is None else env
    raw = env.get(CFG_ENV)
    if not raw:
        return "builtin"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# REQUEST BODY (Anthropic Messages API, Bedrock flavour)
# ---------------------------------------------------------------------------

def split_system(messages):
    """OpenAI-shaped [{role,content}...] -> (system_text, non_system_msgs).
    Bedrock/Anthropic takes `system` as a top-level field, not a role."""
    system_parts = []
    rest = []
    for m in messages or []:
        if m.get("role") == "system":
            system_parts.append(m.get("content") or "")
        else:
            rest.append({"role": m.get("role") or "user",
                         "content": m.get("content") or ""})
    return "\n\n".join(p for p in system_parts if p), rest


def build_body(cfg, messages, system=None):
    """The Bedrock InvokeModel body. anthropic_version is REQUIRED here (the
    model id travels in the path, so there is no "model" field)."""
    body = {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": cfg.max_tokens,
        "messages": messages,
    }
    if system:
        body["system"] = system
    if cfg.thinking_budget_tokens is not None:
        # Carved OUT of max_tokens above; validate() guarantees the headroom.
        body["thinking"] = {"type": "enabled",
                            "budget_tokens": cfg.thinking_budget_tokens}
        # temperature is intentionally omitted: incompatible with thinking.
    elif cfg.temperature is not None:
        body["temperature"] = cfg.temperature
    return body


def extract_text(obj):
    """Concatenate the visible text blocks of a Messages response. Thinking
    blocks are counted, not returned: they are long, and the eval scores the
    visible answer."""
    text = []
    thinking_chars = 0
    for block in (obj.get("content") or []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text.append(block.get("text") or "")
        elif block.get("type") in ("thinking", "redacted_thinking"):
            thinking_chars += len(block.get("thinking") or "")
    return "".join(text), thinking_chars


# ---------------------------------------------------------------------------
# TRANSPORT
#
# _send() is the RAW transport and does NOT consult the egress gate; invoke()
# gates first and then calls it. That split is deliberate: it lets the tests
# exercise the real HTTP path end-to-end against a localhost mock WITHOUT ever
# setting the consent flag in a test environment.
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A signed request carries an Authorization header scoped to ONE host.
    Following a redirect would replay that header (and the full game snapshot)
    at a host nobody consented to and nobody signed for. Bedrock does not
    redirect; if something answers with one, that is the interesting event."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            "refusing to follow a %d redirect to %r: a SigV4-signed Bedrock "
            "request must not be replayed at another host"
            % (code, urllib.parse.urlsplit(newurl).netloc),
            headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect())


def _send(url, headers, body, timeout, opener=None):
    """One HTTP round trip. -> (status, response_bytes). Raises
    urllib.error.HTTPError / URLError / OSError."""
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    op = opener or _OPENER
    with op.open(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read()


def _retryable(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_STATUS
    return isinstance(exc, (urllib.error.URLError, OSError))


def send_signed(url, body, creds, region, cfg, opener=None, amz_date=None,
                sleep=time.sleep, env=None):
    """Sign and POST `body` to `url`, retrying cfg.retries times on 429/5xx
    and transport errors ONLY (a 4xx validation error is never retried — it
    would just re-burn the request). -> (status, raw_bytes, attempts).

    A LOCALHOST url is not egress and is allowed ungated — that is what lets
    the tests exercise the real signing + HTTP + retry path against a mock
    without ever setting the consent flag. Any OTHER host re-checks the gate
    here as well, so this function can never become an ungated back door if a
    future caller reaches it without going through invoke()."""
    local = urllib.parse.urlsplit(url).hostname in ("localhost", "127.0.0.1",
                                                    "::1")
    if not local:
        enforce_egress(env)
        if not url.lower().startswith("https://"):
            raise BedrockError(
                "refusing to sign a non-HTTPS Bedrock endpoint: %r" % url)
    for attempt in range(cfg.retries + 1):
        attempts = attempt + 1
        # Re-sign each attempt: x-amz-date must be fresh or a retry after a
        # long backoff can fall outside the 5-minute skew window.
        _, headers, payload, _sig = signed_request(
            url, body, creds, region, amz_date=amz_date)
        try:
            status, raw = _send(url, headers, payload, cfg.timeout_s, opener)
            return status, raw, attempts
        except Exception as exc:             # noqa: BLE001 — classified below
            if not _retryable(exc) or attempt >= cfg.retries:
                detail = _scrub(exc, creds)
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        detail = "%s %s: %s" % (
                            exc.code, exc.reason,
                            _scrub(exc.read().decode("utf-8", "replace"),
                                   creds))
                    except Exception:        # noqa: BLE001
                        pass
                raise BedrockError(
                    "Bedrock invoke failed after %d attempt(s): %s"
                    % (attempts, detail))
            sleep(0.5 * (2 ** attempt))
    raise BedrockError("unreachable")        # pragma: no cover


def parse_response(status, raw, creds=None):
    """Anthropic Messages response bytes -> the harness result dict. Pure, so
    the response contract is tested without any network."""
    try:
        obj = json.loads(raw.decode("utf-8", "replace")
                         if isinstance(raw, bytes) else raw)
    except ValueError as exc:
        raise BedrockError("Bedrock returned unparseable JSON (status %s): %s"
                           % (status, _scrub(exc, creds)))
    if not isinstance(obj, dict) or "content" not in obj:
        raise BedrockError(
            "Bedrock response has no content block (status %s): %s"
            % (status, _scrub(json.dumps(obj)[:400], creds)))
    text, thinking_chars = extract_text(obj)
    stop_reason = obj.get("stop_reason")
    return {
        "text": text,
        "stop_reason": stop_reason,
        "usage": obj.get("usage") or {},
        "thinking_chars": thinking_chars,
        # THE failure mode this whole per-model table exists to prevent: the
        # cap was spent (largely on hidden reasoning) before a visible token.
        # Surfaced as its own field so it is never silently laundered into
        # "the model gave an unparseable answer".
        "empty_reply": (text == "" and stop_reason == "max_tokens"),
    }


def invoke(model_id, messages, system=None, region=None, creds=None,
           cfg=None, env=None, endpoint=None, opener=None, amz_date=None,
           sleep=time.sleep):
    """Call Bedrock and return {text, stop_reason, usage, thinking_chars,
    empty_reply, latency_ms, attempts}. EGRESS-GATED: raises EgressRefused
    BEFORE resolving credentials, reading ~/.aws, or constructing a URL."""
    env = os.environ if env is None else env
    enforce_egress(env)                      # first, pure, no socket

    region = region or resolve_region(env)
    creds = creds or resolve_credentials(env)
    cfg = cfg or resolve_model_cfg(model_id, env)
    url = endpoint or invoke_url(region, model_id)
    body = json.dumps(build_body(cfg, messages, system)).encode("utf-8")

    t0 = time.time()
    status, raw, attempts = send_signed(url, body, creds, region, cfg,
                                        opener=opener, amz_date=amz_date,
                                        sleep=sleep, env=env)
    result = parse_response(status, raw, creds)
    result["latency_ms"] = int((time.time() - t0) * 1000)
    result["attempts"] = attempts
    return result


def complete(model_id, messages, **kwargs):
    """invoke() reduced to the visible reply text."""
    return invoke(model_id, messages, **kwargs)["text"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_cfg(model_id, env):
    rows = model_cfg_table(env)
    out = {
        "model_cfg_sha": model_cfg_sha(env),
        "table": [{"key": k, "cfg": c.as_dict(),
                   "answer_headroom": c.answer_headroom} for k, c in rows],
    }
    if model_id:
        cfg = resolve_model_cfg(model_id, env)
        out["resolved"] = {"model": model_id, "cfg": cfg.as_dict(),
                           "answer_headroom": cfg.answer_headroom}
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="anthropic.claude-opus-5")
    ap.add_argument("--prompt", help="user message to send")
    ap.add_argument("--system", default=None)
    ap.add_argument("--region", default=None)
    ap.add_argument("--print-cfg", nargs="?", const="", metavar="MODEL",
                    help="print the resolved model-config table and exit")
    ap.add_argument("--whoami", action="store_true",
                    help="print the credential SOURCE (never the secret)")
    ap.add_argument("--dry-run", action="store_true",
                    help="sign and print the request; send NOTHING")
    ap.add_argument("--json", action="store_true",
                    help="print the full result object")
    return ap


def main(argv=None, env=None):
    args = build_parser().parse_args(argv)
    env = os.environ if env is None else env

    if args.print_cfg is not None:
        return _print_cfg(args.print_cfg or args.model, env)

    region = args.region or resolve_region(env)

    if args.whoami:
        try:
            creds = resolve_credentials(env)
        except CredentialsNotFound as exc:
            print("credentials: NOT FOUND — %s" % exc, file=sys.stderr)
            return 1
        print(json.dumps({"source": creds.source,
                          "access_key_prefix": creds.akid_prefix,
                          "has_session_token": bool(creds.session_token),
                          "region": region,
                          "egress_consent": egress_allowed(env)},
                         sort_keys=True))
        return 0

    if not args.prompt:
        print("--prompt is required (or use --print-cfg / --whoami)",
              file=sys.stderr)
        return 2

    messages = [{"role": "user", "content": args.prompt}]
    cfg = resolve_model_cfg(args.model, env)

    if args.dry_run:
        # Signs locally and sends NOTHING, so it does not need consent.
        try:
            creds = resolve_credentials(env)
        except CredentialsNotFound as exc:
            print(str(exc), file=sys.stderr)
            return 1
        url = invoke_url(region, args.model)
        body = json.dumps(build_body(cfg, messages, args.system)).encode("utf-8")
        _, headers, _, sig = signed_request(url, body, creds, region)
        redacted = dict(headers)
        redacted["authorization"] = "<redacted>"
        if "x-amz-security-token" in redacted:
            redacted["x-amz-security-token"] = "<redacted>"
        print(json.dumps({
            "url": url,
            "headers": redacted,
            "signed_headers": sig["signed_headers"],
            "credential_scope": sig["credential_scope"],
            # The canonical request contains every SIGNED header value
            # verbatim — including x-amz-security-token. Scrub before print.
            "canonical_request": _scrub(sig["canonical_request"], creds),
            "string_to_sign": _scrub(sig["string_to_sign"], creds),
            "body_bytes": len(body),
            "cfg": cfg.as_dict(),
            "sent": False,
        }, indent=1, sort_keys=True))
        return 0

    try:
        result = invoke(args.model, messages, system=args.system,
                        region=region, cfg=cfg, env=env)
    except EgressRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except BedrockError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["text"])
        if result["empty_reply"]:
            print("[warn] EMPTY reply with stop_reason=max_tokens: the token "
                  "cap was spent before a visible token (raise max_tokens or "
                  "lower thinking_budget_tokens via %s)" % CFG_ENV,
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
