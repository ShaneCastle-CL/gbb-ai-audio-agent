import time
from typing import Optional
from opentelemetry import trace
from opentelemetry.trace import Span

tracer = trace.get_tracer(__name__)

class TraceContext:
    """
    Context manager for tracing spans with custom attributes and latency bucketing.
    """

    def __init__(
        self,
        name: str,
        call_connection_id: Optional[str] = None,
        session_id: Optional[str] = None,
        test_case: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        self.name = name
        self.call_connection_id = call_connection_id
        self.session_id = session_id
        self.test_case = test_case
        self.metadata = metadata or {}
        self._start_time = None
        self._span: Optional[Span] = None

    def __enter__(self):
        self._start_time = time.time()
        self._span = tracer.start_span(name=self.name)

        # Attach custom span attributes
        if self.call_connection_id:
            self._span.set_attribute("custom.call_connection_id", self.call_connection_id)
        if self.session_id:
            self._span.set_attribute("custom.session_id", self.session_id)
        if self.test_case:
            self._span.set_attribute("custom.test_case", self.test_case)
        for k, v in self.metadata.items():
            self._span.set_attribute(f"custom.{k}", v)

        self._span.__enter__()
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (time.time() - self._start_time) * 1000  # in ms
        if self._span:
            self._span.set_attribute("custom.latency_ms", duration)
            self._span.set_attribute("custom.latency_bucket", self._bucket_latency(duration))
            self._span.__exit__(exc_type, exc_val, exc_tb)

    @staticmethod
    def _bucket_latency(duration_ms: float) -> str:
        if duration_ms < 100:
            return "<100ms"
        elif duration_ms < 300:
            return "100–300ms"
        elif duration_ms < 1000:
            return "300ms–1s"
        elif duration_ms < 3000:
            return "1–3s"
        else:
            return ">3s"