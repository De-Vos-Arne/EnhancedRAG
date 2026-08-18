"""
Model clients.

Both embedding and generation go through a named preset from settings, so
swapping models is a string — `--generator deepseek` on the CLI, or the
dropdown in the chat header. Nothing above this layer knows which vendor
is behind the call.
"""

from __future__ import annotations

import numpy as np
import requests

from .. import settings


class ModelError(RuntimeError):
    """Raised with a message meant to be shown to the person."""


# ── Embeddings ─────────────────────────────────────────────────────────

class Embedder:
    def __init__(self, name: str | None = None):
        self.cfg = settings.embedder(name)
        self.key = self.cfg["key"]
        self.model = self.cfg["model"]
        self.dims = self.cfg["dims"]
        self.base_url = self.cfg["base_url"]

    def check(self):
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
        except Exception:
            raise ModelError(
                f"Cannot reach Ollama at {self.base_url}. Start it with "
                f"`ollama serve`, or set OLLAMA_URL if it runs elsewhere.")
        names = [m["name"] for m in r.json().get("models", [])]
        if not any(n.startswith(self.model) for n in names):
            raise ModelError(f"Embedding model '{self.model}' is not pulled. "
                             f"Run:  ollama pull {self.model}")

    def embed(self, texts: list[str], batch: int = 32) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            try:
                r = requests.post(f"{self.base_url}/api/embed",
                                  json={"model": self.model, "input": chunk},
                                  timeout=300)
                r.raise_for_status()
            except requests.HTTPError as e:
                raise ModelError(
                    f"Embedding call failed ({e.response.status_code}). "
                    f"Check that '{self.model}' is pulled.") from e
            except requests.RequestException as e:
                raise ModelError(
                    f"Cannot reach Ollama at {self.base_url}. Start it with "
                    f"`ollama serve`, or set OLLAMA_URL if it runs elsewhere. ({e})") from e
            out.extend(r.json()["embeddings"])
        arr = np.asarray(out, dtype=np.float32)
        if arr.shape[1] != self.dims:
            raise ModelError(
                f"'{self.model}' returned {arr.shape[1]}-dim vectors but "
                f"settings say {self.dims}. Fix the dims in settings.EMBEDDERS.")
        return arr

    def embed_one(self, text: str) -> np.ndarray:
        v = self.embed([text])[0]
        return v / (np.linalg.norm(v) or 1.0)


# ── Generation ─────────────────────────────────────────────────────────

class Generator:
    def __init__(self, name: str | None = None):
        self.cfg = settings.generator(name)
        self.key = self.cfg["key"]
        self.label = self.cfg["label"]
        self.model = self.cfg["model"]
        self.backend = self.cfg["backend"]
        self.base_url = self.cfg["base_url"]
        self.api_key = self.cfg["api_key"]
        self.context_window = self.cfg.get("context_window", 8192)
        self.gen_params = self.cfg.get("gen_params", {})

    def _unreachable_hint(self) -> str:
        """A local OpenAI-compatible server refusing the connection is
        almost always 'the app is open but its API server isn't started' —
        the single most common demo-day surprise with LM Studio (the app
        running is not the same as the server running). Give the exact fix
        instead of a bare connection-refused trace."""
        if "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            return ""
        return (" This looks like a local server — if it's LM Studio: "
                "`lms ps` (see what's actually loaded — unload duplicates "
                "with `lms unload --all`, a stale extra instance can "
                "silently answer instead of the one you just loaded), then "
                "`lms server start` and "
                f"`lms load {self.model} -c {self.context_window} -y` "
                "(context must match — the wrong default can cause answers "
                "to cut off mid-generation instead of erroring outright).")

    def complete(self, messages: list[dict]) -> str:
        if self.backend == "openai":
            return self._openai(messages)
        return self._ollama(messages)

    def complete_stream(self, messages: list[dict]):
        """Yield text deltas as they arrive, instead of blocking for the
        full completion — the local models here can take a minute-plus on
        a large context, and watching tokens land beats a blank spinner."""
        if self.backend == "openai":
            yield from self._openai_stream(messages)
        else:
            yield from self._ollama_stream(messages)

    def _openai_stream(self, messages):
        import json as _json
        if not self.api_key:
            raise ModelError(
                f"'{self.key}' needs an API key. Put it in your .env file "
                f"(see .env.example), then restart.")
        if not self.base_url:
            raise ModelError(f"'{self.key}' has no base URL configured.")
        try:
            r = requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages,
                      "temperature": settings.TEMPERATURE,
                      "max_tokens": settings.MAX_TOKENS, "stream": True,
                      **self.gen_params},
                timeout=300, stream=True)
            r.raise_for_status()
        except requests.HTTPError as e:
            raise ModelError(f"{self.label} returned "
                             f"{e.response.status_code}: "
                             f"{e.response.text[:200]}") from e
        except requests.RequestException as e:
            raise ModelError(f"Cannot reach {self.base_url}: {e}.{self._unreachable_hint()}") from e
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload.strip() == "[DONE]":
                break
            try:
                delta = _json.loads(payload)["choices"][0]["delta"].get("content")
            except (KeyError, IndexError, ValueError):
                continue
            if delta:
                yield delta

    def _ollama_stream(self, messages):
        import json as _json
        try:
            r = requests.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": True,
                      "options": {"temperature": settings.TEMPERATURE,
                                  "num_predict": settings.MAX_TOKENS,
                                  "num_ctx": self.context_window}},
                timeout=900, stream=True)
            r.raise_for_status()
        except requests.HTTPError as e:
            raise ModelError(
                f"Generation failed. Is '{self.model}' pulled? "
                f"Run:  ollama pull {self.model}") from e
        except requests.RequestException as e:
            raise ModelError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Start it with `ollama serve`. ({e})") from e
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                chunk = _json.loads(line)
            except ValueError:
                continue
            delta = chunk.get("message", {}).get("content")
            if delta:
                yield delta
            if chunk.get("done"):
                break

    def _openai(self, messages):
        if not self.api_key:
            raise ModelError(
                f"'{self.key}' needs an API key. Put it in your .env file "
                f"(see .env.example), then restart.")
        if not self.base_url:
            raise ModelError(f"'{self.key}' has no base URL configured.")
        try:
            r = requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages,
                      "temperature": settings.TEMPERATURE,
                      "max_tokens": settings.MAX_TOKENS,
                      **self.gen_params},
                timeout=300)
            r.raise_for_status()
        except requests.HTTPError as e:
            raise ModelError(f"{self.label} returned "
                             f"{e.response.status_code}: "
                             f"{e.response.text[:200]}") from e
        except requests.RequestException as e:
            raise ModelError(f"Cannot reach {self.base_url}: {e}.{self._unreachable_hint()}") from e
        return r.json()["choices"][0]["message"]["content"]

    def _ollama(self, messages):
        try:
            r = requests.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False,
                      "options": {"temperature": settings.TEMPERATURE,
                                  "num_predict": settings.MAX_TOKENS,
                                  "num_ctx": self.context_window}},
                timeout=900)
            r.raise_for_status()
        except requests.HTTPError as e:
            raise ModelError(
                f"Generation failed. Is '{self.model}' pulled? "
                f"Run:  ollama pull {self.model}") from e
        except requests.RequestException as e:
            raise ModelError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Start it with `ollama serve`. ({e})") from e
        return r.json()["message"]["content"]
