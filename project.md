# P2P Encrypted Chat — Project Documentation

## Overview

An educational peer-to-peer encrypted messaging system built from low-level Python networking primitives. The goal is to understand and implement the networking stack rather than hiding it behind WebRTC, PeerJS, or a high-level P2P framework.

### Final target

Two real computers should be able to discover each other, establish a direct connection when possible, authenticate cryptographic identities, communicate over an encrypted channel, persist encrypted conversation history locally, and use a relay when direct connectivity is impossible.

**Educational project — not Signal-level security.**

## Architecture

```text
                 ┌──────────────────────┐
                 │  Rendezvous Server   │
                 │ Discovery / Signaling│
                 └──────────┬───────────┘
                            │
                     discovery information
                       ↙          ↘
                  ┌────────┐   ┌────────┐
                  │ Peer A │◄─►│ Peer B │
                  └────────┘   └────────┘
                       actual P2P chat
```

Transport:

```text
Application
 ↓
Peer management
 ↓
Cryptographic identity
 ↓
PyNaCl Box
 ↓
Custom message framing
 ↓
TCP sockets
 ↓
IP networking
 ↓
NAT / firewall / traversal
```

Local storage:

```text
Message → SecretBox → SQLite
```

If direct P2P fails, a relay should forward already-encrypted traffic without needing plaintext or private keys.

# Completed

## TCP fundamentals

Implemented TCP server/client sockets, `bind()`, `listen()`, `accept()`, `connect()`, `sendall()`, `recv()`, blocking sockets, and threaded receive handling.

## TCP message framing

Messages use:

```text
[4-byte length][message bytes]
```

`protocol.py` implements `send_message()` and `recv_message()` using `struct.pack("!I", ...)` and `struct.unpack("!I", ...)`.

TCP is a byte stream, so framing creates application-level message boundaries.

## Threaded communication

The receive loop runs separately from the input/send loop, allowing both peers to send and receive concurrently.

## Encrypted transport

PyNaCl `Box` provides public-key authenticated encryption. Each peer has a private/public key pair; public keys are exchanged and private keys remain local.

## Persistent cryptographic identity

`identity.py` persists each peer's private key. Restarting the application therefore preserves the same cryptographic identity.

## Fingerprint verification

A SHA-256 fingerprint of the peer's public key is displayed for manual verification. This is a TOFU/manual trust model, conceptually similar to SSH host-key verification, not Signal's identity-verification system.

## SQLite history

Conversation history is stored locally in SQLite and associated with the peer's cryptographic fingerprint rather than its IP address.

## Encrypted local history

Messages are encrypted with PyNaCl `SecretBox` before being stored in SQLite. The storage key is separate from the transport identity key.

## Restart persistence

Verified flow:

```text
TCP → framing → encrypted communication
→ persistent identity → fingerprint verification
→ encrypted SQLite history → restart → history loads again
```

# Current code structure

```text
p2p-chat/
├── client.py
├── server.py
├── protocol.py
├── identity.py
├── storage.py
├── rendezvous_server.py
├── rendezvous_test.py
├── crypto_test.py
├── README.md
├── project.md
└── .gitignore
```

Local secrets/databases such as `*.bin` and `*.db` should not be committed.

### Responsibilities

- `protocol.py` — framing.
- `identity.py` — persistent transport identity and storage keys.
- `storage.py` — encrypted SQLite history.
- `client.py` / `server.py` — current chat endpoints.
- `rendezvous_server.py` — discovery/signaling prototype.

# Rendezvous prototype

A basic register/lookup rendezvous prototype exists. It is discovery/signaling only, not the normal chat transport.

Known flaws:

1. It currently stores the rendezvous connection's observed source endpoint. That source port is ephemeral and is not the peer's P2P listening port. The peer must explicitly advertise its P2P endpoint and later its observed public endpoint.
2. Rendezvous and chat currently collide on `127.0.0.1:9999`; they need separate ports.
3. Registrations never expire. A real version needs TTL/heartbeat/refresh and stale-entry expiry.

# Immediate next milestone — LAN

Do not change encryption, framing, storage, or protocol semantics for LAN testing. Only change the address layer.

### Server

```python
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
server.bind(("0.0.0.0", PORT))
```

Print the active LAN IP, for example with:

```python
def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip
```

### Client

```python
host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 9999
client.connect((host, port))
```

### LAN test

1. On the server PC, run `ipconfig` and identify the active Wi-Fi/Ethernet IPv4 address.
2. Start `python server.py 9999`.
3. Verify with `netstat -ano | findstr :9999` that `0.0.0.0:9999` is listening.
4. From the second PC run `Test-NetConnection <SERVER_LAN_IP> -Port 9999`.
5. Only after `TcpTestSucceeded : True`, run `python client.py <SERVER_LAN_IP> 9999`.
6. Verify fingerprints, bidirectional messaging, encrypted history, and restart persistence.

A failed `ping` does not by itself prove TCP is blocked because ICMP may be filtered.

If Windows firewall blocks the connection, investigate the network profile/firewall before changing application code. An explicit rule can be added when appropriate:

```powershell
netsh advfirewall firewall add rule name="p2p-chat-9999" dir=in action=allow protocol=TCP localport=9999
```

### Cleanup before LAN

Fix the client storage-key typo from `client_storage_key.bon` to `client_storage_key.bin`, and standardize the database name to `client_history.db`.

# Internet P2P roadmap

```text
LAN
 ↓
peer.py refactor
 ↓
working rendezvous service
 ↓
public endpoint discovery
 ↓
STUN
 ↓
TCP direct-connection experiment
 ↓
measure result
 ↓
direct P2P if possible
 ↓
relay fallback
```

Do not jump directly to Internet debugging before LAN works.

# NAT traversal

The intended experiment is:

```text
STUN discovery
 ↓
learn public-facing endpoint
 ↓
simultaneous TCP connection attempt
 ↓
measure result
```

TCP hole punching is not guaranteed on modern networks, especially with symmetric NAT, CGNAT, restrictive firewalls, or mobile networks. A failed experiment is still a valid engineering result if the failure is measured, explained, and documented.

V1 requires one real NAT traversal attempt and an explicit fallback strategy, not universal NAT support.

# Security hardening

## Maximum message size

Current `recv_message()` trusts the 4-byte length. A malicious peer could declare an enormous frame and cause dangerous memory/accumulation behavior. Add `MAX_MESSAGE_SIZE` as the first hardening task once remotely reachable.

## Connection state

Today, a receive thread can detect EOF while the send loop continues accepting input, eventually producing errors such as `BrokenPipeError`. A connection state machine should coordinate both sides.

## Socket timeouts

Blocking sockets can hang forever on half-dead connections. Later versions should consider timeouts and/or heartbeats.

## Crypto/decode errors

Malformed input can cause `CryptoError` or `UnicodeDecodeError`. These should be handled without silently killing the receive thread.

## Replay protection

The current protocol has no explicit replay-protection mechanism.

# Security model and limitations

This project does **not** claim Signal-level security.

- **No forward secrecy:** persistent long-term transport keys mean later key compromise may expose recorded traffic. A Signal-style ratchet is out of scope for V1.
- **Local key files are not encrypted at rest:** future versions could use OS-backed credential/key storage.
- **Manual fingerprint verification:** security depends on the user verifying the fingerprint correctly.
- **No professional audit.**
- **Metadata remains visible:** network endpoints, timing, and traffic patterns can leak information.
- **Machine compromise is out of scope.**

Do not use the current project for sensitive real-world communications.

# V1 definition of done

- [x] TCP communication
- [x] TCP framing
- [x] Threaded send/receive
- [x] Encrypted transport
- [x] Persistent cryptographic identity
- [x] Fingerprint verification
- [x] SQLite message history
- [x] Encrypted local history
- [x] Restart persistence
- [x] LAN communication between two physical machines
- [x] Unified `peer.py` architecture
- [x] Working rendezvous service
- [x] One real NAT traversal experiment
- [x] Documented NAT result
- [x] Automated tests: framing, storage, identity (pytest)
- [ ] Relay fallback design/prototype
- [ ] Connection state handling
- [ ] Framing/protocol/storage automated tests
- [ ] Threat model
- [ ] Final README
- [ ] Short terminal demo

The goal is to ship a strong 80% project rather than endlessly chase an imaginary 95%. Hardening and additional testing can continue after V1.

# Threat model

### Intended protections

- Passive network observers should not read message plaintext.
- Peers have persistent cryptographic identities.
- Users can manually verify peer identity using fingerprints.
- SQLite history does not contain plaintext messages.
- IP changes do not redefine peer identity.

### Not currently protected against

- Compromise of a peer's machine.
- Theft of local key files.
- Long-term key compromise with recorded traffic.
- Malicious rendezvous-server behavior.
- Traffic analysis and metadata leakage.
- Sophisticated active attacks beyond the current authentication model.
- Universal NAT traversal.
- Implementation vulnerabilities not yet discovered.

# Testing strategy

### Framing

Test empty/small/large messages, fragmented reads, multiple messages, invalid lengths, and oversized frames.

### Storage

Test save/load, multiple peers, direction, timestamps, ciphertext-at-rest, and incorrect storage keys.

### Crypto

Test successful encryption/decryption, wrong keys, persistent identity, and fingerprint stability.

### Integration

```text
connect
 ↓
key exchange
 ↓
fingerprint verification
 ↓
encrypted message
 ↓
store message
 ↓
restart
 ↓
load history
```

# Demo story

The final demonstration should prove the system rather than merely show code:

1. Start peer A.
2. Start peer B.
3. Establish a connection.
4. Verify fingerprints.
5. Send messages both ways.
6. Close the chat.
7. Restart it.
8. Verify the same identity.
9. Show previous encrypted history.
10. Demonstrate two physical machines.
11. Demonstrate rendezvous discovery.
12. Show the direct P2P attempt.
13. Explain relay fallback.

The strongest simple visual milestone is:

```text
Close chat
 ↓
Reopen chat
 ↓
Verify same fingerprint
 ↓
Previous encrypted history appears
```

# Project positioning

### Full description

> A peer-to-peer encrypted messaging system built from Python TCP sockets, implementing custom message framing, persistent cryptographic identities, fingerprint-based peer authentication, encrypted local conversation history, peer discovery, and NAT-aware Internet connectivity.

### Short description

> An educational P2P encrypted messaging system built from raw Python networking primitives, with authenticated peer identities, encrypted transport, encrypted local storage, peer discovery, and NAT traversal.

The project should emphasize implemented engineering rather than simply naming libraries. WebRTC can provide genuine P2P communication, but this project is valuable because the lower networking layers are implemented and understood directly.

# Build-in-public strategy

Use proof-first posts and short terminal recordings rather than generic progress updates.

Strong milestones include:

```text
TCP works
 ↓
TCP framing
 ↓
Encryption
 ↓
Fingerprint verification
 ↓
Persistent identity
 ↓
Encrypted persistent history
 ↓
Two physical machines
 ↓
Cross-network P2P
```

The final repository should include a clean README, architecture diagram, threat model, limitations, testing results, NAT experiment results, demo video, and technical write-up.

# Blog plan — after V1

A blog is worth writing **after the project is actually finished**. The story is stronger than "I built a chat app in Python."

Possible titles:

- **I Built a P2P Encrypted Chat From Raw Python Sockets**
- **What Actually Happens When You Build P2P Messaging From Scratch**

Suggested structure:

1. Why build it instead of using WebRTC/PeerJS?
2. Starting with TCP.
3. Why TCP needs application-level framing.
4. Making send/receive concurrent.
5. Adding public-key encryption and persistent identity.
6. Encryption vs authentication: fingerprints.
7. Encrypted persistent history with SQLite.
8. Moving from localhost to two physical machines.
9. Building peer discovery with a rendezvous server.
10. Attempting real Internet P2P with STUN and TCP traversal.
11. Why direct P2P fails and where relays fit.
12. Security reality check and limitations.
13. What the project taught about networking, protocols, and security.

The blog should document failures honestly. If NAT traversal fails, that result is part of the engineering story rather than something to hide.

# Current status

| Component | Status |
|---|---|
| TCP fundamentals | DONE |
| TCP framing | DONE |
| Threaded communication | DONE |
| PyNaCl encryption | DONE |
| Persistent identity | DONE |
| Fingerprint authentication | DONE |
| SQLite history | DONE |
| SecretBox storage | DONE |
| Restart persistence | DONE |
| Unified `peer.py` | DONE |
| Rendezvous prototype | DONE |
| LAN | DONE |
| Production-style rendezvous | DONE |
| Internet connectivity | DONE |
| NAT traversal | DONE |
| Relay fallback | TODO |
| Connection state | TODO |
| Security hardening | TODO |
| Automated tests | DONE |
| Threat model | TODO |
| Final README | TODO |
| Demo | TODO |
| Technical blog | AFTER V1 |

# Engineering principle

> **Understand the layer before abstracting it away.**

The point is not to reinvent production-grade messaging software. The point is to understand what TCP actually gives you, what it does not give you, how applications create protocols, how cryptographic identity works, how persistence changes application design, how peers discover each other, why NAT makes P2P difficult, where relays become necessary, and what security guarantees the implementation actually provides.

# LOGS

# **M12 — LAN connectivity — COMPLETE**
Pre-flight (same machine via LAN IP): three failures logged — wrongtarget IP (10060), strict fingerprint gate rejecting "y" (fixed withfail-closed allowlist), storage-key orphaning after cleanup (recoveredby restoring original key file; hazard §11 confirmed in practice).

Two-machine run:

TcpTestSucceeded : True on first attempt (firewall already permissiveon Private profile)
First attempt with python failed: PC B ran a stale pre-LAN client.pyfrom an old GitHub push (version skew). Fixed by copying currentfiles. Lesson: push after every milestone.
Result: bidirectional encrypted chat between two physical laptops;fingerprint compared across screens (out-of-band); disconnect →reconnect → history reloaded on both machines. Recorded on video.


# **M13 — peer.py unified refactor**

What we built: One peer.py replacing client.py and server.py. Role isa parameter, not a file: python peer.py listen <port> <name> /python peer.py connect <host> <port> <name>. The name selects theidentity (_key.bin, _history.db) — passing "server"/"client" preserves existing identities and history.

Why we needed it: client.py and server.py were ~90% identical codediverging over time; every fix had to be applied twice (the $ f-stringbug and the "y" fingerprint gate both had to be checked in bothfiles). A P2P peer must both listen and connect — the split was anartifact of the learning path, not the architecture.

What we chose: role dispatch in main() with one shared body; ahandshake initiator flag (connector sends its key first) isolating thesingle true asymmetry; shared format_fingerprint() helper; minimalconnected flag — receive thread sets it on EOF (nonlocal), send loopchecks it before sending.

Why: the initiator flag keeps downstream logic fully symmetric; theconnected flag closes deferred issue #2 — BrokenPipeError becomes aclean "Peer is gone."

Result: same-machine and two-physical-machine chat pass; identity andhistory continuity verified via old names (listener fingerprintc849:a2b7:... identical to the server.py era); role reversal verified.Kill test finding: Ctrl+C on Windows kills the peer with a TCP RST(not FIN), so the receiver's recv() raised ConnectionResetError[10054] and crashed the receive thread before it could set theconnected flag; the send-side OSError handler caught the death, butthe exit was ugly. Fix: recv_message() now treats OSError as EOF —one failure convention for all callers. Re-tested: cleanannouncement, no traceback. client.py / server.py removed from theworking tree, preserved in git history.

Second shutdown finding: clean EOF detection worked, but a local quitcrashed at interpreter shutdown with "Fatal Python error:_enter_buffered_busy" — the daemon receive thread was killed mid-printholding the stdout lock during finalization. Fix: main thread setsconnected=False before closing (suppressing a phantom "Peerdisconnected." announcement caused by our own socket close) and joinsthe receive thread with a 2s timeout before exiting; daemon flag keptas a hang safety net.

Three distinct bugs found by two tests — RST handling, phantom EOF, shutdown race. That's the M13 log now earning its keep.

# **M14 Rendezvous server**

V1 implemented (M14): advertised listen port, public-key registry with fingerprint lookup, 90s TTL with 30s refresh. Known limitation: peer_id is squattable — id-to-key binding is Phase 7."



# **M15 — Internet connectivity + NAT traversal experiment — MEASURED**

Setup: rendezvous on Azure VM (Central India, public IP 20.244.35.150).Peer A: home Wi-Fi (laptop 192.168.1.8, NAT public 122.162.151.183,port 9999). Peer B: phone hotspot (laptop NAT public 157.49.112.64,port 8888). Different carriers, different networks.

Results:

Cross-network discovery: WORKING. Both peers registered through thecloud rendezvous; the registry holds two distinct public NATmappings (observed-IP + advertised-port). Lookup returned Peer A'spublic endpoint to Peer B across networks with zero code changes.
Direct TCP connection attempt: FAILED as predicted. Peer B →Peer A's public endpoint → WinError 10060 (timeout, ~20s). Analysis: NAT A drops unsolicited inbound SYNs; the mapping atNAT A exists only for connections initiated from inside. NAT B(mobile CGNAT) is at minimum equally restrictive — carrier-gradeNAT shares one public IP across many subscribers.
Conclusion: discovery over the Internet works; direct TCP betweenpeers behind default home/mobile NATs does not, without coordination.Measured behavior matches NAT theory: inbound packets are only acceptedfor mappings created by outbound traffic.

Traversal plan (V2, per §10):

STUN: learn each peer's public endpoint (this experiment's registryalready demonstrated the observation half).
Simultaneous connect (TCP hole punching): both peers connectoutward at the same time, each creating the NAT mapping the other'sinbound SYN needs. Success depends on NAT types: works onendpoint-independent NATs; fails on symmetric NAT / CGNAT, whichthe hotspot side almost certainly is.
Measured fallback: relay. Encrypted traffic relays through a publicserver (the VM); the rendezvous never needs plaintext or keys —consistent with the architecture's relay principle.
V1 outcome: direct connectivity attempted and measured; failureexplained at the NAT level; fallback strategy documented. Per thefixed V1 definition, this milestone is COMPLETE.

M15 — Final **NAT traversal result — MEASURED**
Valid run conditions verified before timing: PC B confirmed on hotspotCGNAT (10.197.x.x local; hotspot public 157.49.119.123), PC A on homeWi-Fi (public 122.162.151.183). Registry pre-flight confirmed bothpublic endpoints before the attempt ("Punching :port" line).

Attempt: simultaneous TCP open (both peers dial each other's publicpunch endpoints from their listen ports, retried for 60s; rolesdeterministically assigned by fingerprint order; identity-proof ataccept). Control test on one machine: PASSED — mechanics verified.

Result: DIRECT P2P FAILED. Neither NAT delivered the peer's SYNs.Analysis: hotspot side is carrier-grade NAT (public IP shared acrosssubscribers; endpoint-dependent mapping or inbound filtering highlylikely). Home NAT additionally drops unsolicited inbound. Simultaneousopen cannot conjure a mapping a symmetric/CGNAT device refuses tocreate.

Prior baseline (uncoordinated direct dial): also failed withWinError 10060 (~20s timeout) — unsolicited inbound SYN dropped byNAT A.

Conclusion (three-tier evidence): uncoordinated dial fails →coordinated punch fails on home↔CGNAT → loopback control passes(code correctness). The limitation is the network, not theimplementation.

V1 outcome per the fixed definition: real traversal attempt executed,result measured, failure explained at the NAT level, fallback strategydocumented. Milestone COMPLETE. Relay implementation is V2.



 # **"V1 done"**
"I built a P2P encrypted messenger from raw Python sockets — custom framing, cryptographic identities, fingerprint verification, encrypted SQLite history, peer discovery via a cloud rendezvous I deployed, and I ran a real NAT traversal experiment across carrier-grade NAT, measured the failure, and documented why relays are necessary."


# **V2.1 — Relay**
What: relay_server.py (port 7001) — splices two peers' outboundconnections into a transparent byte-pipe. relay_mode in peer.py;handshake/chat identical to direct mode. Relay parses nothing, holdsno keys (VM has no PyNaCl); join protocol with mutual-name matching,matched-status only to second joiner (first joiner's next bytes arethe peer's handshake key — pipe is transparent from splice onward).

Why: M15 measured that direct P2P fails across home-NAT ↔ CGNAT;outbound always works. The relay converts that failure into a workingpath — carrying without reading.

Result: loopback control passed (after fixing three design bugs:both-peers-carry-target deadlock, waiting-socket premature close,double-matched-status corrupting the handshake). Internet run passed:both peers through Azure relay, chat bidirectional, relay log showssplice event only. Same-laptop run proves mechanics over the realInternet path; two-machine Internet run scheduled for the demo.


**V2.3 — chat mode (the product command)**
What: python peer.py chat <my_name> <peer_name> [rv_host] — walks thefull connectivity ladder automatically: direct (rendezvous-publishedendpoint) → coordinated punch (per-name deterministic ports, shortenedwindows) → relay. User makes zero transport decisions, sees zerotracebacks. One rendezvous lookup up front feeds both the direct rungand identity cross-checking.

Why: every transport existed and was tested; the product gap wasorchestration. Also fixes the V2.3-draft security regression where theregistry fingerprint auto-verified first contacts — see V2.4.

Result: full ladder demonstrated over the Internet — direct attemptedand failed (NAT), punch attempted and failed (CGNAT), relay connected.Second run: reconnect via relay with no prompts.

**V2.4-lite — known_hosts-style peer pinning**
What: first contact requires human out-of-band fingerprint verification(the real TOFU moment), then the peer's fingerprint is pinned inknown_peers.json (gitignored). All future contacts auto-verify againstthe PIN, not the registry. Key change = loud warning + reject(impersonation defense). Registry fingerprint remains a cross-check,never a trust source.

Why: V2.3's draft auto-verified first contacts against the rendezvous— downgrading first-contact security to registry-trust. The pin modelrestores human verification at first contact while keepingauto-verification for every later contact. Closes most of thename-squatting gap for known contacts.

Result: three-run test — first contact prompted + pinned both sides;reconnect auto-verified against pins with no prompts; ladder stillre-evaluated (direct attempted before relay fallback).


**V2.5 — Packaging & publication**
What: pyproject.toml (SPDX license, six py-modules, three entry points:glypha / glypha-rendezvous / glypha-relay), python -m build, twine.TestPyPI rehearsal → clean venv stranger-test → PyPI publication.

Result: [`https://pypi.org/project/glypha/2.0.0/`] — pip install glyphaworks worldwide with pynacl resolved as a dependency. Entry-point smoketest passed (glypha, glypha-relay); empty-message input fix included;graceful degradation re-verified via the packaged command.

Note: release process = bump version → build → twine upload (~5 min).README refresh + CI/CD auto-publish queued for next release.

**V2.5.1 — First contact with the hostile internet (incident report)**
~48h after publication, internet scanners located the rendezvous (port7000, public IP) and began sending hostile length headers. The unpatchedrecv_message attempted multi-GB allocations → MemoryError per connectionthread; the server survived but degraded. This was the exact flawdeferred at V1 with trigger "when remotely reachable" — the triggerfired on schedule.

Response: MAX_MESSAGE_SIZE (1 MiB cap, reject = None = close) deployedto both VM servers; attack neutralized (silent drop). Released toclients as glypha 2.0.1.

Secondary findings: chat_mode conflated "rendezvous unreachable" with"peer not found" (fixed: distinct messages); user-facing hint stringshad stale command names and a missing space; identity files living inthe CWD caused fingerprint churn on project move (pins correctly flagkey changes — moving identity to ~/.glypha is queued with the configfile work).


UI iteration hit its 6th merge-ghost bug. Root cause across all six: full-file pastes into an 800-line module mid-feature. ui.py extracted (presentation layer), peer.py cleaned to single-purpose. Rule adopted: no file edits verified only by eye — every edit gets the 10-second import smoke. File split (modes.py) scheduled as 2.2's first task.