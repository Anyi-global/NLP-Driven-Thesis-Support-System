"""Thin Gemini wrapper. This is the ONLY module that talks to the vendor SDK, so the
LLM can be swapped for a locally hosted model by re-implementing these two functions
(the thesis architecture calls for exactly this replaceability).

Prefers the current `google-genai` SDK; falls back to the legacy `google-generativeai`.
NOTE: model names change and old ones get shut down. Always set GEMINI_MODEL and
EMBEDDING_MODEL in .env to models listed on https://ai.google.dev/gemini-api/docs/deprecations
"""


class LLMUnavailable(RuntimeError):
    pass


def _client(settings):
    from google import genai
    from google.genai import types  # Needed for configuration structures
    
    timeout_seconds = int(settings.get("LLM_TIMEOUT_SECONDS", 300000))
    
    # Correct way to set client options in google-genai
    return genai.Client(
        api_key=settings["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=timeout_seconds)
    )


def generate_text(prompt, settings):
    """Return the model's text response (JSON requested). Raises LLMUnavailable if the model is gone."""
    try:
        try:
            # Force checking if google-genai is present
            from google import genai
            from google.genai import types
            
            client = _client(settings)
            try:
                # Configuration must be a GenerateContentConfig object, not a dict
                config = types.GenerateContentConfig(
                    response_mime_type="application/json", 
                    temperature=0.2
                )
                resp = client.models.generate_content(
                    model=settings["GEMINI_MODEL"], 
                    contents=prompt,
                    config=config
                )
                return resp.text or ""
            finally:
                client.close()
        except ImportError:
            # Legacy SDK fallback logic remains perfectly fine
            from importlib import import_module
            genai = import_module("google.generativeai")  
            genai.configure(api_key=settings["GEMINI_API_KEY"])
            model = genai.GenerativeModel(settings["GEMINI_MODEL"])
            resp = model.generate_content(
                prompt, generation_config={"response_mime_type": "application/json", "temperature": 0.2},
                request_options={"timeout": int(settings.get("LLM_TIMEOUT_SECONDS", 300000))})
            return resp.text or ""
    except ImportError as exc:
        raise LLMUnavailable("No Gemini SDK installed. Run: pip install google-genai") from exc
    except Exception as exc:
        msg = str(exc)
        if "404" in msg or "NOT_FOUND" in msg or "no longer available" in msg:
            raise LLMUnavailable("Model '%s' is not available. Update GEMINI_MODEL in .env." % settings["GEMINI_MODEL"]) from exc
        raise


def embed_texts(texts, settings, query=False):
    """Return a list of embedding vectors (floats). Requires google-genai."""
    from google import genai
    from google.genai import types
    
    client = _client(settings)
    try:
        # Configuration must be an EmbedContentConfig object, not a dict
        task = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
        config = types.EmbedContentConfig(task_type=task)
        
        resp = client.models.embed_content(
            model=settings["EMBEDDING_MODEL"], 
            contents=list(texts),
            config=config
        )
        return [list(e.values) for e in resp.embeddings]
    finally:
        client.close()
