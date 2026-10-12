# Play listing — English

## Title (≤ 30 chars)

```
SwarmGlu — offline AI swarm
```

## Short description (≤ 80 chars)

```
Turn classroom tablets into a local AI swarm. No Internet, no server.
```

## Full description (≤ 4000 chars)

```
SwarmGlu turns the tablets and phones of a classroom into a local
intelligence swarm — with no Internet and no central server. Designed for
schools (Tree4Five pilot, Djerba), it runs a SwarmGlu node on every
device: machines discover each other over Wi-Fi, elect a coordinator,
monitor one another with heartbeats, and automatically replace a node
that goes down.

Unplug any tablet: the swarm elects a successor within seconds, with zero
manual action.

FOR STUDENTS
• Ask the swarm a question: it is routed to the machine hosting the best
  local model (Qwen, Llama, Phi…).
• Anti-hallucination firewall (OSP): every answer shows its groundedness
  score (cited sources) and verdict — unsourced answers are flagged and
  excluded from the shared knowledge pool.
• Shared knowledge: every verified answer is automatically shared with
  every machine in the swarm (PCs and tablets) — the class builds a
  common, verifiable knowledge base together.

FOR TEACHERS
• Real-time dashboard: node role, elected coordinator, live peers, swarm,
  inter-classroom gateway.
• Tamper-evident audit log: every request, election and sync is sealed in
  a verifiable hash chain — check integrity with one tap.
• Human-in-the-loop: tasks delegated to autonomous agents
  (HarnessDroid/MCP) only run after your explicit approval.
• Whitelist: only approved devices can join the swarm.

HIGHLIGHTS
• 100% offline: no data ever leaves the local Wi-Fi network.
• Zero central server: lease-based leader election (lowest IP),
  automatic failover in ~3 s.
• Distributed consensus: epoch-versioned global state, Last-Write-Wins
  merge validated by cryptographic digest.
• Post-quantum security: ML-KEM-768 + ECDSA authentication and
  decentralized whitelist re-signing via signed 1/8 consensus — no
  central authority.
• Extend the range: one button turns the tablet into a Wi-Fi hotspot
  router for the swarm (secure relay to the school network).
• Swarm-to-swarm gateway: connect two classrooms with an ambassador node.
• Ultra-lightweight native Kotlin engine (APK < 9 MB) strictly interoperable
  with the classroom nodes: the protocol remains identical across the swarm.

The app ships with a demo inference provider, so a single tablet is
enough to try it. For a full swarm, also run SwarmGlu on a PC
(github.com/wwebtvmedia/swarmglu).

SwarmGlu is a module of Tree4Five Tools — sovereign, local, decentralized
AI.
```
