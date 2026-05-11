"""Band Guesser — sample app demonstrating coolhand-python monitoring."""

import asyncio
import json
import os
import subprocess
import sys

if sys.version_info < (3, 8):
    sys.exit(
        "Python 3.8+ required. Run with: python3.11 -m uvicorn main:app --reload --port 8188"
    )

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# structlog — configure before coolhand so basicConfig is never called
# ---------------------------------------------------------------------------
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
)

# ---------------------------------------------------------------------------
# OpenTelemetry — instrument before coolhand to test worst-case patch ordering.
# TracerProvider has no exporter so spans are silently discarded; the point is
# that OTel's httpx + requests patches are applied *before* coolhand's patch.
# ---------------------------------------------------------------------------
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.trace import TracerProvider

otel_trace.set_tracer_provider(TracerProvider())
RequestsInstrumentor().instrument()  # patches requests before coolhand
HTTPXClientInstrumentor().instrument()  # patches httpx before coolhand

# ---------------------------------------------------------------------------
# coolhand — imported after OTel to exercise the double-patch scenario.
# silent=True: skip logging.basicConfig so structlog owns logging config.
# ---------------------------------------------------------------------------
import coolhand

_ch = coolhand.Coolhand(
    api_key=os.getenv("COOLHAND_API_KEY"),
    intercept_addresses=["models.inference.ai.azure.com"],
    silent=True,
)

import httpx  # noqa: E402
from azure.ai.inference.models import SystemMessage, UserMessage  # noqa: E402
from azure.core.credentials import AzureKeyCredential  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402
from pydantic import BaseModel  # noqa: E402

logger = structlog.get_logger(__name__)

app = FastAPI(title="Band Guesser")
FastAPIInstrumentor.instrument_app(app)

_HERE = os.path.dirname(os.path.abspath(__file__))
_INDEX_HTML = os.path.join(_HERE, "templates", "index.html")

SYSTEM_PROMPT = (
    "You are a music expert. When given a sentence describing a person, return ONLY "
    "a JSON array of exactly 8 band or artist names you think they would enjoy, "
    "based on writing style, vocabulary, and tone. "
    "No explanation, no markdown, no prose — only the JSON array."
)


async def _resolve_github_token(provided: str) -> str:
    """Return provided token if non-empty, otherwise fall back to `gh auth token`."""
    if provided.strip():
        return provided.strip()
    try:
        token = await asyncio.to_thread(
            subprocess.check_output, ["gh", "auth", "token"], text=True
        )
        if token.strip():
            return token.strip()
    except Exception:
        pass
    raise HTTPException(
        status_code=400,
        detail="No GitHub token provided and `gh auth token` is unavailable. "
        "Run `gh auth login` or paste a token above.",
    )


class GuessBandsRequest(BaseModel):
    github_token: str = ""
    sentence: str
    mode: str = "copilot"


class GuessBandsResponse(BaseModel):
    bands: list
    raw_response: str


class SubmitFeedbackRequest(BaseModel):
    raw_response: str
    liked_bands: list
    all_bands: list


class SubmitFeedbackResponse(BaseModel):
    success: bool
    message: str


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------


async def _guess_via_azure_sdk(github_token: str, sentence: str) -> str:
    """Call GitHub Models via azure-ai-inference ChatCompletionsClient (requests transport).

    This path is NOT intercepted by coolhand's httpx patcher — use it to test issue #12.
    """
    import asyncio

    from azure.ai.inference import ChatCompletionsClient

    print(
        "[AZURE-AI-INFERENCE SDK] Making inference call via ChatCompletionsClient (requests transport)"
    )

    def _sync():
        client = ChatCompletionsClient(
            endpoint="https://models.inference.ai.azure.com",
            credential=AzureKeyCredential(github_token),
        )
        resp = client.complete(
            model="gpt-4o-mini",
            messages=[
                SystemMessage(content=SYSTEM_PROMPT),
                UserMessage(
                    content=f'Here is what the person wrote about themselves: "{sentence}"'
                ),
            ],
            temperature=0.9,
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        err = str(e)
        if "401" in err or "unauthorized" in err.lower():
            raise HTTPException(
                status_code=401, detail="Invalid or expired GitHub token."
            )
        if "429" in err or "rate limit" in err.lower():
            raise HTTPException(
                status_code=429, detail="Rate limit reached — please try again shortly."
            )
        raise HTTPException(
            status_code=502, detail=f"Azure AI Inference SDK error: {err}"
        )


async def _guess_via_azure(github_token: str, sentence: str) -> str:
    """Call GitHub Models via azure-core credential + httpx transport (intercepted by coolhand)."""
    print("[AZURE HTTPX] Making inference call via models.inference.ai.azure.com")
    cred = AzureKeyCredential(github_token)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        UserMessage(
            content=f'Here is what the person wrote about themselves: "{sentence}"'
        ),
    ]
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://models.inference.ai.azure.com/chat/completions",
                headers={"Authorization": f"Bearer {cred.key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [m.as_dict() for m in messages],
                    "temperature": 0.9,
                    "max_tokens": 200,
                },
                timeout=30.0,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502, detail=f"Azure inference connection error: {exc}"
            )

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired GitHub token.")
    if resp.status_code == 429:
        raise HTTPException(
            status_code=429, detail="Rate limit reached — please try again shortly."
        )
    if not resp.is_success:
        raise HTTPException(
            status_code=502, detail=f"GitHub Models API error: HTTP {resp.status_code}"
        )

    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


async def _guess_via_copilot(github_token: str, sentence: str) -> str:
    """Call GitHub Copilot via github-copilot-sdk (intercepted by coolhand's copilot interceptor)."""
    try:
        from copilot import CopilotClient, SubprocessConfig
        from copilot.generated.session_events import AssistantMessageData
        from copilot.session import PermissionHandler, SystemMessageReplaceConfig
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="github-copilot-sdk is not installed. Run: pip install github-copilot-sdk",
        )

    print(
        "[COPILOT SDK] Making inference call via github-copilot-sdk (JSON-RPC over stdio)"
    )

    chunks = []

    def on_event(event):
        if isinstance(event.data, AssistantMessageData):
            chunks.append(event.data.content)

    config = SubprocessConfig(github_token=github_token, log_level="warning")

    try:
        async with CopilotClient(config) as client:
            async with await client.create_session(
                on_permission_request=PermissionHandler.approve_all,
                system_message=SystemMessageReplaceConfig(
                    mode="replace",
                    content=SYSTEM_PROMPT,
                ),
                on_event=on_event,
            ) as session:
                await session.send_and_wait(
                    f'Here is what the person wrote about themselves: "{sentence}"',
                    timeout=30,
                )
    except Exception as e:
        err = str(e)
        print(f"[COPILOT SDK ERROR] {type(e).__name__}: {err}")
        if "401" in err or "unauthorized" in err.lower() or "auth" in err.lower():
            raise HTTPException(
                status_code=401, detail="Invalid or expired GitHub token."
            )
        raise HTTPException(status_code=502, detail=f"Copilot SDK error: {err}")

    return "".join(chunks)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open(_INDEX_HTML) as f:
        return HTMLResponse(f.read())


@app.post("/api/guess-bands", response_model=GuessBandsResponse)
async def guess_bands(body: GuessBandsRequest):
    github_token = await _resolve_github_token(body.github_token)
    if not body.sentence.strip():
        raise HTTPException(
            status_code=400, detail="Please write something about yourself."
        )
    if len(body.sentence) > 2000:
        raise HTTPException(
            status_code=400, detail="Sentence too long (max 2000 characters)."
        )

    try:
        if body.mode == "azure":
            raw = await _guess_via_azure(github_token, body.sentence)
        elif body.mode == "azure-sdk":
            raw = await _guess_via_azure_sdk(github_token, body.sentence)
        else:
            raw = await _guess_via_copilot(github_token, body.sentence)
    except HTTPException:
        raise
    except Exception as e:
        err = str(e)
        if "401" in err or "Unauthorized" in err or "authentication" in err.lower():
            raise HTTPException(
                status_code=401, detail="Invalid or expired GitHub token."
            )
        if "429" in err or "rate limit" in err.lower():
            raise HTTPException(
                status_code=429, detail="Rate limit reached — please try again shortly."
            )
        raise HTTPException(status_code=502, detail=f"Inference error: {err}")

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        bands = json.loads(cleaned)
        if not isinstance(bands, list) or len(bands) == 0:
            raise ValueError("Expected a non-empty list")
        bands = [str(b) for b in bands[:8]]
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="The model returned an unexpected format. Please try again.",
        )

    return GuessBandsResponse(bands=bands, raw_response=raw)


@app.post("/api/submit-feedback", response_model=SubmitFeedbackResponse)
async def submit_feedback(body: SubmitFeedbackRequest):
    liked_count = len(body.liked_bands)
    total_count = len(body.all_bands) if body.all_bands else 1

    like = liked_count >= (total_count / 2)

    disliked = [b for b in body.all_bands if b not in body.liked_bands]
    liked_str = ", ".join(body.liked_bands) if body.liked_bands else "none"
    disliked_str = ", ".join(disliked) if disliked else "none"
    explanation = f"Liked: {liked_str}. Disliked: {disliked_str}."

    try:
        _ch.create_feedback(
            {
                "original_output": body.raw_response,
                "like": like,
                "explanation": explanation,
            }
        )
    except Exception as e:
        logger.warning("feedback_failed", error=str(e))
        return SubmitFeedbackResponse(success=False, message=str(e))

    return SubmitFeedbackResponse(
        success=True, message="Feedback submitted successfully."
    )
