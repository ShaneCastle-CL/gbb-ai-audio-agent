<!-- markdownlint-disable MD033 MD041 -->

# 🎙️ **RTAgent**  
*Omni-channel, real-time voice-intelligence accelerator framework on Azure*

**RTAgent** is an accelerator that delivers a friction-free, AI-driven voice experience—whether callers dial a phone number, speak to an IVR, or click “Call Me” in a web app. Built entirely on generally available Azure services—Azure Communication Services, Azure AI, and Azure App Service—it provides a low-latency stack that scales on demand while keeping the AI layer fully under your control.

Design a single agent or orchestrate multiple specialist agents (claims intake, authorization triage, appointment scheduling—anything). The framework allows you to build your voice agent from scratch, incorporate long- and short-term memory, configure actions, and fine-tune your TTS and STT layers to give any workflow an intelligent voice.

## **Overview** 

<img src="utils/images/RTAGENT.png" align="right" height="180" alt="RTAgent Logo" />

> **88 %** of customers still make a **phone call** when they need real support  
> — yet most IVRs feel like 1999. **RTAgent** fixes that.

**RTAgent in a nutshell**

RT Agent is a plug-and-play accelerator, voice-to-voice AI pipeline that slots into any phone line, web client, or CCaaS flow. Caller audio arrives through Azure Communication Services (ACS), is transcribed by a dedicated STT component, routed through your agent chain of LLMs, tool calls, and business logic, then re-synthesised by a TTS component—all in a sub-second round-trip. Because each stage runs as an independent microservice, you can swap models, fine-tune latency budgets, or inject custom logic without touching the rest of the stack. The result is natural, real-time conversation with precision control over every hop of the call.

<img src="utils/images/RTAgentArch.png" alt="RTAgent Logo" />

## **Getting Started**

### Local Quick-Start

```bash
# 1️⃣ Backend (FastAPI + Uvicorn)
git clone https://github.com/your-org/gbb-ai-audio-agent.git
cd gbb-ai-audio-agent/rtagents/RTAgent/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env   # add ACS, Speech, OpenAI keys
python server.py      # ws://localhost:8010/realtime
```

```bash
# 2️⃣ Frontend (Vite + React)
cd ../../frontend
npm install
npm run dev           # http://localhost:5173
```

Dial-in from a real phone? Expose your backend with **Azure Dev Tunnels**, update `BASE_URL` in both `.env` files, and mirror the URL in the ACS event subscription.

## **Deployment on Azure**

```bash
azd auth login
azd up         # full infra + code (~15 min)
```

• SSL via Key Vault ‑> App Gateway  
• Container Apps auto-scale (KEDA)  
• Private Redis, Cosmos DB, OpenAI endpoints  

Step-by-step guide: `docs/DeploymentGuide.md`.

## **Load & Chaos Testing**

Targets: **<500 ms STT→TTS • 1k+ concurrent calls • >99.5 % success** (WIP)

```bash
az load test run --test-plan tests/load/azure-load-test.yaml
```

Locust & Artillery scripts: `docs/LoadTesting.md`.


## **Repository Layout**
```text
gbb-ai-audio-agent/
├── .github/          # CI / CD
├── docs/             # Architecture, Deployment, Integration
├── infra/            # Bicep modules & azd templates
├── rtagents/         # Core Python package (agents, tools, router) [backend + Frontend (React + Vite frontend)]
├── labs/             # Jupyter notebooks & PoCs
├── src/              # source code libraries
├── tests/            # pytest + load tests
├── utils/            # diagrams & helper scripts
└── Makefile, docker-compose.yml, CHANGELOG.md …
```

## **Roadmap**
- Live Agent API integration
- Multi-modal agents (docs + images)  

## **Contributing**
PRs & issues welcome—see `CONTRIBUTING.md` and run `make pre-commit` before pushing.

## **License & Disclaimer**
Released under MIT. This sample is **not** an official Microsoft product—validate compliance (HIPAA, PCI, GDPR, etc.) before production use.

<br>

> [!IMPORTANT]  
> This software is provided for demonstration purposes only. It is not intended to be relied upon for any production workload. The creators of this software make no representations or warranties of any kind, express or implied, about the completeness, accuracy, reliability, suitability, or availability of the software or related content. Any reliance placed on such information is strictly at your own risk.