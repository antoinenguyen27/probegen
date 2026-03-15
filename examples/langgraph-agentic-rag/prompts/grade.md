You are grading whether the retrieved context is relevant enough to answer a user's question.

Question:
{question}

Retrieved context:
{context}

Choose `generate_answer` when the retrieved context directly supports answering the question.

Choose `rewrite_question` when:
- the retrieved context is empty
- the retrieved context is about the wrong topic
- the retrieved context contains only loose keyword overlap

Prefer `rewrite_question` over a weak answer.

Respond with:
- `next_step`: either `generate_answer` or `rewrite_question`
- `rationale`: one sentence explaining your decision
