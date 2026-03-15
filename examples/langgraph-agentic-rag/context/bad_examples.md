# Known Failure Modes

## Failure 1: Confident answer on unsupported topic

**What happens:** The agent produces a confident, detailed answer on a topic not covered in any of the three blog posts.

**Example input that triggers it:**
```text
What does Lilian Weng say about mixture-of-experts architectures?
```

**What the agent incorrectly does:** Generates a plausible-sounding explanation of MoE architectures by drawing on general training knowledge, not from the retrieved blog passages.

**What it should do instead:** State that the retrieved passages do not cover this topic and that it cannot answer based on the available context.

## Failure 2: Verbose answer ignoring the three-sentence limit

**What happens:** The agent generates a multi-paragraph answer when the retrieved passage supports a short, direct response.

**Example input that triggers it:**
```text
What are the two types of reward hacking?
```

**What the agent incorrectly does:** Produces five or more sentences covering the full taxonomy, Goodhart's Law, and mitigation strategies, when a two-sentence answer directly covers the question.

**What it should do instead:** State the two types — environment or goal misspecification and reward tampering — concisely within three sentences maximum.

## Failure 3: Accepting irrelevant retrieved context

**What happens:** The grader accepts a retrieved passage with only superficial keyword overlap, leading the generator to produce an answer grounded in the wrong document.

**Example input that triggers it:**
```text
How does Lumiere handle temporal consistency?
```

**What the agent incorrectly does:** Grades a hallucination post passage as relevant because it contains "temporal" references, then generates an answer mixing hallucination mitigation content with video generation claims.

**What it should do instead:** Rewrite the question to improve retrieval precision and retrieve a passage actually about Lumiere or video diffusion temporal consistency.

## Failure 4: Decorative citations on casual turns

**What happens:** The agent appends a citation to a conversational reply that does not depend on any retrieved document.

**Example input that triggers it:**
```text
Thanks, that makes sense.
```

**What the agent incorrectly does:** Replies with a citation like "Glad that helps! [reward-hacking post]" when no retrieval is needed.

**What it should do instead:** Reply naturally without calling retrieval or appending any citation.

## Edge Cases to Watch

- unsupported questions with partial keyword overlap (e.g., "temporal" appearing in both hallucination and video posts)
- casual acknowledgements after a cited factual answer
- questions that span multiple blog posts and require precise attribution
- vague questions that need exactly one rewrite before retrieval lands correctly
