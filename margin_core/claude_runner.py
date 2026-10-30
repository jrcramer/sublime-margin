"""Run Claude Code non-interactively, one round per call.

Each document gets its own session id, so a conversation about a text can be
resumed over days: the first round uses ``--session-id <uuid>``, later rounds
``--resume <uuid>``.
"""

import json
import os
import subprocess
import uuid

COMMON_BIN_DIRS = [
    "~/.local/bin", "~/.claude/local", "~/.npm-global/bin", "~/.volta/bin",
    "/opt/homebrew/bin", "/usr/local/bin",
]


AUTH_HINT = ("Claude Code is not logged in (or its login expired). In a terminal, "
             "run `claude`, type /login, then try again.")


class ClaudeError(Exception):
    pass


def _env(extra_path):
    # extra_path first (the user's explicit choice), then the inherited PATH,
    # and only then the usual install dirs, so an old copy of claude in one of
    # those dirs does not shadow the one the terminal uses.
    env = dict(os.environ)
    dirs = [os.path.expanduser(d) for d in extra_path or []]
    dirs.append(env.get("PATH", ""))
    dirs += [os.path.expanduser(d) for d in COMMON_BIN_DIRS]
    env["PATH"] = os.pathsep.join(d for d in dirs if d)
    return env


def _is_auth_error(msg):
    m = msg.lower()
    return ("401" in m or "authenticat" in m or "/login" in m) and \
        ("token" in m or "authenticat" in m or "log in" in m or "login" in m)


def build_argv(settings, system_prompt, session_id, resume):
    argv = list(settings.get("claude_command") or ["claude"])
    argv += ["-p", "--output-format", "json", "--append-system-prompt", system_prompt]
    argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    tools = settings.get("claude_tools")
    if tools is not None:
        argv += ["--tools", ",".join(tools)]
        if tools:
            argv += ["--allowedTools", ",".join(tools)]
    if settings.get("model"):
        argv += ["--model", settings["model"]]
    argv += list(settings.get("claude_extra_args") or [])
    return argv


def run(settings, system_prompt, user_prompt, session_id=None, cwd=None, info=None):
    """Return (result_text, session_id). Raises ClaudeError.

    ``info``, if given, is filled with what Claude Code reports about the
    round: token usage, turns, and whether a new session had to be started.
    """
    resume = bool(session_id)
    sid = session_id or str(uuid.uuid4())
    info = {} if info is None else info
    try:
        return _run_once(settings, system_prompt, user_prompt, sid, resume, cwd, info)
    except ClaudeError as e:
        # A session made on another machine (or deleted) cannot be resumed:
        # start a fresh one; the sidecar still carries all decisions.
        if resume and "conversation" in str(e).lower() and "found" in str(e).lower():
            info["new_session"] = True
            return _run_once(settings, system_prompt, user_prompt, str(uuid.uuid4()), False, cwd, info)
        raise


def _run_once(settings, system_prompt, user_prompt, sid, resume, cwd, info):
    argv = build_argv(settings, system_prompt, sid, resume)
    try:
        proc = subprocess.run(
            argv, input=user_prompt.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd or None, env=_env(settings.get("extra_path")),
            timeout=settings.get("timeout", 600),
        )
    except FileNotFoundError:
        raise ClaudeError("Claude Code not found. Set 'claude_command' in Margin settings "
                          "(e.g. [\"/opt/homebrew/bin/claude\"]).")
    except subprocess.TimeoutExpired:
        raise ClaudeError("Claude did not answer within %ss." % settings.get("timeout", 600))
    out = proc.stdout.decode("utf-8", "replace").strip()
    err = proc.stderr.decode("utf-8", "replace").strip()
    data = None
    if out:
        try:
            data = json.loads(out.splitlines()[-1] if not out.startswith("{") else out)
        except ValueError:
            data = None
    if proc.returncode != 0 or data is None or data.get("is_error"):
        msg = (data or {}).get("result") or err or out or ("exit code %d" % proc.returncode)
        if _is_auth_error(msg):
            msg = AUTH_HINT + "\n\n" + msg
        raise ClaudeError(msg[:2000])
    info["usage"] = data.get("usage") or {}
    info["num_turns"] = data.get("num_turns")
    info["new_session"] = info.get("new_session", False) or not resume
    return data.get("result", ""), data.get("session_id") or sid
