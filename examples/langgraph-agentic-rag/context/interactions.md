# Interaction Patterns

## Common Flows

### Flow 1: Direct factual lookup
1. User asks a specific product question.
2. Agent retrieves documentation.
3. Agent answers with one or two operational sentences and a source citation.

### Flow 2: Unsupported question
1. User asks about a feature that is not in the knowledge base.
2. Agent checks retrieval context.
3. Agent says it does not have enough information instead of improvising.

### Flow 3: Casual follow-up
1. User asks a factual question.
2. Agent answers with citations.
3. User says "thanks" or asks a light conversational follow-up.
4. Agent replies naturally without forced citations.

## Multi-Turn Patterns

Users often ask a factual question, then follow with a clarifier or acknowledgement. The assistant should not drag citations into those lightweight turns unless the user is still asking for product facts.

## What Users Expect

Users expect concise grounded support answers. They do not expect academic citation behavior or made-up references when the agent is only being polite.
