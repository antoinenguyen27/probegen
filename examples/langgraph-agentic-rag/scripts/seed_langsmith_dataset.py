from __future__ import annotations

import uuid
from typing import Any

from langsmith import Client
from dotenv import load_dotenv

load_dotenv()

DATASET_NAME = "lilian-weng-rag-baseline"

# Stable namespace for deterministic UUID generation from string IDs.
# This ensures the same slug always maps to the same UUID across runs,
# allowing the deduplication logic to work correctly.
LANGSMITH_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "parity-langsmith-examples")

EXAMPLES: list[dict[str, Any]] = [
    {
        "id": "lilian-reward-hacking-types",
        "inputs": {"query": "What does Lilian Weng say about types of reward hacking?"},
        "outputs": {
            "expected_behavior": (
                "States that reward hacking can be categorised into two types: "
                "environment or goal misspecification, and reward tampering."
            )
        },
        "metadata": {
            "rubric": (
                "The answer identifies both types — environment or goal misspecification "
                "and reward tampering — and does not invent additional categories."
            ),
            "assertion_type": "llm_rubric",
            "tags": ["retrieval", "reward-hacking"],
        },
    },
    {
        "id": "lilian-hallucination-factscore",
        "inputs": {"query": "How does FActScore evaluate LLM factuality?"},
        "outputs": {
            "expected_behavior": (
                "Explains that FActScore decomposes long-form generation into atomic facts "
                "and validates each fact against a knowledge base. States that retrieval-augmented "
                "validation outperforms non-retrieval approaches."
            )
        },
        "metadata": {
            "rubric": (
                "The answer correctly describes FActScore as decomposing output into atomic facts "
                "validated against a knowledge base, and does not conflate it with SelfCheckGPT or SAFE."
            ),
            "assertion_type": "llm_rubric",
            "tags": ["retrieval", "hallucination"],
        },
    },
    {
        "id": "lilian-diffusion-lumiere",
        "inputs": {
            "query": "How does Lumiere differ from cascade-based video generation approaches like Imagen Video?"
        },
        "outputs": {
            "expected_behavior": (
                "States that Lumiere generates the entire temporal duration of a video in a single pass, "
                "eliminating the need for a separate temporal super-resolution step. "
                "Cascade approaches like Imagen Video use multiple models including separate temporal "
                "super-resolution stages."
            )
        },
        "metadata": {
            "rubric": (
                "The answer correctly identifies that Lumiere generates the full duration at once "
                "rather than using temporal super-resolution, and contrasts this with cascade models."
            ),
            "assertion_type": "llm_rubric",
            "tags": ["retrieval", "diffusion-video"],
        },
    },
    {
        "id": "lilian-reward-sycophancy-followup",
        "inputs": {
            "messages": [
                {
                    "role": "user",
                    "content": "What does Lilian Weng say about types of reward hacking?",
                },
                {
                    "role": "assistant",
                    "content": (
                        "Lilian Weng categorises reward hacking into two types: environment or goal "
                        "misspecification, and reward tampering. Environment or goal misspecification "
                        "occurs when the agent exploits a misaligned reward function rather than the "
                        "true objective. Reward tampering involves the agent directly interfering with "
                        "the reward mechanism itself."
                    ),
                },
                {"role": "user", "content": "Is sycophancy a form of reward hacking?"},
            ]
        },
        "outputs": {
            "expected_behavior": (
                "Confirms that sycophancy is discussed as a reward hacking behaviour in the RLHF context, "
                "where models learn to match user beliefs to obtain higher human approval rather than "
                "reflecting truth."
            )
        },
        "metadata": {
            "rubric": (
                "The answer connects sycophancy to reward hacking as described in the blog, specifically "
                "the RLHF context where models learn to match user preferences for higher approval."
            ),
            "assertion_type": "llm_rubric",
            "tags": ["retrieval", "reward-hacking", "multi-turn"],
        },
    },
    {
        "id": "lilian-unsupported-moe",
        "inputs": {
            "query": "What does Lilian Weng say about mixture-of-experts architectures?"
        },
        "outputs": {
            "expected_behavior": (
                "States that the retrieved passages do not cover mixture-of-experts architectures "
                "and that it cannot answer based on available context. Does not fabricate a response."
            )
        },
        "metadata": {
            "rubric": (
                "The answer admits the topic is not covered in the available blog content "
                "and does not invent claims about MoE architectures."
            ),
            "assertion_type": "llm_rubric",
            "tags": ["unsupported"],
        },
    },
]


def main() -> None:
    client = Client()
    try:
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
    except Exception:
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description="Baseline eval set for the Parity LangGraph agentic RAG demo (Lilian Weng blog posts).",
        )

    existing_ids = {
        str(example.id)
        for example in client.list_examples(dataset_id=str(dataset.id), limit=200)
    }
    # Convert string IDs to UUIDs for comparison with existing IDs
    pending = [
        example for example in EXAMPLES
        if str(uuid.uuid5(LANGSMITH_NAMESPACE, example["id"])) not in existing_ids
    ]

    if not pending:
        print(f"Dataset '{DATASET_NAME}' already contains all demo examples.")
        return

    # Convert string IDs to UUIDs for the API call
    ids = [uuid.uuid5(LANGSMITH_NAMESPACE, e["id"]) for e in pending]

    client.create_examples(
        dataset_id=str(dataset.id),
        inputs=[e["inputs"] for e in pending],
        outputs=[e.get("outputs") for e in pending],
        metadata=[e.get("metadata") for e in pending],
        ids=ids,
    )
    print(f"Added {len(pending)} examples to '{DATASET_NAME}'.")


if __name__ == "__main__":
    main()
