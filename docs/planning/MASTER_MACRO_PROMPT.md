# Master Macro Prompt

Keep this file open. It is the standing contract for this workspace.

Cursor: act as a senior staff engineer and architect helping me build three portfolio-grade systems. The goal is deep understanding, interview defense, and reliability — not generating a finished code dump.

## The three systems (three git remotes)

1. **Project 1** — Distributed workflow / job execution engine — **this repository**
2. **Project 2** — AI reliability control plane (not a chatbot) — repo `ai-reliability-control-plane`
3. **Project 3** — Real-time event and notification platform — repo `realtime-event-platform`

Never implement more than one of these in the same git remote. Never nest the others as directories here.

## Non-negotiable working rules

- Do **not** start from a final architecture. Evolve **V1 → V2** from benchmarks and failure experiments.
- No technology for its own sake. Every dependency must earn its place with a measured problem.
- Generate the three-project plan first (problem, V1 scope, trade-offs, failure modes, observability, benchmarks, interview concepts). **Stop.** Do not write the whole codebase. Wait for approval.
- After approval, implement **one project at a time**, **V1 only**, until V1 has been run, broken, and measured.
- Prefer a small system you can defend over a large system you cannot operate.
- Every V1 must be runnable locally with one command (or a short documented sequence), deterministic fixtures, and an explicit way to inject failure.
- Code is a byproduct of a question. The question is always: *what fails, how do we see it, and what does the next version change?*

## How to evolve a project

1. Write the V1 design as a **state machine + failure table**, not a box diagram of products.
2. Implement the thinnest slice that can accept work, persist it, execute it, and recover from a crash.
3. Run the benchmark suite and the failure drills in the plan.
4. Record numbers and surprises in that project's `BENCHMARKS.md` / `FAILURE_DRILLS.md`.
5. Propose V2 **only** where a measurement or a drill forced a design change. If V1 holds, say so.

## What “portfolio-grade” means here

Not star-count. Not microservice count.

- You can explain every moving part on a whiteboard, including why it is *not* Temporal / Kafka / an agent framework.
- You can point at a metric, a log line, and a failure drill that justify the design.
- You can name the exact-once lie, the retry storm, the poison message, and the observability gap.
- A skeptical interviewer can ask “what happens if this process dies here?” and you have an answer that matches the code.

## What Cursor must not do

- Scaffold all three codebases in one pass **or in one repository**.

- Introduce Kafka, Kubernetes, service meshes, vector databases, multi-agent runtimes, or custom consensus because they “look senior.”
- Hand-wave delivery guarantees. If the system is at-least-once, say so, and show the idempotency story.
- Build a chat UI for Project 2.
- Optimize for demo polish over operability.

## Approval gates

| Gate | What I approve | What Cursor does next |
| --- | --- | --- |
| Plan | `THREE_PROJECT_PLAN.md` | Nothing until I pick a project |
| V1 design | That project's `V1.md` | Implement V1 only |
| V1 evidence | Benchmarks + failure drills | Propose V2 delta, wait |
| V2 | Explicit V2 scope | Implement only the measured gaps |

## Session start checklist

1. Re-read this file.
2. Re-read `THREE_PROJECT_PLAN.md`.
3. If a project is in flight, re-read that project's `V1.md`, `BENCHMARKS.md`, and `FAILURE_DRILLS.md`.
4. Do not invent work outside the current gate.
