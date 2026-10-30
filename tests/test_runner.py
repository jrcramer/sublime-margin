import stat

from margin_core import claude_runner as cr


def fake_claude(tmp_path, body):
    p = tmp_path / "claude"
    p.write_text("#!/usr/bin/env python3\nimport sys, json\nargs = sys.argv[1:]\n"
                 "prompt = sys.stdin.read()\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_argv_first_round_and_resume():
    s = {"claude_command": ["claude"], "claude_tools": ["WebFetch"], "model": "opus"}
    a = cr.build_argv(s, "SYS", "abc", False)
    assert a[:4] == ["claude", "-p", "--output-format", "json"]
    assert "--session-id" in a and "--resume" not in a
    assert a[a.index("--tools") + 1] == "WebFetch" and "--allowedTools" in a
    assert a[a.index("--model") + 1] == "opus"
    b = cr.build_argv(s, "SYS", "abc", True)
    assert b[b.index("--resume") + 1] == "abc"


def test_run_returns_result_and_session(tmp_path):
    exe = fake_claude(tmp_path,
        "sid = args[args.index('--session-id') + 1]\n"
        "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'echo:' + prompt, 'session_id': sid}))\n")
    text, sid = cr.run({"claude_command": [exe]}, "SYS", "hallo")
    assert text == "echo:hallo" and len(sid) == 36


def test_run_falls_back_when_session_missing(tmp_path):
    exe = fake_claude(tmp_path,
        "if '--resume' in args:\n"
        "    print(json.dumps({'is_error': True, 'result': 'No conversation found with session ID x'})); sys.exit(1)\n"
        "print(json.dumps({'is_error': False, 'result': 'fresh', 'session_id': args[args.index('--session-id')+1]}))\n")
    text, sid = cr.run({"claude_command": [exe]}, "SYS", "q", session_id="old-session")
    assert text == "fresh" and sid != "old-session"


def test_run_error_is_raised(tmp_path):
    exe = fake_claude(tmp_path, "sys.stderr.write('boom'); sys.exit(2)\n")
    try:
        cr.run({"claude_command": [exe]}, "SYS", "q")
    except cr.ClaudeError as e:
        assert "boom" in str(e)
    else:
        raise AssertionError("expected ClaudeError")


def test_missing_binary_message():
    try:
        cr.run({"claude_command": ["/nonexistent/claude"]}, "S", "q")
    except cr.ClaudeError as e:
        assert "claude_command" in str(e)


def test_auth_error_gets_login_hint(tmp_path):
    exe = fake_claude(tmp_path,
        "print('Failed to authenticate. API Error: 401 {\"type\":\"error\",\"error\":"
        "{\"type\":\"authentication_error\",\"message\":\"OAuth access token is invalid.\"}}')\n"
        "sys.exit(1)\n")
    try:
        cr.run({"claude_command": [exe]}, "S", "q")
    except cr.ClaudeError as e:
        assert "/login" in str(e) and "401" in str(e)
    else:
        raise AssertionError("expected ClaudeError")


def test_inherited_path_wins_over_common_dirs(monkeypatch):
    monkeypatch.setenv("PATH", "/my/bin")
    parts = cr._env(["/extra"])["PATH"].split(":")
    assert parts[0] == "/extra" and parts[1] == "/my/bin"
    assert parts.index("/my/bin") < parts.index("/opt/homebrew/bin")
