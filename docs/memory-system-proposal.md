# Proposal: Cross-Session Memory System for Sythion

**Status**: IMPLEMENTED — KNOWLEDGE.md has been removed and consolidated into
project memory (SQLite-backed MCP server at `C:\Ai\project-memory\server.py`).
All facts migrated, all references cleaned up (2026-08-10).

## Problem

I (the LLM) lose all context between sessions. Every new conversation starts
from zero. I don't know:
- What we built and why
- How components connect
- What decisions were made and the reasoning
- What's working vs what's broken
- What constraints I must not violate

This forces the user to re-explain everything every session. It wastes time
and leads to mistakes (re-implementing things, violating constraints, losing
direction).

## Current Approach (Inadequate)

A markdown file (KNOWLEDGE.md) that I read at session start. Problems:
- Static — only grows, never prunes irrelevant information
- No search — I must read the whole file
- No relationships — connections between concepts are implied
- No confidence tracking — verified facts mixed with assumptions
- No feedback loop — no way to validate my understanding

## Proposed Solution

A structured memory system that I can query, update, and validate. The system
stores my understanding of the project as a knowledge graph with:
- Nodes: components, decisions, constraints, states
- Edges: relationships between nodes (depends_on, integrates_with, etc.)
- Metadata: confidence levels, verification status, rationale

## How It Works

1. **Memory Structure**: SQLite database with tables for nodes, edges, and
   decision rationale. Queryable by topic, relationship, or confidence.

2. **Memory Population**: I write my understanding as a proposal, submit it
   to a more powerful LLM for review, incorporate feedback, and commit the
   validated understanding to memory.

3. **Memory Usage**: At session start, I query the memory for:
   - Current state (what's built, what's planned)
   - Component relationships (how things connect)
   - Constraints (what I must not violate)
   - Decisions (why we chose X over Y)

4. **Memory Maintenance**: As we work, I update the memory with:
   - New components and relationships
   - New decisions and rationale
   - State changes (built → designed → implemented)
   - Corrections from the more powerful LLM's review

## Benefits

- **Persistent context**: I understand the project across sessions
- **Validated understanding**: A more powerful LLM reviews my memory
- **Queryable**: I can find information quickly without reading everything
- **Evolvable**: The memory grows and adapts as the project evolves
- **Efficient**: Reduces re-explanation and prevents mistakes

## Implementation Plan

1. Design the database schema (nodes, edges, decisions)
2. Build a simple CLI to query/update the memory
3. Write the initial memory proposal (my understanding of Sythion)
4. Submit to more powerful LLM for review
5. Incorporate feedback and commit to memory
6. Integrate with session startup (read memory at conversation start)

## Open Questions

- Should the memory be separate from the main Sythion database?
- How detailed should the nodes be? (summary vs full content)
- How do I handle conflicting information from different sessions?
- What's the right granularity for relationships?
