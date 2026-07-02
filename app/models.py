from pydantic import BaseModel, HttpUrl, field_validator


class ProbeRequest(BaseModel):
    # HttpUrl validates the string is a well-formed URL at request time,
    # so we never start a polling job against a garbage target
    url: HttpUrl
    interval_seconds: float

    @field_validator("interval_seconds")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        # Reject zero or negative intervals upfront; a 0-second interval
        # would spin the event loop into a busy-wait without sleeping
        if v <= 0:
            raise ValueError("interval_seconds must be > 0")
        return v


class ProbeResponse(BaseModel):
    job_id: str
    url: str
    interval_seconds: float
    status: str


class ProbeResult(BaseModel):
    job_id: str
    url: str
    timestamp: str
    status_code: int | None
    latency_ms: float | None
    # error is None on success; a string description on any failure
    error: str | None
