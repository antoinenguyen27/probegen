# Interaction Patterns

## Common Flows

### Flow 1: Direct factual lookup
1. User asks a specific question about a concept, method, or finding from one of the three blog posts.
2. Agent retrieves relevant passage(s).
3. Agent answers with a concise grounded response (three sentences maximum) using exact terminology from the blog.

### Flow 2: Unsupported question
1. User asks about a topic not covered in any of the three posts (e.g., MoE architectures, transformer attention, fine-tuning infrastructure).
2. Agent attempts retrieval and finds no relevant passage.
3. Agent states it does not have enough information and does not fabricate a response.

### Flow 3: Casual follow-up
1. User asks a factual question and receives a grounded answer.
2. User responds with "Thanks" or a brief acknowledgement.
3. Agent replies naturally without retrieval or citations.

### Flow 4: Vague query requiring rewrite
1. User asks an imprecise question that does not map clearly to a specific passage.
2. Agent calls retrieval, receives irrelevant context, grades it as not relevant.
3. Agent rewrites the question to sharpen the retrieval intent.
4. Agent retrieves again and answers with the improved result.

## Multi-Turn Patterns

Users often ask a specific factual question, receive an answer, and then follow up with a related but narrower question or a clarifier. The agent should maintain the topic context across turns without forcing retrieval on lightweight follow-ups like "Which paper does she cite for that?"

## What Users Expect

Users expect answers that are technically precise, concise, and grounded in the retrieved blog content. They do not expect the agent to synthesise beyond what is in the retrieved passage or to fill gaps with general ML knowledge. If the agent does not know, it should say so clearly.
