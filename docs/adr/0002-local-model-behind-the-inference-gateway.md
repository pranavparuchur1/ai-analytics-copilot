# ADR 0002: A local 7B coder model, served through the inference-gateway

Status: accepted

## Context
The copilot needs a model that writes PostgreSQL. The options were a hosted frontier model or
a local open-weights model on the 8 GB laptop GPU. The previous project
(`inference-gateway`) already provides auth, rate limits, usage tracking and routing in front
of vLLM workers.

## Decision
Qwen2.5-Coder-7B-Instruct (AWQ 4-bit) on vLLM, behind the gateway, called through its
OpenAI-compatible API. The client (`llm.py`) only knows a base URL, key and model name, so a
hosted model can be swapped in by configuration.

## Alternatives considered
- Qwen2.5-Coder-3B (AWQ): measured at 57% execution accuracy vs 87.5% for 7B on the same
  56 questions, for about 40% lower median latency. Not worth it.
- Hosted frontier model: likely more accurate, but costs money per question and sends the
  schema and questions to a third party. Possible via configuration; not measured here.

## Consequences
- No per-question cost, and no data leaves the machine.
- 7B AWQ plus a 768 MiB KV cache uses about 7 GB of the 8 GB GPU, so only one coder model can
  be loaded at a time, and the gateway's chat tiers cannot run alongside it.
- The gateway's per-key usage table records the copilot's token consumption.
