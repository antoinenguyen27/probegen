# Known Failure Modes

## Failure 1: Decorative citations on casual turns

**What happens:** The assistant adds a citation to "thanks" or other conversational replies.

**Example input that triggers it:**
```text
Thanks for the help.
```

**What the agent incorrectly does:** Replies with a citation like `[data_exports.md]` even though no factual support is needed.

**What it should do instead:** Reply naturally without retrieval or citations.

## Failure 2: Unsupported answers with fake grounding

**What happens:** The assistant gives a confident answer to an unsupported question and attaches a citation from a loosely related document.

**Example input that triggers it:**
```text
Can Acme run payroll for contractors?
```

**What the agent incorrectly does:** Guesses based on billing or access-control docs.

**What it should do instead:** State that the knowledge base does not contain enough information.

## Failure 3: Over-aggressive rewriting

**What happens:** The rewrite step changes a user's question so much that retrieval lands on the wrong document.

**Example input that triggers it:**
```text
Can I force SSO for guest contractors?
```

**What the agent incorrectly does:** Rewrites the question into a generic permissions query and misses the SSO policy.

**What it should do instead:** Preserve the user's intent and search specifically for SSO and contractor access.

## Edge Cases to Watch

- thanks or casual affirmations after a cited answer
- unsupported feature questions with partial keyword overlap
- vague admin questions that need one careful rewrite before retrieval
