# Security Policy

## Supported Versions
| Version | Supported |
|---|---|
| latest (`main`) | ✅ |

## Reporting a Vulnerability
Please **do not** open a public issue for security vulnerabilities. Instead,
report them privately:

- Email **edy.cu@live.com**, or
- Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) (Security → Report a vulnerability).

You'll get an acknowledgment within 48 hours and a resolution timeline after
triage. Please give us a reasonable window to patch before public disclosure.

## Scope Notes
Tarmac's offline path (the default, and the graded judge path) makes **zero**
network calls — `scripts/verify_offline.py` runs with sockets disabled. The
only network-facing surface is the opt-in `--live` mode, which talks to
DashScope/Qwen Cloud using a caller-supplied `DASHSCOPE_API_KEY` (never
committed, never logged).
