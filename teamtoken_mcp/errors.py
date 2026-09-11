"""Turning gateway errors into sentences a model can act on.

The reader of a tool error is an LLM that will paraphrase it to a human and then
decide what to do next. `insufficient balance` tells it nothing actionable;
"you need $0.40 and have $0.12, top up in the cabinet" tells it both the cause
and the fix. So every error that has a known remedy gets one, in the same
sentence — never a bare provider code.

The code table mirrors the "media errors" section of the published TeamToken API
guide; adding or renaming a code here means re-reading that section, because a
remedy that no longer matches the gateway is worse than none.
"""
from __future__ import annotations

# code -> what the model should tell the user / do next.
_REMEDIES: dict[str, str] = {
    "EMPTY_PROMPT": "the prompt is empty or shorter than 10 characters — send a longer, more specific prompt",
    "INVALID_VIDEO_FILE": "this model edits an existing video, so a `video` input is required — pass a URL or base64 video",
    "FILE_TOO_LARGE": "the input file is too large (images up to 10 MB, videos up to 100 MB) — downscale it first",
    "FILE_TYPE_NOT_ALLOWED": "unsupported input format — images must be JPG or PNG, videos MP4, MOV or WebM",
    "INVALID_INPUT": "one of the parameters is out of range or of the wrong type — check duration, aspect_ratio and resolution against list_models",
    "VIDEO_DURATION_TOO_LONG": "the input video is longer than this model accepts — trim it and retry",
    "SERVICE_PRICE_NOT_FOUND": "this model, or this combination of options, is not offered — call list_models and pick a model from it",
    "NOT_ENOUGH_CREDIT": "the account ran out of balance mid-generation — top up and retry",
    "GEMINI_RAI_MEDIA_FILTERED": "the provider's safety filter blocked this content — rephrase the prompt or use a different subject",
    "KLING_GENERATION_FAILED": "generation failed at the provider — for motion control the character in the input image must be clearly visible; also check the content policy",
    "SEEDANCE_GENERATION_FAILED": "generation failed at the provider — check the input and prompt, then retry",
    "SYSTEM_ERROR": "a temporary provider failure — retry in a few seconds",
}

# HTTP status -> remedy, used when the gateway sent no provider code.
_STATUS_REMEDIES: dict[int, str] = {
    401: "the API key is missing or invalid — the user must supply a valid TeamToken key",
    402: "the account balance is too low for this request — call get_balance to see the shortfall, then top up",
    400: "the request was rejected as invalid — check the model name and required fields",
    # 404 is not only a job lookup: the gateway answers an unknown key with the
    # same code. Naming "someone else's job" in a reply about the balance was
    # misleading — it points at a cause the request could not have had.
    404: "not found — if this was a job lookup, the id is wrong or belongs to a different key",
    429: "rate limited — wait a few seconds and retry",
    502: "the upstream provider failed — retry, or try a different model",
}


class TeamTokenError(RuntimeError):
    """A gateway failure, already phrased for the model that will read it."""

    def __init__(self, message: str, status: int | None = None,
                 code: str | None = None, job_id: str | None = None):
        self.status = status
        self.code = code
        self.job_id = job_id
        super().__init__(message)


def humanize(message: str, status: int | None, code: str | None,
             job_id: str | None = None) -> str:
    """Compose provider message + remedy + the job id that must not be lost."""
    parts = [message.strip() or "the request failed"]
    remedy = _REMEDIES.get(code or "") or _STATUS_REMEDIES.get(status or 0)
    if remedy and remedy.lower() not in parts[0].lower():
        parts.append(remedy)
    # An ambiguous submit (the gateway parked a job it may still finalize and
    # charge for) carries an id. Surfacing it is the difference between the model
    # polling that job and the model cheerfully re-queuing a second paid
    # generation — the user would pay twice for one picture.
    if job_id:
        parts.append(
            f"a job was already created with id {job_id}; it may still finish and be billed — "
            f"call get_job with this id instead of generating again")
    text = ". ".join(p.rstrip(".") for p in parts) + "."
    return f"[{code}] {text}" if code else text


def envelope(body: object) -> tuple[str, str | None, str | None]:
    """Pull (message, code, job_id) out of the gateway's {"error": {...}} shape.

    Defensive on purpose: this runs on the failure path, where the body is least
    likely to have the shape the happy path assumes.
    """
    message, code, job_id = "the request failed", None, None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or message)
            code = err.get("code") or None
            job_id = err.get("id") or None
        elif isinstance(err, str):
            message = err
        elif "message" in body:
            message = str(body["message"])
            code = body.get("code") or None
        job_id = job_id or body.get("id") or None
    return message, code, (str(job_id) if job_id else None)
