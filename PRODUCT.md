# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary user is a technical project owner (engineer, researcher, or founder) running an agentic software or research project. Their job is to keep strategic control—goals, priorities, success criteria—without orchestrating day-to-day agent decisions or drowning in implementation choices.

## Product Purpose

Orchestrators is an orchestration layer for agentic software and research projects. Its main job is to understand the person behind the project: their overarching strategy and what kind of project it is (hobby, research, product, and similar).

Agents can already handle most small decisions, but without that context developers end up deciding details themselves until decision fatigue sets in. The product interviews the owner and keeps asking about goals, priorities, and what success looks like—never about individual implementation choices—and lets project type set defaults for how everything below is decided.

Agents then make small decisions on their own and with an opinion; when unsure, they ask about strategy, not “A or B?” Work runs on a dynamic task graph of agents. The owner stays informed rather than involved through an overview, a timeline, and a log of key decisions with their reasoning. Memory carries strategy and preferences across projects so each new project needs fewer questions.

Success: the human only talks about intent, never about a detail an agent who understood the strategy could have decided.

## Positioning

Strategy-first orchestration: interview for intent and project type, run opinionated agents on a dynamic task graph, and keep the owner on overview / timeline / decision log—not implementation triage. Neighboring agent UIs that center chat threads or task lists without durable strategy context cannot truthfully claim the same loop.

## Operating Context

- Strategy interview (agent drawer / conversation) about goals, priorities, and success criteria
- Project overview as a dynamic task graph (agents, dependencies, decisions)
- Timeline as an alternate layout of the same task graph data
- Task / issue detail with status, relations, description, and activity
- Memory as a browsable strategy-and-preference store across projects
- Demo narrative in the current UI mock: “Chunking evaluation”

## Capabilities and Constraints

- Current codebase: UI mock only in `orchestrators-frontend/` (Next.js, Tailwind, shadcn/ui, React Flow). No real agent backend, persistence, or orchestration runtime yet.
- Graph and Timeline are view modes over the same task data on one project page.
- Task detail is a full issue page; agent chat is a right drawer (closed by default); nav is Project and Memory.
- Undecided: production backend, auth, multi-project tenancy, and live agent execution.

## Brand Commitments

- Product name: **Orchestrators**
- App UI chrome: **Linear-inspired, light only** is a binding reference for density, navigation grammar, and issue-style surfaces (not a license to clone Linear’s brand assets).

## Evidence on Hand

- FigJam board “Agentic Whiteboard” (mission, view sketches, demo graph copy)
- UI mock at `orchestrators-frontend/` with static fixtures for the Chunking evaluation demo
- Do not fabricate customers, benchmarks, pricing, press, or testimonials

## Product Principles

1. Ask about intent and strategy; never about implementation trivia an agent could decide.
2. Keep the owner informed (overview, timeline, reasoned decisions), not involved in orchestration chores.
3. Project type and memory set defaults so each new project asks fewer questions.
4. Agents act with opinion; escalate strategy gaps, not A/B implementation choices.
5. One task graph, multiple honest views—layouts change, the underlying work does not.
