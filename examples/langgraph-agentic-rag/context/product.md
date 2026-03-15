# Product Context

## What This Product Does

This is a retrieval-augmented generation agent that answers questions about Lilian Weng's machine learning research blog (lilianweng.github.io). The agent retrieves relevant passages from three blog posts on reward hacking, hallucination in LLMs, and diffusion models for video generation, then synthesises a concise answer grounded in those passages.

## Who Uses It

The primary users are ML researchers, AI practitioners, and graduate students who want quick, accurate answers about recent developments in reinforcement learning alignment, LLM factuality, and generative video models. They are technically literate and expect precise answers with no fabrication.

## The Agent's Role

The agent is a research assistant. It should answer factual questions grounded in the retrieved blog content, admit uncertainty when the retrieved passages do not support an answer, rewrite vague queries to improve retrieval precision, and respond naturally to casual or conversational turns without forcing a retrieval call.

## Stakes and Sensitivity

Mistakes are medium-stakes. The domain is technical research. An incorrect answer about a taxonomy, a named method, or a specific empirical finding can mislead practitioners who rely on the blog for technical grounding. Fabricated citations or confident answers on unsupported questions damage trust.

## Domain Vocabulary

- reward hacking
- reward tampering
- environment or goal misspecification
- specification gaming
- sycophancy
- hallucination
- extrinsic hallucination
- in-context hallucination
- FActScore
- SelfCheckGPT
- atomic facts
- calibration
- attribution
- diffusion model
- temporal consistency
- v-parameterization
- Lumiere
- Imagen Video
- Stable Video Diffusion
- Text2Video-Zero
