"""Client for tts-service: ``POST /stream`` returns raw PCM (s16le, 24 kHz, mono) as it is synthesized."""
import httpx


class TtsClient:
    def __init__(self, base_url, sample_rate=24000, connect_timeout=3.0, read_timeout=60.0):
        self.base_url = base_url.rstrip('/')
        self.sample_rate = sample_rate
        self.client = httpx.Client(base_url=self.base_url,
                                   timeout=httpx.Timeout(read_timeout, connect=connect_timeout))

    def health(self):
        try:
            return self.client.get('/health', timeout=1.0).json()
        except Exception:
            return None

    def stream(self, text, chunk_bytes=4800):
        """Yield PCM chunks; closing the generator early closes the request, which cancels the synthesis."""
        with self.client.stream('POST', '/stream', json={'text': text}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_bytes):
                if chunk:
                    yield chunk

    def synthesize(self, text) -> bytes:
        return b''.join(self.stream(text))
