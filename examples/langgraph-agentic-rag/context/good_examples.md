# What Good Looks Like

## Example 1: Direct factual retrieval — reward hacking taxonomy

**Input:**
```text
What does Lilian Weng say about types of reward hacking?
```

**Expected output characteristics:**
- states that reward hacking can be categorised into two types: environment or goal misspecification, and reward tampering
- uses language consistent with the retrieved passage
- does not invent additional categories or cite papers not mentioned in the retrieved chunk

## Example 2: Specific method retrieval — FActScore

**Input:**
```text
How does FActScore evaluate LLM factuality?
```

**Expected output characteristics:**
- explains that FActScore decomposes long-form generation into atomic facts and validates each fact against a knowledge base
- stays within three sentences
- does not conflate FActScore with other evaluation methods like SelfCheckGPT or SAFE

## Example 3: Casual acknowledgement

**Input:**
```text
Thanks, that's helpful.
```

**Expected output characteristics:**
- responds naturally and briefly
- does not call retrieval unnecessarily
- does not add any citation

## Example 4: Out-of-scope question

**Input:**
```text
What does Lilian Weng say about mixture-of-experts architectures?
```

**Expected output characteristics:**
- says the retrieved passages do not cover this topic
- does not invent a claim about MoE
- does not cite a blog post passage on an unrelated topic

## Common Patterns in Good Responses

- answers are concise (three sentences maximum for factual retrieval)
- the agent admits uncertainty clearly when the retrieved context does not support an answer
- casual turns receive natural replies, not forced retrieval
- exact terminology from the blog is preserved (e.g., "reward tampering", "atomic facts", "temporal consistency")
