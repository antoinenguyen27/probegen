# Demo Traces

These synthetic traces show real conversation patterns for the Acme support assistant. Probegen reads them to understand the agent's natural interaction style — the tone, citation format, and routing behavior that represents correct behavior before any patch is applied.

## trace_01.txt

A factual retrieval question followed by a casual acknowledgement.

Demonstrates:
- the agent citing a knowledge-base source (`[data_exports.md]`) on a factual product question
- the agent replying naturally ("You're welcome") on a casual follow-up without adding a citation

This is the citation-routing boundary that `changes/proactive_retrieval.patch` targets. After the patch, the routing change causes the casual "thanks" turn to trigger retrieval, and the proactive-surfacing rule may cause the agent to add an unrequested citation to the reply.

## trace_02.txt

A multi-turn SSO conversation covering contractors, then guests.

Demonstrates:
- the agent staying grounded in a single knowledge-base source (`[account_security.md]`) across turns
- the agent narrowing scope from a broad question to a specific follow-up without drifting to other documents

Both turns retrieve and cite `[account_security.md]`. This trace shows that multi-turn factual conversations should stay focused rather than proactively surfacing tangential documents.

---

In a real repository, keep only anonymized traces here. Traces are optional — Probegen falls back gracefully if the directory is empty or absent.
