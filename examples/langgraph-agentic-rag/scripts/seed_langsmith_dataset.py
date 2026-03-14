from __future__ import annotations

from typing import Any

from langsmith import Client
from dotenv import load_dotenv

load_dotenv()

DATASET_NAME = "acme-rag-baseline"

EXAMPLES: list[dict[str, Any]] = [
    {
        "id": "acme-export-retention",
        "inputs": {"query": "How long are exports available after I create one?"},
        "outputs": {"expected_behavior": "States that exports remain available for 7 days and cites [data_exports.md]."},
        "metadata": {
            "rubric": "The answer mentions the 7 day retention window and cites [data_exports.md].",
            "assertion_type": "llm_rubric",
            "tags": ["retrieval", "exports"],
        },
    },
    {
        "id": "acme-billing-owner",
        "inputs": {"query": "Who can change the billing owner?"},
        "outputs": {"expected_behavior": "States that only workspace owners can change the billing owner and cites [team_billing.md]."},
        "metadata": {
            "rubric": "The answer says only workspace owners can transfer billing ownership and cites [team_billing.md].",
            "assertion_type": "llm_rubric",
            "tags": ["retrieval", "billing"],
        },
    },
    {
        "id": "acme-sso-contractors",
        "inputs": {"query": "Can I require SSO for contractors?"},
        "outputs": {"expected_behavior": "Confirms that workspace owners can require SSO for contractors and cites [account_security.md]."},
        "metadata": {
            "rubric": "The answer says SSO can be required for contractors on Scale or Enterprise and cites [account_security.md].",
            "assertion_type": "llm_rubric",
            "tags": ["retrieval", "security"],
        },
    },
    {
        "id": "acme-guest-sso-followup",
        "inputs": {
            "messages": [
                {"role": "user", "content": "Can I require SSO for contractors?"},
                {"role": "assistant", "content": "Yes. Workspace owners can require SSO for all members, including contractors. [account_security.md]"},
                {"role": "user", "content": "What about guests?"},
            ]
        },
        "outputs": {"expected_behavior": "Explains that guests still authenticate through the configured identity provider and cites [account_security.md]."},
        "metadata": {
            "rubric": "The answer keeps the conversation grounded in [account_security.md] and addresses guests specifically.",
            "assertion_type": "llm_rubric",
            "tags": ["retrieval", "security", "multi-turn"],
        },
    },
    {
        "id": "acme-unsupported-payroll",
        "inputs": {"query": "Can Acme process payroll for contractors?"},
        "outputs": {"expected_behavior": "States that the assistant does not have enough information and does not invent a citation."},
        "metadata": {
            "rubric": "The answer admits missing knowledge and avoids fabricated citations.",
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
            description="Baseline eval set for the Probegen LangGraph agentic RAG demo.",
        )

    existing_ids = {str(example.id) for example in client.list_examples(dataset_id=str(dataset.id), limit=200)}
    pending = [example for example in EXAMPLES if example["id"] not in existing_ids]

    if not pending:
        print(f"Dataset '{DATASET_NAME}' already contains all demo examples.")
        return

    client.create_examples(dataset_id=str(dataset.id), examples=pending)
    print(f"Added {len(pending)} examples to '{DATASET_NAME}'.")


if __name__ == "__main__":
    main()
