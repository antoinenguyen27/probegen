from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_BASE_DIR = ROOT / "knowledge_base"
PROMPTS_DIR = ROOT / "prompts"


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class RetrievalDecision(BaseModel):
    next_step: Literal["generate_answer", "rewrite_question"] = Field(
        description="Generate an answer when the retrieved context is relevant, otherwise rewrite the question."
    )
    rationale: str


def _prompt(path: Path, **values: str) -> str:
    return path.read_text(encoding="utf-8").format(**values)


def _latest_user_question(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    raise ValueError("Agent state does not contain a user question")


def _latest_retrieval_context(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            return str(message.content)
    return "No relevant documents were retrieved."


def _model() -> ChatOpenAI:
    return ChatOpenAI(model=os.environ.get("RAG_MODEL", "gpt-4.1-mini"), temperature=0)


@lru_cache(maxsize=1)
def _vector_store() -> InMemoryVectorStore:
    embeddings = OpenAIEmbeddings()
    vector_store = InMemoryVectorStore(embeddings)
    vector_store.add_documents(_load_documents())
    return vector_store


def _load_documents() -> list[Document]:
    documents: list[Document] = []
    for path in sorted(KNOWLEDGE_BASE_DIR.glob("*.md")):
        documents.append(
            Document(
                page_content=path.read_text(encoding="utf-8").strip(),
                metadata={"source": path.name},
            )
        )
    return documents


@tool(response_format="content_and_artifact")
def retrieve_knowledge(query: str) -> tuple[str, list[Document]]:
    """Retrieve the most relevant Acme knowledge-base passages for the current user question."""

    documents = _vector_store().similarity_search(query, k=3)
    if not documents:
        return "No relevant documents were retrieved.", []

    rendered = []
    for document in documents:
        rendered.append(
            "\n".join(
                [
                    f"SOURCE: {document.metadata['source']}",
                    document.page_content,
                ]
            )
        )
    return "\n\n".join(rendered), documents


def query_or_respond(state: AgentState) -> dict[str, list[AIMessage]]:
    system_prompt = _prompt(PROMPTS_DIR / "query_or_respond.md")
    response = _model().bind_tools([retrieve_knowledge]).invoke(
        [SystemMessage(content=system_prompt), *state["messages"]]
    )
    return {"messages": [response]}


def grade_documents(state: AgentState) -> Literal["generate_answer", "rewrite_question"]:
    question = _latest_user_question(state["messages"])
    context = _latest_retrieval_context(state["messages"])
    grader = _model().with_structured_output(RetrievalDecision)
    decision = grader.invoke(
        [
            SystemMessage(
                content=_prompt(
                    PROMPTS_DIR / "grade.md",
                    question=question,
                    context=context,
                )
            )
        ]
    )
    return decision.next_step


def rewrite_question(state: AgentState) -> dict[str, list[HumanMessage]]:
    rewritten = _model().invoke(
        [
            SystemMessage(
                content=_prompt(
                    PROMPTS_DIR / "rewrite.md",
                    question=_latest_user_question(state["messages"]),
                    context=_latest_retrieval_context(state["messages"]),
                )
            )
        ]
    )
    return {"messages": [HumanMessage(content=str(rewritten.content).strip())]}


def generate_answer(state: AgentState) -> dict[str, list[AIMessage]]:
    answer = _model().invoke(
        [
            SystemMessage(
                content=_prompt(
                    PROMPTS_DIR / "answer.md",
                    question=_latest_user_question(state["messages"]),
                    context=_latest_retrieval_context(state["messages"]),
                )
            )
        ]
    )
    return {"messages": [AIMessage(content=str(answer.content).strip())]}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("query_or_respond", query_or_respond)
    graph.add_node("retrieve", ToolNode([retrieve_knowledge]))
    graph.add_node("rewrite_question", rewrite_question)
    graph.add_node("generate_answer", generate_answer)

    graph.add_edge(START, "query_or_respond")
    graph.add_conditional_edges("query_or_respond", tools_condition, {"tools": "retrieve", END: END})
    graph.add_conditional_edges(
        "retrieve",
        grade_documents,
        {
            "generate_answer": "generate_answer",
            "rewrite_question": "rewrite_question",
        },
    )
    graph.add_edge("rewrite_question", "query_or_respond")
    graph.add_edge("generate_answer", END)
    return graph.compile()


def run(question: str) -> str:
    result = build_graph().invoke({"messages": [HumanMessage(content=question)]})
    for message in reversed(result["messages"]):
        if isinstance(message, AIMessage) and not message.tool_calls and message.content:
            return str(message.content)
    raise ValueError("Graph completed without a final assistant message")
