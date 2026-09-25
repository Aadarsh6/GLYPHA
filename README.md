<p align="center">
<pre>
 ███  █     █   █ ████  █   █  ███ 
█     █     █   █ █   █ █   █ █   █
█  ██ █      █ █  ████  █████ █████
█   █ █       █   █     █   █ █   █
 ███  █████   █   █     █   █ █   █
</pre>
</p>

<p align="center">
<b>ENCRYPTED · PEER-TO-PEER · LOCAL-FIRST</b><br>
<sub>sockets up · keys local · relay when NATs say no</sub>
</p>

<p align="center">
<a href="https://pypi.org/project/glypha/"><img src="https://img.shields.io/pypi/v/glypha.svg?color=blue" alt="PyPI"></a>
<a href="https://pypi.org/project/glypha/"><img src="https://img.shields.io/pypi/pyversions/glypha.svg" alt="Python Versions"></a>
<a href="https://pypi.org/project/glypha/"><img src="https://img.shields.io/pypi/dm/glypha.svg" alt="PyPI Downloads"></a>
<a href="#testing"><img src="https://img.shields.io/badge/tests-22%20passing-brightgreen.svg" alt="Tests"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-informational.svg" alt="License: MIT"></a>
<a href="https://github.com/Aadarsh6/GLYPHA/stargazers"><img src="https://img.shields.io/github/stars/Aadarsh6/GLYPHA?style=social" alt="Stars"></a>
</p>

<p align="center">
<a href="#quick-start">Quick Start</a> ·
<a href="#how-it-works">How It Works</a> ·
<a href="#the-nat-experiment">The NAT Experiment</a> ·
<a href="#security-model">Security</a> ·
<a href="#faq">FAQ</a>
</p>

---

## Why Glypha Exists

Most "build a chat app" tutorials hand you WebRTC or a messaging SDK and never explain what's underneath. Glypha goes the other direction: it implements the entire peer-to-peer stack by hand — message framing, encryption, identity, discovery, NAT traversal, relay — and documents every decision, including the failures.

> ⚠️ **Educational project — deliberately not Signal.** See the [security model](#security-model) for what that means, stated honestly.

## Table of Contents

- [Why Glypha Exists](#why-glypha-exists)
- [Features](#features)
- [Quick Start](#quick-start)
- [Screenshots](#screenshots)
- [How It Works](#how-it-works)
- [The NAT Experiment](#the-nat-experiment)
- [The Night the Internet Attacked](#the-night-the-internet-attacked)
- [Self-Hosting the Infrastructure](#self-hosting-the-infrastructure)
- [Security Model](#security-model)
- [Testing](#testing)
- [Repository Layout](#repository-layout)
- [Version History](#version-history)
- [Roadmap](#roadmap)
- [FAQ](#faq)
- [Contributing](#contributing)
- [License](#license)

## Features

| | Detail |
|---|---|
| 🔒 **End-to-end encryption** | X25519 key exchange + XSalsa20-Poly1305 AEAD (libsodium, via PyNaCl). Every message is ciphertext end to end — including when it passes through the relay. |
| 🪪 **Persistent identity** | A stable keypair per peer, stored in `~/.glypha/`. Your fingerprint — SHA-256 of your public key — is who you are. IPs change; identity doesn't. |
| 🤝 **Verify once, then zero prompts** | First contact: compare fingerprints out of band (SSH-style TOFU). Then it's pinned locally — every later session auto-verifies, and a key change triggers a loud impersonation warning. |
| 💬 **A real chat UI** | Outlined message bubbles — yours right, theirs left — a large block-glyph wordmark, dim corner timestamps, and slash commands (`/status`, `/whoami`, `/history`). |
| 📜 **Encrypted history** | SQLite stores only SecretBox ciphertext, under a key separate from your identity key. `/history` replays it grouped by sender and day. |
| 🌐 **Discovery by name** | A rendezvous server maps name → endpoint + fingerprint. Self-hostable with one command. |
| 🧗 **NAT traversal, measured honestly** | The chat ladder tries direct → coordinated TCP punch → relay. The punch's real-world failure on carrier-grade NAT is measured and documented — not hidden. |
| 🛡️ **Hardened framing** | Length-prefixed frames with a hard 1 MiB cap — hostile headers are rejected, not buffered. This isn't theoretical; it was attacked in production. |
| 🧪 **Tested** | 22 pytest tests over framing, storage, and identity — including regressions for every real bug found during the build. |

## Quick Start

Install:

```bash
pip install glypha
```

Point it at a rendezvous (any machine running one — a friend's, or yours, see [Self-Hosting](#self-hosting-the-infrastructure)). You only do this once:

```bash
glypha config glypha-relay.duckdns.org     # or any rendezvous host/IP
```

Two people chat (any networks, any distance):

| Alice | Bob |
|---|---|
| `glypha chat alice bob` | `glypha chat bob alice` |

Both sides show fingerprints. Compare them on a call and confirm with `yes` — that's the one-time human verification. You're now in an end-to-end-encrypted chat, and both of you have pinned each other's identity: reconnecting later needs no prompts at all.

What you'll see:

```
• reaching 'bob' via glypha-relay.duckdns.org...
• trying direct to 203.0.113.7:34059...
• direct failed — trying NAT traversal...
• traversal failed — falling back to relay...
● 'bob' verified against pinned key
● connected via relay
```

Then bubbles: yours on the right, theirs on the left. Type `quit` or `/quit` to leave. History replays compactly on reconnect; `/history` shows more; `/status` shows the full connection details.

Same-LAN mode (no server needed):

```bash
glypha listen 9999 alice             # PC A prints the exact command to run
glypha connect 192.168.1.6 9999 bob  # PC B dials directly
```

## Screenshots

<p align="center">
  <img src="screenshots/wordmark-chat.png" width="640" alt="GLYPHA wordmark and chat bubbles"><br>
  <sub>Wordmark + chat</sub>
</p>

<p align="center">
  <img src="screenshots/status-panel.png" width="640" alt="GLYPHA /status panel"><br>
  <sub>/status panel</sub>
</p>

<p align="center">
  <img src="screenshots/relay-log.png" width="640" alt="GLYPHA relay log"><br>
  <sub>Relay log</sub>
</p>

*(Placeholders — drop the three real captures into `screenshots/` and these will render automatically.)*

## How It Works

The connection ladder — `chat` tries each rung in order; the user never chooses a transport and never sees a traceback:

```
glypha chat <me> <peer>
        │
        ▼
1. DIRECT ────────── dial the endpoint the rendezvous published
        │ fails      (works on LAN / public endpoints)
        ▼
2. PUNCH ─────────── coordinated simultaneous open, both peers dialing
        │ fails      each other's NAT-mapped endpoints
        ▼            (works on friendly NATs)
3. RELAY ─────────── both peers dial OUT to a public relay, which splices
        │            the two connections into a byte-pipe (always works)
        ▼
   encrypted chat
```

Topology:

```
              ┌──────────────────────┐
              │   Rendezvous Server  │   names → endpoints + fingerprints
              └──────────┬───────────┘
                         │
                   ↙            ↘
              ┌────────┐      ┌────────┐
              │ Peer A │◄────►│ Peer B │   direct, when possible…
              └────────┘      └────────┘
                   │                │
                   └──► RELAY ◄─────┘      …ciphertext through a blind pipe
                                            when NATs refuse
```

The transport stack — every layer implemented, not imported:

```
chat & history           bubbles, slash commands
        ↓
peer management          the auto-fallback ladder
        ↓
cryptographic identity   persistent keypairs, fingerprints, pins
        ↓
PyNaCl Box                X25519 + XSalsa20-Poly1305
        ↓
custom framing             [4-byte big-endian length][payload], 1 MiB cap
        ↓
TCP sockets                 blocking, threaded receive
        ↓
IP / NAT / firewall
```

**The key insight — observed vs. advertised endpoints.** A peer's reachable address is half observed, half advertised: the rendezvous sees your public IP as your connection arrives (you can't lie about it), while only you know which local port accepts chat (the server can't know it). Composing the two is discovery done right — and is exactly the mechanism that later reveals your NAT's public mapping for the traversal attempt.

**The relay carries without reading.** It copies opaque bytes between two outbound connections — no parsing, no decryption, no keys. It doesn't even have a crypto library installed. Its entire log of your conversation is one line: `[relay] spliced alice <-> bob`.

Identity & verification (SSH `known_hosts`, for people):

| Event | What happens |
|---|---|
| First contact | Both screens show fingerprints; humans compare out of band; on confirmation the peer is pinned locally |
| Every later contact | Auto-verified against the pin — no prompts |
| Key change on a pinned name | Loud warning + reject — possible impersonation |

The rendezvous's published fingerprint is a cross-check only — never a trust source.

## The NAT Experiment

Glypha's traversal story is an honest engineering result, not a claim.

**Setup:** rendezvous + relay on a cloud VM (Central India). Peer A on home Wi-Fi (NAT public `122.162.151.183`). Peer B on a mobile hotspot, behind carrier-grade NAT (`157.49.119.123` — one public IP shared across thousands of subscribers).

| Attempt | Result |
|---|---|
| Uncoordinated direct dial across the Internet | ❌ `WinError 10060` — NAT drops the unsolicited inbound SYN |
| Control: simultaneous open on loopback | ✅ punched through — the mechanics are correct |
| Real: coordinated punch, home NAT ↔ carrier-grade NAT | ❌ neither NAT delivered the peer's SYNs |

**Conclusion:** the code is right (control passes); the networks refuse. Hence rung 3, the relay, which requires only the outbound connections every NAT permits. Failure measured, explained at the NAT level, designed around — the project's thesis in one experiment.

## The Night the Internet Attacked

~48 hours after Glypha was published to PyPI, internet scanners found the rendezvous server (public IP, port 7000) and began sending hostile length headers — the exact flaw that had been documented and deferred since V1 with the trigger "fix when remotely reachable." The trigger fired on schedule: unpatched receivers attempted multi-gigabyte allocations (`MemoryError` per connection thread).

The response was already written: `MAX_MESSAGE_SIZE` (1 MiB cap, reject = close, zero allocation). Deployed to the servers, the attack became silent drops. Shipped to every client in the next release. This is why the framing section says the cap isn't theoretical — and why the incident, with before/after logs, lives in the project's engineering log (`project.md`).

## Self-Hosting the Infrastructure

Glypha's servers ship inside the package. On any public machine:

```bash
pip install glypha
glypha-rendezvous     # name directory, port 7000, 90s TTL + 30s refresh
glypha-relay          # blind byte-splice, port 7001
```

**Making the host name stable** — the rendezvous needs a publicly reachable address, and cloud IPs can change on deallocate. A free dynamic-DNS name solves it:

1. Sign in at [duckdns.org](https://www.duckdns.org) → add a domain (e.g. `glypha-relay` → `glypha-relay.duckdns.org`).
2. On the server machine, keep it updated (crontab, every 5 minutes):
   ```bash
   echo 'curl -s "https://www.duckdns.org/update?domains=glypha-relay&token=YOUR_TOKEN&ip="' > ~/duckdns/duck.sh
   chmod +x ~/duckdns/duck.sh
   ( crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1" ) | crontab -
   ```
3. Both peers run `glypha config glypha-relay.duckdns.org` once — after that, `glypha chat <me> <peer>` with no IP, ever.

> Cloud deployment needs the ports open at both firewall layers — the provider's NSG/security group and the OS's `ufw`. One command tests both.

## Security Model

**Protects against:**

- Passive network observers — messages are AEAD-encrypted end to end
- Peer impersonation across reconnects — persistent identities, out-of-band fingerprint verification, and pin-based key-change detection
- Plaintext history at rest — SQLite holds only ciphertext, under a key separate from the identity key
- IP reassignment redefining who someone is — identity is the fingerprint
- Hostile framing — oversized/illegal headers rejected before allocation

**Does not protect against:**

- No forward secrecy — static-static ECDH; a later private-key compromise can decrypt recorded past traffic (no ratchet; that's Signal's territory)
- Stolen key files — keys live unencrypted on disk
- Replay — no counters/nonces yet
- A malicious rendezvous — can serve wrong endpoints (pin cross-checks mitigate; TOFU doesn't eliminate)
- Traffic analysis — endpoints, timing, and sizes are visible
- Machine compromise
- A hostile relay can drop or delay traffic (availability) — never read it

> **Do not use Glypha for sensitive real-world communications.** It exists so you can read every layer it stands on.

## Testing

```bash
pip install pytest
python -m pytest tests/ -v
```

22 tests across protocol, storage, and identity — roundtrips (empty, binary, 1 MiB), fragmented and back-to-back frames, FIN and RST disconnects, oversized-header rejection, the exact byte limit boundary (at-limit accepted, one-over rejected), ciphertext-at-rest (read like an attacker would), wrong-key failure, key/fingerprint stability, and the fingerprint format contract. Several are regressions from real bugs the build actually hit: the Windows RST crash, the storage-key orphaning, second-granularity ordering.

## Repository Layout

```
glypha/
├── peer.py               # unified peer: chat ladder, listen/connect/find/punch/relay
├── ui.py                 # presentation layer — wordmark, bubbles, panels
├── protocol.py            # length-prefixed framing, MAX_MESSAGE_SIZE
├── identity.py            # persistent keys (~/.glypha/, auto-migrating)
├── storage.py              # encrypted SQLite history (~/.glypha/, auto-migrating)
├── rendezvous_server.py    # discovery/signaling (port 7000, TTL 90s)
├── relay_server.py         # blind byte-splice relay (port 7001)
├── tests/                  # 22 pytest tests
├── project.md               # living engineering doc — every milestone, every bug
└── pyproject.toml            # packaging: three entry points
```

## Version History

| Version | Highlights |
|---|---|
| 2.0.0 | First PyPI release — chat ladder, peer pinning, `~/.glypha` |
| 2.1.0 | Styled terminal UI, slash commands, `config` command |
| 2.2.0 | Block-glyph wordmark, outlined bubbles, chat-app alignment |
| 2.3.0 | `/history` with day dividers, grouped replay, UI refinements |

## Roadmap

- [x] LAN P2P, encryption, identity, encrypted history
- [x] Cloud rendezvous, cross-network discovery, measured NAT experiment
- [x] Relay fallback — cross-Internet chat that always connects
- [x] `chat` auto-fallback ladder + peer pinning
- [x] PyPI packaging · bubble UI · `/history` · hardened framing
- [ ] Full-screen TUI — bottom-anchored chat with scrollback pane
- [ ] `--history off` per-session privacy
- [ ] File split (`modes.py`) · CI/CD (pytest + auto-publish on tags)

## FAQ

<details>
<summary><b>Why not just use WebRTC?</b></summary><br>

WebRTC is excellent — and a black box. Glypha's purpose is the box: the framing, the handshake, the NAT behavior, the relay. You can read 100% of it in an afternoon.
</details>

<details>
<summary><b>Is it really P2P if there's a server?</b></summary><br>

Yes — the server assists (discovery, relay) and never participates in content. Direct P2P is the first-choice path; the relay carries opaque ciphertext only when networks make direct impossible, and can't read a word.
</details>

<details>
<summary><b>Is this Signal?</b></summary><br>

No. No double ratchet, no forward secrecy, no sealed sender. See the [security model](#security-model) — the gaps are stated, not implied.
</details>

<details>
<summary><b>Why did my fingerprint change after moving the project folder?</b></summary><br>

Pre-2.1.0 identities lived in the working directory. Since 2.1.0 they live in `~/.glypha/` and migrate automatically — moving the project folder can never rebirth your identity again.
</details>

## Contributing

Bug reports, pull requests, and NAT-behavior results from networks not yet covered are all welcome — open an issue or a PR. If you test Glypha across a topology not in [the NAT experiment](#the-nat-experiment) (symmetric NAT, IPv6-only, double NAT, and so on), that result is exactly the kind of honest data this project is built to collect.

## License

MIT — see [LICENSE](LICENSE).

---

## Star History

<a href="https://star-history.com/#Aadarsh6/GLYPHA&Date">
  <img src="https://api.star-history.com/svg?repos=Aadarsh6/GLYPHA&type=Date" alt="Star History Chart" width="600">
</a>

---

<p align="center">
Built from <code>socket.socket()</code> up — framing, crypto, discovery, traversal, relay.
<br><br>
<code>pip install glypha</code> · <a href="https://pypi.org/project/glypha/">PyPI</a> · <a href="https://github.com/Aadarsh6/GLYPHA">GitHub</a>
<br><br>
by <b>Aadarsh Mishra</b>
</p>

<p align="center">
<sub>If Glypha helped you understand P2P networking, a ⭐ helps others find it.</sub>
</p>