from __future__ import annotations

import argparse

from dotenv import load_dotenv

load_dotenv()

from app.graph import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LangGraph agentic RAG demo.")
    parser.add_argument("question", help="Question to ask the demo agent")
    args = parser.parse_args()
    print(run(args.question))


if __name__ == "__main__":
    main()
